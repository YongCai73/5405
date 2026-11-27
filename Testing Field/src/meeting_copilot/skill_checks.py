"""Deterministic checks for skill routing and code-enforced tool policy.

Used by `evaluate` (report sections `routing` and `skill_controls`) and by the test
suite. Everything here runs on synthetic fixtures with the local extractive provider:
it shows that the controls execute, not that a real model behaves well under them.
"""
import asyncio
import json
from importlib.resources import files
from pathlib import Path
from .assistant import Assistant
from .config import Config
from .model_tools import EvidenceTools
from .provider import Provider
from .replay import events
from .service import EvidenceService
from .skill_runtime import ToolDenied, load_contract, policy_for, route
from .store import Denied, Store
from .trace import Trace


def routing_cases():
    return json.loads(files('meeting_copilot').joinpath('routing_cases.json').read_text(encoding='utf-8'))['cases']


def routing_suite():
    cases = []
    for case in routing_cases():
        decision = route(case['question'])
        cases.append({'id': case['id'], 'expected': case['skill'], 'expected_status': case['status'],
                      'selected': decision['selected'], 'status': decision['status'], 'scores': decision['scores'],
                      'passed': decision['selected'] == case['skill'] and decision['status'] == case['status']})
    return {'cases': cases, 'total': len(cases), 'passed': sum(c['passed'] for c in cases),
            'note': ('Regression set labelled by the router author while writing the trigger phrases. It guards '
                     'against accidental changes; it is not an independent estimate of routing accuracy on real questions.')}


