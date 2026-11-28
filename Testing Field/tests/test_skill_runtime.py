"""Skill routing, contracts, code-enforced tool policy, trace correlation and recovery pinning."""
import asyncio
import json
import os
import subprocess
import sys
import pytest
from meeting_copilot import assistant as assistant_module
from meeting_copilot.assistant import Assistant
from meeting_copilot.config import Config
from meeting_copilot.provider import Completion, Provider, ProviderError
from meeting_copilot.skill_checks import routing_cases, routing_suite, skill_control_suite
from meeting_copilot.skill_runtime import (MANUAL, SKILLS, TOOLS, ToolArgumentError, ToolDenied, check_contract,
                                           load_contract, policy_for, route, validate_args)
from meeting_copilot.trace import Trace

QUOTE = 'SQLite was chosen for simplicity.'


@pytest.mark.parametrize('case', routing_cases(), ids=lambda c: c['id'])
def test_auto_routing_regression_case(case):
    decision = route(case['question'])
    assert (decision['selected'], decision['status']) == (case['skill'], case['status']), decision


def test_routing_is_explainable_and_manual_choice_is_kept():
    auto = route('Why did we choose SQLite versus Kafka?')
    assert auto['mode'] == 'auto' and {k: v for k, v in auto['scores'].items() if v} == {'trace_decision': 4, 'compare_options': 3}
    assert auto['matched'] == {'trace_decision': ['choose', 'why'], 'compare_options': ['versus']}
    manual = route('Why did we choose SQLite versus Kafka?', 'compare_options')
    assert manual['mode'] == 'manual' and manual['selected'] == 'compare_options' and manual['requested'] == 'compare_options'
    tie = route('Summarize why.')
    assert tie['status'] == 'ambiguous' and tie['selected'] == 'direct' and tie['candidates'] == ['catch_up', 'trace_decision']
    with pytest.raises(ValueError, match='unknown skill'):
        route('q', 'shell')
    assert routing_suite()['passed'] == len(routing_cases())


def test_contracts_are_versioned_and_reject_tools_outside_the_registry():
    for name in MANUAL:
        contract = load_contract(name)
        assert contract['version'] in ('1.0.0', '1.1.0')
        assert set(contract['allowed_tools']['mcp']) <= set(TOOLS['mcp'])
        assert set(contract['allowed_tools']['model']) <= set(TOOLS['model'])
    assert load_contract('catch_up')['allowed_tools']['model'] == ['read_evidence']
    forged = load_contract('catch_up'); forged['allowed_tools']['mcp'].append('write_file')
    with pytest.raises(ValueError, match='allowed_tools'):
        check_contract(forged, 'catch_up')
    unversioned = load_contract('catch_up'); del unversioned['version']
    with pytest.raises(ValueError, match='version'):
        check_contract(unversioned, 'catch_up')
    # No registered tool on either surface accepts identity, scope or a filesystem path.
    forbidden = {'user', 'principal', 'snapshot', 'root', 'path', 'command', 'folder'}
    assert not any(forbidden & set(spec) for surface in TOOLS.values() for spec in surface.values())


def test_shared_argument_validation():
    assert validate_args('mcp', 'get_transcript', {}) == {}
    assert validate_args('model', 'search_evidence', {'query': 'SQLite'}) == {'query': 'SQLite'}
    for surface, name, args in [('mcp', 'get_transcript', {'offset': True}), ('mcp', 'get_transcript', {'offset': 10**7}),
                                ('mcp', 'find_in_transcripts', {}), ('mcp', 'find_in_transcripts', {'query': ''}),
                                ('model', 'read_evidence', {'source_id': 1, 'start_line': 1}),
                                ('model', 'search_evidence', {'query': 'x', 'snapshot': 'other'}), ('model', 'search_evidence', ['x'])]:
        with pytest.raises(ToolArgumentError):
            validate_args(surface, name, args)
    with pytest.raises(ToolDenied):
        validate_args('model', 'get_transcript', {})  # an MCP name is not a model tool


def test_skill_control_suite_passes(tmp_path):
    report = skill_control_suite(tmp_path/'controls')
    assert [c for c in report['cases'] if not c['passed']] == []
    assert report['total'] == 17


