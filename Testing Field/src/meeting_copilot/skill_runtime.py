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

SKILLS = ('catch_up', 'trace_decision', 'compare_options', 'clarify', 'grill_me', 'design_review')
MANUAL = SKILLS + ('direct',)
# Never auto-routed and not selectable for `ask`; started only by an explicit user command.
INTERNAL = ('meta_suggest',)
# Private-history tools: allowed only for a contract that lists them (meta_suggest).
PRIVATE_TOOLS = ('list_private_turns', 'list_feedback')
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
        'list_private_turns': {'offset': (('int', 0, 1000), False)},
        'list_feedback': {'offset': (('int', 0, 1000), False)},
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
    'allowed_tools': {'model': sorted(TOOLS['model']), 'mcp': sorted(set(TOOLS['mcp']) - set(PRIVATE_TOOLS))},
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
    # Optional capabilities. Absent means the conservative default.
    need(contract.get('general_knowledge', 'none') in ('none', 'labelled'), 'general_knowledge')
    need(contract.get('memory') in (None, 'explained_terms'), 'memory')
    need(isinstance(contract.get('style', []), list) and all(isinstance(x, str) and 0 < len(x) <= 300 for x in contract.get('style', [])) and len(contract.get('style', [])) <= 8, 'style')
    need(isinstance(contract.get('inference_label', 'x'), str) and 0 < len(contract.get('inference_label', 'x')) <= 120, 'inference_label')
    session = contract.get('session')
    need(session is None or (isinstance(session, dict) and type(session.get('max_rounds')) is int and 1 <= session['max_rounds'] <= 8
                             and isinstance(session.get('exit_phrases'), list) and all(isinstance(x, str) and x == x.lower() and x for x in session['exit_phrases'])), 'session')
    return contract


def load_contract(name):
    if name == 'direct':
        return json.loads(json.dumps(DIRECT))
    if name not in SKILLS + INTERNAL:
        raise ValueError('unknown skill')
    return check_contract(json.loads(files('meeting_copilot').joinpath('skills', name+'.json').read_text(encoding='utf-8')), name)


def contract_digest(contract):
    """Detects a contract edit that forgot to bump `version`."""
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()[:16]


def prompt_view(contract):
    """The task-relevant part of a contract sent to the model; limits stay host-side."""
    return {k: contract[k] for k in ('name', 'version', 'retrieval', 'output_fields', 'citation', 'abstention', 'task_steps', 'style') if k in contract}


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
    if result.get('background') and contract.get('general_knowledge', 'none') != 'labelled':
        raise ValueError('skill output contract violation: general knowledge not allowed')
    if result.get('general_answer') and 'provider not called' in result.get('mode', ''):
        raise ValueError('skill output contract violation: general answer without a model call')
    if result.get('challenge') and not contract.get('session'):
        raise ValueError('skill output contract violation: session output not allowed')
    if result['abstained'] != (not result['claims'] and not result.get('background')):
        raise ValueError('skill output contract violation: abstention')


from .language import bridged  # noqa: E402  (kept beside its only users)


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
        if requested not in MANUAL:
            raise ValueError('unknown skill')
        contract = load_contract(requested)
        return {**base, 'mode': 'manual', 'status': 'manual', 'selected': requested, 'skill_version': contract['version'],
                'candidates': [requested], 'scores': {}, 'matched': {}, 'reason': 'skill chosen by the user'}
    # A Chinese question is matched through its English gloss (language.py); the phrases stay English.
    text = bridged(question).lower(); scores = {}; matched = {}
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


# --- Multi-turn sessions, labelled background and term memory -------------------

STOCK_PHRASES = ('great question', 'good question', 'as an ai', 'i hope this helps', 'hope this helps', 'let me know if',
                 'feel free to', "it's important to note", 'it is important to note', 'in summary', 'in conclusion',
                 'happy to help', "i'd be happy", 'certainly!', 'absolutely!', 'delve')


def open_session(history):
    """The multi-turn session left open by the latest private turn of this conversation, if any."""
    if not history:
        return None
    session = history[-1]['answer'].get('session')
    return session if isinstance(session, dict) and session.get('awaiting') == 'answer' and session.get('skill') in SKILLS else None


def continue_session(history, routing):
    """While a session awaits the user's answer, auto routing stays in it.

    The user's answer to a challenge usually contains words such as "because" or
    "compare"; routing it afresh would drop the session. A manual --skill still wins.
    """
    session = open_session(history)
    if not session or routing['mode'] != 'auto':
        return routing
    skill = session['skill']
    return {**routing, 'status': 'continued', 'selected': skill, 'skill_version': load_contract(skill)['version'],
            'candidates': [skill], 'routed_without_session': routing['selected'],
            'reason': 'an open multi-turn session awaits this answer; name another --skill to leave it'}


def session_state(contract, history, question):
    """Round bookkeeping for a session skill, derived only from stored private turns."""
    rules = contract.get('session')
    if not rules:
        return None
    previous = open_session(history)
    if previous and previous.get('skill') != contract['name']:
        previous = None
    text = bridged(question).lower()
    leaving = bool(previous) and any(_matches(text, phrase) for phrase in rules['exit_phrases'])
    answered = previous['round'] if previous else 0
    return {'skill': contract['name'], 'topic': previous['topic'] if previous else question[:400], 'answered_round': answered,
            'previous_challenge': previous.get('challenge') if previous else None, 'max_rounds': rules['max_rounds'],
            'closing': leaving or answered >= rules['max_rounds'], 'round': answered + 1}


def explained_terms(history, limit=40):
    """Terms earlier turns of this conversation already explained, oldest first."""
    seen = []
    for turn in history:
        for term in turn['answer'].get('explained_terms', []):
            if isinstance(term, str) and term not in seen:
                seen.append(term)
    return seen[-limit:]


