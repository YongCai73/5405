"""Skill contracts, deterministic routing and code-enforced tool policy.

One place defines (a) what a skill is allowed to do, (b) how a question is
routed to a skill, and (c) the argument and permission checks shared by the two
tool surfaces: the stdio MCP server (`mcp_server.py` -> `EvidenceService.call`)
and the in-process model tool loop (`model_tools.EvidenceTools.call`).

Identity, snapshot and folder scope are never tool arguments. They are bound by
the host when the surface object is constructed; any attempt to pass them is an
unknown argument and is rejected here before a store read happens.
"""
import hashlib
import json
import re
import time
from importlib.resources import files
from .store import Denied

SKILLS = ('catch_up', 'trace_decision', 'compare_options')
MANUAL = SKILLS + ('direct',)
AUTO = 'auto'
ROUTER_VERSION = '1.0.0'
_SEMVER = re.compile(r'^\d+\.\d+\.\d+$')
_ANY_ID = ('int', -2**63, 2**63-1)
_LINE = ('int', 1, 10_000_000)
_QUERY = ('str', 1, 1000)

# surface -> tool -> argument -> ((type, low, high), required)
TOOLS = {
    'mcp': {
        'meet2notes_status': {},
        'list_meetings': {},
        'get_meeting': {'meeting_id': (('str', 1, 200), True)},
        'get_transcript': {'offset': (('int', 0, 1_000_000), False)},
        'find_in_transcripts': {'query': (_QUERY, True)},
        'get_summary': {},
        'read_evidence': {'source_id': (_ANY_ID, True)},
        'list_project_files': {'offset': (('int', 0, 1000), False)},
        'search_project': {'query': (_QUERY, True)},
        'read_project_file': {'source_id': (_ANY_ID, True), 'start_line': (_LINE, False), 'end_line': (_LINE, False)},
    },
    'model': {
        'search_evidence': {'query': (_QUERY, True)},
        'read_evidence': {'source_id': (_ANY_ID, True), 'start_line': (_LINE, True), 'end_line': (_LINE, True)},
    },
}

DIRECT = {
    'name': 'direct', 'version': '1.1.0', 'trigger': [],
    'routing': {'phrases': {}},
    'retrieval': 'question keywords',
    'retrieval_plan': {'strategy': 'keyword', 'limit': 16},
    'allowed_tools': {'model': sorted(TOOLS['model']), 'mcp': sorted(TOOLS['mcp'])},
    'output_fields': ['evidence'],
    'abstention': 'insufficient evidence',
    'termination': {'max_tool_rounds': 2, 'max_tool_calls': 4, 'max_claims': 8, 'max_inferences': 8,
                    'mcp_session_calls': 1000,
                    'on_limit': 'further tool calls are denied; answer from evidence already retrieved or abstain'},
}