class ScriptedProvider(Provider):
    """Replaces only the HTTP request; the real tool loop in Provider.complete still runs."""
    def __init__(self, replies, **config):
        super().__init__(Config(provider='openai', model='scripted', send_evidence=True, **config))
        self.replies = list(replies); self.requests = []

    async def chat(self, messages, schemas=()):
        self.requests.append({'messages': json.loads(json.dumps(messages)), 'tools': [s['function']['name'] for s in schemas]})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def tool_call(name, arguments, call_id='call-1'):
    return Completion('', 5, 3, tool_calls=[{'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}])


def final(source_id):
    return Completion(json.dumps({'claims': [{'source_id': source_id, 'quote': QUOTE}]}), 7, 4)


@pytest.fixture
def sid(store, event):
    store.ingest('alice', 'm', {**event, 'final': True})
    return store.snapshot('alice', 'm')


async def test_model_tool_outside_skill_is_denied_in_code_and_traced(store, sid, tmp_path):
    rid = store.evidence('alice', sid)[0]['id']
    provider = ScriptedProvider([tool_call('search_evidence', {'query': 'SQLite'}), final(rid)], tool_rounds=2)
    trace = Trace(tmp_path/'trace.jsonl')
    result = await Assistant(store, 'alice', provider, trace).ask(sid, 'Catch me up', 'c', 'auto')
    assert result['skill'] == 'catch_up' and result['routing']['status'] == 'matched' and not result['abstained']
    assert result['tool_events'] == [{'name': 'search_evidence', 'status': 'denied', 'reason': 'tool_not_allowed_for_skill'}]
    assert result['validation_report']['tool_calls_denied'] == 1
    # catch_up advertises only read_evidence and allows one tool round although the host allows two.
    assert [r['tools'] for r in provider.requests] == [['read_evidence'], []]
    rows = trace.rows(task_id=result['task_id'])
    denied = [r for r in rows if r['event'] == 'tool_call']
    assert len(denied) == 1 and denied[0]['policy'] == 'deny' and denied[0]['reason'] == 'tool_not_allowed_for_skill'
    assert denied[0]['tool_surface'] == 'model' and denied[0]['skill'] == 'catch_up' and denied[0]['snapshot_id'] == sid
    assert [r['event'] for r in rows] == ['skill_route', 'retrieval', 'tool_call', 'answer']
    assert all('latency_ms' in r for r in rows)
    assert 'SQLite' not in (tmp_path/'trace.jsonl').read_text(encoding='utf-8')


async def test_allowed_model_tool_call_adds_evidence_and_is_traced(store, sid, tmp_path):
    rid = store.evidence('alice', sid)[0]['id']
    provider = ScriptedProvider([tool_call('search_evidence', {'query': 'SQLite simplicity'}), final(rid)], tool_rounds=1)
    trace = Trace(tmp_path/'trace.jsonl')
    result = await Assistant(store, 'alice', provider, trace).ask(sid, 'Why SQLite?', 'c', 'auto')
    assert result['skill'] == 'trace_decision' and result['claims'] == [{'source_id': rid, 'quote': QUOTE}]
    call = [r for r in trace.rows(task_id=result['task_id']) if r['event'] == 'tool_call'][0]
    assert call['policy'] == 'allow' and call['tool'] == 'search_evidence' and call['source_ids'] == [rid]


async def test_no_evidence_abstains_without_a_provider_call(store, sid, tmp_path):
    # The strict contract (general_answers=False), which the API evaluation also uses.
    provider = ScriptedProvider([], general_answers=False)
    result = await Assistant(store, 'alice', provider, Trace(tmp_path/'trace.jsonl')).ask(sid, 'Compare the cafeteria sandwich options', 'c', 'auto')
    assert result['skill'] == 'compare_options' and result['abstained'] and result['abstain_reason'] == 'no_evidence_retrieved'
    assert provider.requests == [] and result['usage'] == {'input_tokens': 0, 'output_tokens': 0, 'estimated_cost_usd': 0.0}
    assert 'provider not called' in result['mode']


async def test_invalid_model_output_is_rejected_and_counted(store, sid, tmp_path):
    rid = store.evidence('alice', sid)[0]['id']
    content = json.dumps({'claims': [{'source_id': rid, 'quote': QUOTE}, {'source_id': rid, 'quote': 'invented quotation'}],
                          'inferences': [{'text': 'ok', 'source_ids': [rid], 'field': 'decision_evidence'},
                                         {'text': 'wrong field', 'source_ids': [rid], 'field': 'option_evidence'}]})
    provider = ScriptedProvider([Completion(content, 1, 1)])
    result = await Assistant(store, 'alice', provider, Trace(tmp_path/'trace.jsonl')).ask(sid, 'Why SQLite?', 'c', 'trace_decision')
    assert len(result['claims']) == 1 and [i['field'] for i in result['inferences']] == ['decision_evidence']
    assert result['validation_report']['claims_rejected'] == 1 and result['validation_report']['inferences_rejected'] == 1
    assert set(result['fields']) == set(result['contract']['output_fields'])


async def test_timeout_is_traced_and_resume_keeps_skill_and_task(store, sid, tmp_path):
    rid = store.evidence('alice', sid)[0]['id']
    provider = ScriptedProvider([ProviderError('timeout'), final(rid)])
    trace = Trace(tmp_path/'trace.jsonl'); assistant = Assistant(store, 'alice', provider, trace)
    with pytest.raises(ProviderError, match='timeout'):
        await assistant.ask(sid, 'Why SQLite?', 'c', 'auto')
    saved = store.checkpoint_get('alice', sid, 'c')
    assert saved['step'] == 'retrieved' and saved['skill'] == 'trace_decision' and saved['routing']['requested'] == 'auto'
    failed = [r for r in trace.rows(task_id=saved['task_id']) if r['event'] == 'ask_failed']
    assert failed and failed[0]['error'] == 'timeout' and failed[0]['skill'] == 'trace_decision'
    result = await assistant.ask(sid, 'Why SQLite?', 'c', 'auto')
    assert result['task_id'] == saved['task_id'] and result['skill'] == 'trace_decision' and not result['abstained']
    events = [r['event'] for r in trace.rows(task_id=saved['task_id'])]
    assert events.count('retrieval') == 1 and 'ask_resumed' in events and events[-1] == 'answer'


async def test_stored_answer_keeps_its_skill_version_when_the_contract_changes(store, sid, tmp_path, monkeypatch):
    assistant = Assistant(store, 'alice', Provider(Config()), Trace(tmp_path/'trace.jsonl'))
    put = store.private_put; failures = [RuntimeError('crash before the turn is saved')]
    def fail_once(*args):
        if failures:
            raise failures.pop()
        return put(*args)
    monkeypatch.setattr(store, 'private_put', fail_once)
    with pytest.raises(RuntimeError):
        await assistant.ask(sid, 'Why SQLite?', 'c', 'auto')
    answered = store.checkpoint_get('alice', sid, 'c')
    assert answered['step'] == 'answered'
    monkeypatch.setattr(assistant_module, 'contract_digest', lambda contract: 'f'*16)  # the contract was edited afterwards
    result = await assistant.ask(sid, 'Why SQLite?', 'c', 'auto')
    assert result['contract_sha256'] == answered['result']['contract_sha256'] != 'f'*16
    assert result == answered['result']
    assert store.checkpoint_get('alice', sid, 'c')['contract_sha256'] == answered['result']['contract_sha256']


async def test_legacy_checkpoint_without_routing_still_resumes(store, sid, assistant):
    evidence = (await assistant.retrieve({'snapshot': sid, 'question': 'Why SQLite?', 'skill': 'trace_decision'}))['evidence']
    store.checkpoint_put('alice', sid, 'c', {'snapshot': sid, 'question': 'Why SQLite?', 'skill': 'trace_decision', 'step': 'retrieved',
                                             'source_ids': [e['id'] for e in evidence], 'evidence': evidence})
    assert assistant.resume_point(sid, 'Why SQLite?', 'c', 'trace_decision')
    assert assistant.resume_point(sid, 'Why SQLite?', 'c', 'auto')          # auto resolves to the pinned skill
    assert assistant.resume_point(sid, 'Why SQLite?', 'c', 'catch_up') is None
    result = await assistant.ask(sid, 'Why SQLite?', 'c', 'trace_decision')
    assert result['skill'] == 'trace_decision' and result['routing']['mode'] == 'manual' and not result['abstained']


def test_cli_auto_route_citation_and_trace_show(tmp_path):
    config = tmp_path/'host.json'; config.write_text(json.dumps({'principal': 'local-host', 'data_dir': 'runtime'}))
    app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    def cli(*args):
        done = subprocess.run([sys.executable, '-m', 'meeting_copilot', '--config', str(config), *args], capture_output=True, text=True,
                              encoding='utf-8', cwd=app, env={**os.environ, 'PYTHONUTF8': '1'}, timeout=120)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout), done.stderr
    assert cli('route', 'Why was Kafka rejected?')[0]['selected'] == 'trace_decision'
    assert {c['name'] for c in cli('skills')[0]} == set(MANUAL)
    cli('create', 'design'); cli('replay', 'design', os.path.join(app, 'examples', 'replay.jsonl'))
    answer, stderr = cli('ask', 'design', 'Why was Kafka rejected?', '--conversation', 'notes')
    assert answer['skill'] == 'trace_decision' and answer['routing']['mode'] == 'auto' and answer['citations'] and '[E' in answer['text']
    note = [json.loads(line) for line in stderr.splitlines() if '"skill_selected"' in line][0]
    assert note['task_id'] == answer['task_id'] and note['route'] == 'matched'
    events, _ = cli('trace-show', '--task', answer['task_id'])
    assert [e['event'] for e in events] == ['skill_route', 'retrieval', 'answer']
    assert {e['task_id'] for e in events} == {answer['task_id']} and {e['skill'] for e in events} == {'trace_decision'}
    manual, _ = cli('ask', 'design', 'Why was Kafka rejected?', '--conversation', 'manual', '--skill', 'compare_options', '--snapshot', answer['snapshot_id'])
    assert manual['skill'] == 'compare_options' and manual['routing']['mode'] == 'manual'
    again, stderr = cli('ask', 'design', 'Why was Kafka rejected?', '--conversation', 'notes', '--skill', 'catch_up', '--snapshot', answer['snapshot_id'])
    assert again['skill_conflict']['answered_with'] == 'trace_decision' and '"skill_conflict"' in stderr


def open_reply(rid=None, general='Sandwich choices usually trade cost against freshness.'):
    """A scripted model reply that answers beyond the record."""
    claims = [{'source_id': rid, 'quote': 'SQLite was chosen for simplicity.'}] if rid else []
    inferences = [{'text': 'The team chose SQLite because it is simple.', 'source_ids': [rid], 'field': 'option_evidence'}] if rid else []
    return Completion(json.dumps({'claims': claims, 'inferences': inferences, 'general_answer': general}), 11, 7)


async def test_question_without_meeting_evidence_gets_a_labelled_model_answer(store, sid, tmp_path):
    from meeting_copilot.skill_runtime import GENERAL_ANSWER_LABEL, NO_DIRECT_EVIDENCE

    # A citation marker inside the model's own answer would pass it off as a quotation; the host removes it.
    provider = ScriptedProvider([open_reply(general='Sandwich choices usually trade cost against freshness. [E1]')])
    result = await Assistant(store, 'alice', provider, Trace(tmp_path/'trace.jsonl')).ask(sid, 'Compare the cafeteria sandwich options', 'c', 'auto')
    # The model was asked even though nothing was retrieved, and was told how to answer beyond the record.
    assert len(provider.requests) == 1 and 'general_answer' in provider.requests[0]['messages'][0]['content']
    assert result['text'] == (NO_DIRECT_EVIDENCE + '\n\n' + GENERAL_ANSWER_LABEL
                              + ':\nSandwich choices usually trade cost against freshness.')
    assert result['general_answer']['text'] == 'Sandwich choices usually trade cost against freshness.'
    assert result['grounding'] == 'model_only' and result['mode'] == 'API / model knowledge only: no meeting evidence cited'
    # Nothing here is grounded in the meeting, and the answer says so in its own fields.
    assert result['claims'] == [] and result['citations'] == [] and result['inferences'] == []
    assert result['abstained'] and result['abstain_reason'] == 'no_evidence_retrieved'
    assert result['usage']['input_tokens'] == 11


async def test_meeting_evidence_and_model_knowledge_are_kept_apart(store, sid, tmp_path):
    from meeting_copilot.skill_runtime import GENERAL_ANSWER_LABEL

    rid = store.evidence('alice', sid)[0]['id']
    provider = ScriptedProvider([open_reply(rid, 'Embedded databases in general avoid a separate server process.')])
    result = await Assistant(store, 'alice', provider, Trace(tmp_path/'trace.jsonl')).ask(sid, 'Compare SQLite with other options', 'c', 'auto')
    assert result['grounding'] == 'meeting_and_model' and not result['abstained'] and result['abstain_reason'] is None
    assert [c['source_id'] for c in result['citations']] == [rid] and len(result['inferences']) == 1
    quoted, beyond = result['text'].split('\n\n' + GENERAL_ANSWER_LABEL + ':\n')
    assert 'SQLite was chosen for simplicity.' in quoted and f'[E{rid}]' in quoted
    assert beyond == 'Embedded databases in general avoid a separate server process.' and '[E' not in beyond
    assert 'API / model synthesis' in result['mode']

    # A fabricated quotation is still rejected; only the labelled model answer remains.
    forged = Completion(json.dumps({'claims': [{'source_id': rid, 'quote': 'Kafka was chosen for scale.'}],
                                    'inferences': [{'text': 'The team chose Kafka.', 'source_ids': [rid], 'field': 'option_evidence'}],
                                    'general_answer': 'Kafka is a distributed log.'}), 1, 1)
    provider = ScriptedProvider([forged])
    result = await Assistant(store, 'alice', provider, Trace(tmp_path/'trace2.jsonl')).ask(sid, 'Compare SQLite with Kafka options', 'd', 'auto')
    assert result['claims'] == [] and result['inferences'] == [] and result['abstained']
    assert result['validation_report']['claims_rejected'] == 1 and result['grounding'] == 'model_only'
    assert 'The team chose Kafka' not in result['text'] and result['text'].endswith('Kafka is a distributed log.')


async def test_local_mode_and_a_reply_without_general_answer_keep_the_abstention_text(store, sid, tmp_path):
    local = await Assistant(store, 'alice', Provider(Config()), Trace(tmp_path/'local.jsonl')).ask(sid, 'Compare the cafeteria sandwich options', 'c', 'auto')
    assert local['text'].startswith('Insufficient evidence in this authorized snapshot.') and local['grounding'] == 'none'
    assert 'general_answer' not in local and local['abstained']
    provider = ScriptedProvider([Completion(json.dumps({'claims': [], 'inferences': [], 'general_answer': None}), 1, 1)])
    silent = await Assistant(store, 'alice', provider, Trace(tmp_path/'silent.jsonl')).ask(sid, 'Compare the cafeteria sandwich options', 'e', 'auto')
    assert silent['text'] == 'Insufficient evidence in this authorized snapshot.' and silent['grounding'] == 'none'


def test_general_answers_setting_is_a_validated_boolean(tmp_path):
    assert Config().general_answers is True
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'general_answers': False}), encoding='utf-8')
    assert Config.load(path).general_answers is False
    path.write_text(json.dumps({'general_answers': 'yes'}), encoding='utf-8')
    with pytest.raises(ValueError, match='general_answers'):
        Config.load(path)


async def test_the_host_tells_the_model_which_model_it_is(store, sid, tmp_path):
    """Asked through the app, a real model named the wrong maker; the host states what it actually called."""
    provider = ScriptedProvider([open_reply(general='I am the Meeting Copilot assistant, running on the model scripted.')],
                                base_url='https://api.example.invalid/v1')
    await Assistant(store, 'alice', provider, Trace(tmp_path/'trace.jsonl')).ask(sid, 'What model are you?', 'c', 'auto')
    system = provider.requests[0]['messages'][0]['content']
    assert system.startswith('Identity: you are the Meeting Copilot assistant, running on the model "scripted" served from api.example.invalid.')
    assert 'claim no other' in system