def skill_control_suite(root):
    """Illegal calls, budgets, trace correlation and recovery pinning through real objects."""
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    store = Store(root/'runtime'/'skills.db'); trace = Trace(root/'skill-trace.jsonl'); cases = []

    class Counting(Assistant):
        retrievals = 0
        async def retrieve(self, state):
            self.retrievals += 1
            return await super().retrieve(state)

    def record(case_id, check):
        try:
            detail = check() or {}
            cases.append({'id': case_id, 'passed': bool(detail.pop('passed')), **detail})
        except Exception as exc:
            cases.append({'id': case_id, 'passed': False, 'error_type': type(exc).__name__})

    def reason(call):
        try:
            call()
        except ToolDenied as exc:
            return exc.reason
        except Denied:
            return 'outside_authorized_snapshot'
        except ValueError:
            return 'invalid_arguments'
        return None

    def model_tools(skill, user='alice', snapshot=None):
        return EvidenceTools(store, user, snapshot or sid, policy=policy_for(load_contract(skill), 'model'), trace=trace)

    def turns(conversation):
        return store.db.execute('SELECT COUNT(*) FROM conversation_turns WHERE snapshot=? AND conversation=?', (sid, conversation)).fetchone()[0]

    try:
        store.create('alice', 'm', 'Skill control fixture')
        first = None
        for event in events():
            rid = store.ingest('alice', 'm', event); first = first or rid
        sid = store.snapshot('alice', 'm')
        assistant = Counting(store, 'alice', Provider(Config()), trace)

        def unknown_tool():
            result = model_tools('trace_decision').call('delete_meeting', '{}')
            return {'passed': result['status'] == 'denied' and result['reason'] == 'unknown_tool', 'reason': result.get('reason')}

        def outside_skill():
            tools = model_tools('catch_up'); result = tools.call('search_evidence', json.dumps({'query': 'SQLite'}))
            advertised = [s['function']['name'] for s in tools.schemas]
            return {'passed': result['status'] == 'denied' and result['reason'] == 'tool_not_allowed_for_skill' and advertised == ['read_evidence'] and not tools.sources,
                    'reason': result.get('reason'), 'advertised': advertised}

        def identity_argument():
            result = model_tools('trace_decision').call('search_evidence', json.dumps({'query': 'SQLite', 'user': 'bob', 'snapshot': 'forged'}))
            return {'passed': result['status'] == 'denied' and result['reason'] == 'invalid_arguments', 'reason': result.get('reason')}

        def call_budget():
            tools = model_tools('catch_up'); args = json.dumps({'source_id': first, 'start_line': 1, 'end_line': 1})
            statuses = [tools.call('read_evidence', args) for _ in range(3)]
            return {'passed': [s['status'] for s in statuses] == ['ok', 'ok', 'denied'] and statuses[2]['reason'] == 'tool_call_budget_exhausted',
                    'statuses': [s['status'] for s in statuses]}

        def mcp_outside_skill():
            service = EvidenceService(store, 'alice', sid, trace, policy_for(load_contract('catch_up'), 'mcp'))
            allowed = bool(service.call('get_transcript', {'offset': 0}))
            why = reason(lambda: service.call('search_project', {'query': 'SQLite'}))
            return {'passed': allowed and why == 'tool_not_allowed_for_skill', 'reason': why}

        def mcp_arguments():
            service = EvidenceService(store, 'alice', sid, trace)
            reasons = [reason(lambda: service.call('get_transcript', {'offset': -1})),
                       reason(lambda: service.call('read_evidence', {'source_id': first, 'principal': 'owner'})),
                       reason(lambda: service.call('run_shell', {'command': 'dir'}))]
            return {'passed': reasons == ['invalid_arguments', 'invalid_arguments', 'unknown_tool'], 'reasons': reasons}

        def revoked():
            store.grant('alice', 'm', 'viewer'); vsid = store.snapshot('viewer', 'm')
            tools = model_tools('trace_decision', 'viewer', vsid)
            service = EvidenceService(store, 'viewer', vsid, trace)
            store.grant('alice', 'm', 'viewer', False)
            reasons = [reason(lambda: tools.call('search_evidence', json.dumps({'query': 'SQLite'}))),
                       reason(lambda: service.call('get_transcript', {}))]
            return {'passed': reasons == ['outside_authorized_snapshot']*2, 'reasons': reasons}

        def denials_traced():
            rows = [r for r in trace.rows() if r['event'] == 'tool_call']
            denied = {r.get('reason') for r in rows if r.get('policy') == 'deny'}
            leaked = any(word in json.dumps(rows) for word in ('SQLite', 'bob', 'forged', 'delete_meeting', 'run_shell'))
            expected = {'unknown_tool', 'tool_not_allowed_for_skill', 'invalid_arguments', 'tool_call_budget_exhausted'}
            return {'passed': expected <= denied and not leaked and all(r.get('tool_surface') in ('model', 'mcp') for r in rows),
                    'denied_reasons': sorted(x for x in denied if x), 'argument_text_in_trace': leaked}

        def correlated():
            result = asyncio.run(assistant.ask(sid, 'Why was Kafka rejected?', 'ctl-auto', 'auto'))
            rows = trace.rows(task_id=result['task_id']); names = [r['event'] for r in rows]
            same = all(r.get('skill') == 'trace_decision' and r.get('skill_version') == result['skill_version'] and r.get('snapshot_id') == sid for r in rows)
            return {'passed': result['skill'] == 'trace_decision' and result['routing']['status'] == 'matched' and not result['abstained']
                    and names[0] == 'skill_route' and {'retrieval', 'answer'} <= set(names) and same, 'events': names}

        def no_evidence():
            result = asyncio.run(assistant.ask(sid, 'Why is the cafeteria sandwich so expensive?', 'ctl-none', 'auto'))
            return {'passed': result['abstained'] and result['abstain_reason'] == 'no_evidence_retrieved' and result['skill'] == 'trace_decision',
                    'abstain_reason': result['abstain_reason']}

        def checkpoint(conversation, question, skill, **extra):
            evidence = asyncio.run(assistant.retrieve({'snapshot': sid, 'question': question, 'skill': skill}))['evidence']
            store.checkpoint_put('alice', sid, conversation, {'version': 1, 'snapshot': sid, 'question': question, 'skill': skill,
                'step': 'retrieved', 'source_ids': [e['id'] for e in evidence], 'evidence': evidence, **extra})

        def resume_pinned():
            # The router now sends this question to `direct`; the checkpoint pinned trace_decision.
            question = 'Who owns security policy?'; contract = load_contract('trace_decision')
            pinned = {**route(question, 'trace_decision'), 'requested': 'auto', 'mode': 'auto', 'status': 'matched'}
            from .skill_runtime import contract_digest
            checkpoint('ctl-pin', question, 'trace_decision', routing=pinned, task_id='pinned-task',
                       skill_version=contract['version'], contract_sha256=contract_digest(contract))
            before = assistant.retrievals
            result = asyncio.run(assistant.ask(sid, question, 'ctl-pin', 'auto'))
            return {'passed': route(question)['selected'] == 'direct' and result['skill'] == 'trace_decision' and result['task_id'] == 'pinned-task'
                    and assistant.retrievals == before and turns('ctl-pin') == 1, 'resumed_skill': result['skill']}

        def contract_changed():
            question = 'Why does Atlas choose SQLite?'
            checkpoint('ctl-version', question, 'trace_decision', routing=route(question), task_id='version-task',
                       skill_version='0.9.0', contract_sha256='0'*16)
            before = assistant.retrievals
            result = asyncio.run(assistant.ask(sid, question, 'ctl-version', 'auto'))
            names = [r['event'] for r in trace.rows(task_id='version-task')]
            return {'passed': 'skill_contract_changed' in names and assistant.retrievals == before+1 and result['skill'] == 'trace_decision'
                    and result['skill_version'] == load_contract('trace_decision')['version'] and turns('ctl-version') == 1, 'events': names}

        def conflict():
            question = 'What is the latency target?'
            original = asyncio.run(assistant.ask(sid, question, 'ctl-conflict', 'trace_decision'))
            again = asyncio.run(assistant.ask(sid, question, 'ctl-conflict', 'catch_up'))
            same = asyncio.run(assistant.ask(sid, question, 'ctl-conflict', 'trace_decision'))
            return {'passed': again.get('skill_conflict', {}).get('answered_with') == 'trace_decision' and again['skill'] == 'trace_decision'
                    and again['text'] == original['text'] and 'skill_conflict' not in same and turns('ctl-conflict') == 1,
                    'conflict': again.get('skill_conflict', {}).get('answered_with')}

        record('model_unknown_tool_denied', unknown_tool)
        record('model_tool_outside_skill_denied', outside_skill)
        record('model_identity_argument_denied', identity_argument)
        record('model_tool_call_budget_enforced', call_budget)
        record('mcp_tool_outside_skill_denied', mcp_outside_skill)
        record('mcp_invalid_arguments_and_unknown_tool_denied', mcp_arguments)
        record('revoked_scope_denied_on_both_surfaces', revoked)
        record('denials_traced_without_argument_text', denials_traced)
        record('auto_route_events_share_task_id', correlated)
        record('misrouted_question_without_evidence_abstains', no_evidence)
        record('resume_keeps_pinned_skill_and_task', resume_pinned)
        record('contract_change_restarts_retrieval_same_skill', contract_changed)
        record('same_question_other_skill_reports_conflict', conflict)
    finally:
        store.close()
    return {'cases': cases, 'total': len(cases), 'passed': sum(c['passed'] for c in cases),
            'note': ('Executed against the real Store, EvidenceService, EvidenceTools and Assistant with the local extractive '
                     'provider. The MCP cases call the gateway the stdio server uses, not a stdio session; '
                     'tests/test_mcp.py covers the stdio transport.')}