class ToolDenied(Denied):
    """A policy refusal. `reason` is a fixed code that is safe to trace."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class ToolArgumentError(ValueError):
    reason = 'invalid_arguments'


def validate_args(surface, name, args):
    """Shared argument validation for both tool surfaces; returns checked arguments."""
    spec = TOOLS[surface].get(name) if isinstance(name, str) else None
    if spec is None:
        raise ToolDenied('unknown_tool')
    if not isinstance(args, dict) or set(args) - set(spec):
        # Includes user/principal/snapshot/root/path: scope is host-bound, never an argument.
        raise ToolArgumentError('unknown or malformed arguments')
    for key, ((kind, low, high), required) in spec.items():
        if key not in args:
            if required:
                raise ToolArgumentError('missing argument')
            continue
        value = args[key]
        if kind == 'int' and (type(value) is not int or not low <= value <= high):
            raise ToolArgumentError('integer argument out of range')
        if kind == 'str' and (not isinstance(value, str) or not low <= len(value) <= high):
            raise ToolArgumentError('string argument out of range')
    return dict(args)


class ToolPolicy:
    """Per-task (model) or per-session (MCP) allowlist and call budget for one skill."""
    def __init__(self, surface, skill, version, allowed, max_calls, max_rounds=0):
        self.surface = surface; self.skill = skill; self.version = version
        self.allowed = frozenset(allowed); self.max_calls = max_calls; self.max_rounds = max_rounds
        self.calls = 0

    def admit(self, name, args):
        if not isinstance(name, str) or name not in TOOLS[self.surface]:
            raise ToolDenied('unknown_tool')
        if name not in self.allowed:
            raise ToolDenied('tool_not_allowed_for_skill')
        if self.calls >= self.max_calls:
            raise ToolDenied('tool_call_budget_exhausted')
        self.calls += 1  # malformed attempts also consume budget, so a loop terminates
        return validate_args(self.surface, name, args)


def policy_for(contract, surface, configured_rounds=None):
    limits = contract['termination']
    if surface == 'mcp':
        return ToolPolicy('mcp', contract['name'], contract['version'], contract['allowed_tools']['mcp'], limits['mcp_session_calls'])
    rounds = limits['max_tool_rounds'] if configured_rounds is None else min(configured_rounds, limits['max_tool_rounds'])
    return ToolPolicy('model', contract['name'], contract['version'], contract['allowed_tools']['model'], limits['max_tool_calls'], rounds)


def _source_ids(result):
    rows = result if isinstance(result, list) else [result]
    ids = []
    for row in rows:
        if isinstance(row, dict):
            value = row.get('source_id', row.get('id'))
            if type(value) is int:
                ids.append(value)
    return ids[:30]


def guarded_call(policy, trace, snapshot, name, args, execute, decode=None):
    """The single enforcement and audit point for a tool call on either surface.

    Order: allowlist -> budget -> argument validation -> host-scoped execution.
    The trace row holds the registered tool name, the decision and a fixed reason
    code; never the arguments, query text, or returned evidence.
    """
    started = time.perf_counter()

    def emit(decision, reason=None, ids=()):
        if trace:
            known = isinstance(name, str) and name in TOOLS[policy.surface]
            trace.emit('tool_call', snapshot_id=snapshot, tool=name if known else 'unknown',
                       tool_surface=policy.surface, policy=decision, reason=reason, source_ids=list(ids),
                       skill=policy.skill, skill_version=policy.version,
                       latency_ms=round((time.perf_counter()-started)*1000, 3))
    try:
        if decode:
            args = decode(args)
        result = execute(policy.admit(name, args))
    except ToolDenied as e:
        emit('deny', e.reason); raise
    except Denied:
        emit('deny', 'outside_authorized_snapshot'); raise
    except (ValueError, TypeError, KeyError):
        emit('deny', 'invalid_arguments'); raise
    except Exception:
        emit('error', 'tool_failed'); raise
    emit('allow', ids=_source_ids(result))
    return result


def check_contract(contract, name):
    def need(ok, what):
        if not ok:
            raise ValueError('invalid skill contract: '+what)
    need(isinstance(contract, dict) and contract.get('name') == name, 'name')
    need(isinstance(contract.get('version'), str) and _SEMVER.match(contract['version']), 'version')
    phrases = contract.get('routing', {}).get('phrases') if isinstance(contract.get('routing'), dict) else None
    need(isinstance(phrases, dict) and all(isinstance(k, str) and k == k.lower().strip() and k and type(v) is int and 1 <= v <= 5 for k, v in phrases.items()), 'routing.phrases')
    plan = contract.get('retrieval_plan')
    need(isinstance(plan, dict) and plan.get('strategy') in ('keyword', 'recent_segments') and type(plan.get('limit')) is int and 1 <= plan['limit'] <= 16, 'retrieval_plan')
    tools = contract.get('allowed_tools')
    need(isinstance(tools, dict) and set(tools) == set(TOOLS) and all(isinstance(tools[s], list) and set(tools[s]) <= set(TOOLS[s]) for s in TOOLS), 'allowed_tools')
    fields = contract.get('output_fields')
    need(isinstance(fields, list) and fields and all(isinstance(f, str) and f for f in fields), 'output_fields')
    need(isinstance(contract.get('abstention'), str) and contract['abstention'], 'abstention')
    end = contract.get('termination')
    bounds = {'max_tool_rounds': (0, 2), 'max_tool_calls': (0, 8), 'max_claims': (1, 8), 'max_inferences': (0, 8), 'mcp_session_calls': (1, 10000)}
    need(isinstance(end, dict) and all(type(end.get(k)) is int and lo <= end[k] <= hi for k, (lo, hi) in bounds.items()), 'termination')
    return contract


def load_contract(name):
    if name == 'direct':
        return json.loads(json.dumps(DIRECT))
    if name not in SKILLS:
        raise ValueError('unknown skill')
    return check_contract(json.loads(files('meeting_copilot').joinpath('skills', name+'.json').read_text(encoding='utf-8')), name)


def contract_digest(contract):
    """Detects a contract edit that forgot to bump `version`."""
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()[:16]


def prompt_view(contract):
    """The task-relevant part of a contract sent to the model; limits stay host-side."""
    return {k: contract[k] for k in ('name', 'version', 'retrieval', 'output_fields', 'citation', 'abstention', 'task_steps') if k in contract}


def check_output(result, contract):
    """Host-side output contract; raises rather than saving a malformed answer."""
    end = contract['termination']
    if set(result.get('fields', {})) != set(contract['output_fields']):
        raise ValueError('skill output contract violation: fields')
    if len(result['claims']) > end['max_claims'] or len(result['inferences']) > end['max_inferences']:
        raise ValueError('skill output contract violation: limits')
    cited = {c['source_id'] for c in result['claims']}
    if any(not set(i['source_ids']) <= cited or i['field'] not in contract['output_fields'] for i in result['inferences']):
        raise ValueError('skill output contract violation: inference references')
    if result['abstained'] != (not result['claims']):
        raise ValueError('skill output contract violation: abstention')


def _matches(question, phrase):
    pattern = r'(?<![a-z0-9])'+r'\s+'.join(re.escape(p) for p in phrase.split())+r'(?![a-z0-9])'
    return re.search(pattern, question) is not None


def route(question, requested=AUTO):
    """Explainable routing: weighted phrase matches declared in each skill contract.

    manual          the user named a skill; it is used as given
    matched         exactly one skill has the highest positive score
    ambiguous       two or more skills tie for the highest score -> `direct`
    not_applicable  no trigger phrase matched -> `direct`

    `direct` is plain keyword retrieval with one generic output field, so the
    fallback never applies a skill-specific framing the question did not ask for.
    """
    base = {'requested': requested, 'router_version': ROUTER_VERSION}
    if requested != AUTO:
        contract = load_contract(requested)
        return {**base, 'mode': 'manual', 'status': 'manual', 'selected': requested, 'skill_version': contract['version'],
                'candidates': [requested], 'scores': {}, 'matched': {}, 'reason': 'skill chosen by the user'}
    text = question.lower(); scores = {}; matched = {}
    for skill in SKILLS:
        phrases = load_contract(skill)['routing']['phrases']
        hits = sorted(p for p in phrases if _matches(text, p))
        matched[skill] = hits; scores[skill] = sum(phrases[p] for p in hits)
    top = max(scores.values())
    leaders = [s for s in SKILLS if scores[s] == top]
    if top == 0:
        status, selected, candidates = 'not_applicable', 'direct', []
        reason = 'no skill trigger matched; generic keyword retrieval is used'
    elif len(leaders) > 1:
        status, selected, candidates = 'ambiguous', 'direct', leaders
        reason = 'skills tie; generic keyword retrieval is used. Choose one of the candidates manually to apply it'
    else:
        status, selected, candidates = 'matched', leaders[0], leaders
        reason = 'highest trigger score'
    return {**base, 'mode': 'auto', 'status': status, 'selected': selected,
            'skill_version': load_contract(selected)['version'], 'candidates': candidates,
            'scores': scores, 'matched': {k: v for k, v in matched.items() if v}, 'reason': reason}