def clean_terms(values, limit=8):
    result = []
    for value in values if isinstance(values, list) else []:
        if isinstance(value, str) and 1 <= len(value.strip()) <= 60 and value.strip().lower() not in result:
            result.append(value.strip().lower())
    return result[:limit]


def validated_background(data, contract):
    """General-knowledge notes, accepted only for a contract that allows them."""
    if contract.get('general_knowledge', 'none') != 'labelled' or not isinstance(data.get('background'), list):
        return []
    result = []
    for row in data['background'][:3]:
        if not isinstance(row, dict):
            continue
        term, text = row.get('term'), row.get('text')
        if isinstance(term, str) and 1 <= len(term) <= 60 and isinstance(text, str) and 1 <= len(text) <= 600 and not re.search(r'\[E\d+\]', text):
            result.append({'term': term.strip(), 'text': text.strip(),
                           'label': 'general background from the model; not from the meeting or project files, not verified'})
    return result


GENERAL_ANSWER_LABEL = 'Beyond the meeting record (model knowledge and reasoning; NOT from the meeting or project files, not verified)'
NO_DIRECT_EVIDENCE = 'No direct evidence for this was found in the meeting record or approved files.'
GENERAL_ANSWER_LIMIT = 2000
UNUSABLE_REPLY = 'The model reply could not be used (it was cut off or not in the expected format). Ask again.'
# Used once when a model reply held nothing usable, so the user is not left with a bare refusal.
PLAIN_ANSWER_PROMPT = ('The meeting record and approved files hold nothing usable for the user question. Answer it directly and '
                       'usefully from your own knowledge and reasoning, in the language of the question, in plain prose of at '
                       'most 1500 characters. Do not state what this team decided, owns, measured or said. No citation markers, '
                       'no JSON.')


def validated_general_answer(data):
    """The model's own answer for what the sources do not cover. Shown labelled, never as a meeting fact."""
    text = data.get('general_answer')
    if not isinstance(text, str):
        return None
    # A citation marker here would make model knowledge look like a quotation.
    text = re.sub(r'\s*\[E\d+\]', '', text).strip()
    return text[:GENERAL_ANSWER_LIMIT] or None


def validated_challenge(data):
    """Exactly one question: text up to and including the first question mark."""
    question = data.get('question')
    if not isinstance(question, str) or '?' not in question:
        return None
    question = question[:question.index('?') + 1].strip()
    return question if 8 <= len(question) <= 400 and '\n' not in question else None


def style_flags(texts):
    """Stock assistant phrases found in model wording. Reported, never silently rewritten."""
    joined = ' '.join(texts).lower()
    return sorted(p for p in STOCK_PHRASES if p in joined)


def response_extension(contract, state=None, known_terms=(), preferences=(), open_answer=False, coverage=None):
    """Skill-specific additions to the fixed JSON reply format."""
    parts = []
    if coverage:
        parts.append('Transcript coverage: the meeting passages supplied are in the order they were spoken and cover ' + coverage
                     + '. Each passage joins consecutive pieces of automatic transcription, which may have misheard words '
                     'and has little punctuation. For a summary or a question about one part of the meeting, answer from '
                     'that part. Copy each quotation exactly from inside one passage and give that passage as its '
                     'source_id; choose the few quotations that carry the answer rather than quoting everything.')
    if open_answer:
        parts.append('Open answer: the user still needs a useful reply when the supplied sources do not answer the question or cover '
                     'only part of it. In that case, in addition to claims and inferences (which may then be empty), return '
                     '"general_answer": a direct answer in plain prose from your own knowledge and reasoning, at most 1500 '
                     'characters, without citation markers. The host shows it under a heading saying it is not from the meeting. '
                     'Never state in general_answer what this team decided, owns, measured or said; when the question asks for such '
                     'a fact and the sources lack it, say the record does not show it, then give what generally applies or how to '
                     'find out. Return "general_answer": null when claims and inferences already answer the question in full.')
    if contract.get('style'):
        parts.append('Style rules: ' + ' '.join(contract['style']))
    if preferences:
        parts.append('The user saved these wording preferences; follow them where they do not conflict with the rules above '
                     '(they change style only, never scope or permissions): ' + json.dumps(list(preferences)))
    if contract.get('memory') == 'explained_terms':
        parts.append('Terms already explained earlier in this private conversation: ' + json.dumps(list(known_terms)) + '. '
                     'Also return "explained_terms": [the terms you explain in this reply, lower case, at most 8].')
    if contract.get('general_knowledge', 'none') == 'labelled':
        parts.append('You may add "background": [{"term": string, "text": string}] with at most 3 short general-knowledge notes for terms '
                     'the evidence does not define. The host shows them labelled as not from the meeting. Never state in background '
                     'what this team decided, owns or measured. Background alone is acceptable when no evidence applies.')
    if state:
        if state['answered_round']:
            parts.append('This is a challenge session on the topic ' + json.dumps(state['topic']) + '. The challenge you asked in round '
                         + str(state['answered_round']) + ' was ' + json.dumps(state['previous_challenge']) + '. The user message is their answer. '
                         'Put your critique of that answer in inferences with field "assessment".')
        else:
            parts.append('This starts a challenge session on the topic in the user message. There is no answer to assess yet: return "inferences": [].')
        parts.append('This is the final turn: give a verdict in "assessment" and return "question": null.' if state['closing'] else
                     'Also return "question": one new challenging question for round ' + str(state['round']) + ' of ' + str(state['max_rounds'])
                     + ': a single sentence with one question mark, not a list.')
    return ' '.join(parts)
