"""User-triggered meta-suggestion: isolation, evidence-backed suggestions, nothing auto-enabled."""
import json
import os
import subprocess
import sys
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from meeting_copilot.assistant import Assistant
from meeting_copilot.config import Config
from meeting_copilot.meta_suggest import (add_feedback, draft_contract, model_suggestions, preferences, private_turns,
                                          rule_suggestions, set_preferences, suggest)
from meeting_copilot.provider import Completion, Provider
from meeting_copilot.service import EvidenceService
from meeting_copilot.skill_runtime import SKILLS, ToolDenied, load_contract, policy_for
from meeting_copilot.store import Denied, Store
from meeting_copilot.trace import Trace
from test_skill_runtime import ScriptedProvider

FACTS = ['Atlas chooses SQLite because deployment needs no database service.',
         'Zimo owns microphone capture. Cai owns security policy. Qiyan owns evaluation.',
         'Action items: Zimo ships capture by Friday.']
QUESTIONS = [('Who owns microphone capture?', 'c1'), ('Who owns security policy? briefly', 'c1'), ('Who owns evaluation? Briefly please', 'c2'),
             ('Ask me hard questions about SQLite', 'c2'), ('Challenge the SQLite deployment', 'c3'), ('Why does Atlas choose SQLite?', 'c3'),
             ('What is the cafeteria menu?', 'c3')]


@pytest.fixture
async def history(store, tmp_path):
    for n, text in enumerate(FACTS, 1):
        store.ingest('alice', 'm', {'event_id': f'e{n}', 'stream': 's', 'seq': n, 'segment_id': f's{n}', 'revision': 1,
                                    'start_ms': n*1000, 'end_ms': n*1000+900, 'text': text, 'final': True})
    assistant = Assistant(store, 'alice', Provider(Config()), Trace(tmp_path/'ask.jsonl'))
    answers = []
    for question, conversation in QUESTIONS:
        answers.append(await assistant.ask(store.snapshot('alice', 'm'), question, conversation, 'auto'))
    return answers


async def test_rules_separate_preferences_existing_skills_and_new_skill_candidates(store, history, tmp_path):
    trace = Trace(tmp_path/'meta.jsonl'); sid = store.snapshot('alice', 'm')
    report = await suggest(store, 'alice', sid, trace)
    assert report['mode'].startswith('rules only') and report['scope']['turns_analysed'] == 7 and report['model_usage'] is None
    by_kind = {s['kind']: s for s in report['suggestions']}
    assert set(by_kind) == {'preference', 'use_existing_skill', 'new_skill'}
    turn_ids = {t['question']: t['turn_id'] for t in private_turns(store, 'alice', sid)}
    # "briefly" twice is a wording preference, not a skill.
    assert by_kind['preference']['preference'] == 'Keep answers brief.' and by_kind['preference']['recommended_action'] == 'prefs add "Keep answers brief."'
    assert by_kind['preference']['evidence_turn_ids'] == sorted(turn_ids[q] for q in (QUESTIONS[1][0], QUESTIONS[2][0]))
    # Wanting to be questioned is the existing grill_me skill; the missing trigger words are reported.
    assert by_kind['use_existing_skill']['skill'] == 'grill_me' and by_kind['use_existing_skill']['missing_trigger_phrases'] == ['ask me', 'challenge']
    # Three unmatched "who owns ..." questions: a candidate skill with a valid draft contract and tests.
    spec = by_kind['new_skill']['specification']
    assert spec['draft_contract']['name'] == 'who_owns' and spec['draft_contract']['version'] == '0.1.0' and by_kind['new_skill']['support'] == 3
    assert [t['question'] for t in spec['test_cases'][:3]] == [q for q, _ in reversed(QUESTIONS[:3])]
    assert all(s['support'] >= 2 and s['source'] == 'rules' for s in report['suggestions'])
    # Nothing was enabled or saved.
    assert 'who_owns' not in SKILLS and preferences(store, 'alice') == []
    with pytest.raises(ValueError, match='unknown skill'):
        load_contract('who_owns')
    assert 'Nothing was changed' in report['activation']
    rows = trace.rows(snapshot_id=sid)
    assert [(r['tool'], r['policy']) for r in rows if r['event'] == 'tool_call'] == [('meet2notes_status', 'allow'), ('list_private_turns', 'allow'), ('list_feedback', 'allow')]
    assert rows[-1]['event'] == 'meta_suggest' and 'microphone' not in json.dumps(rows)
    # After the preference is saved it is no longer suggested.
    set_preferences(store, 'alice', ['Keep answers brief.'])
    assert 'preference' not in {s['kind'] for s in (await suggest(store, 'alice', store.snapshot('alice', 'm')))['suggestions']}


async def test_feedback_drives_adjust_skill_and_single_complaints_change_nothing(store, history, tmp_path):
    sid = store.snapshot('alice', 'm')
    assert rule_suggestions(private_turns(store, 'alice', sid)[:1], [])[0]['kind'] == 'no_change'
    why = [a for a in history if a['skill'] == 'trace_decision'][0]
    add_feedback(store, 'alice', why['snapshot_id'], 'c3', 'Why does Atlas choose SQLite?', 'down', 'too long')
    report = await suggest(store, 'alice', store.snapshot('alice', 'm'))
    assert 'adjust_skill' not in {s['kind'] for s in report['suggestions']} and report['signals']['feedback'] == {'up': 0, 'down': 1}
    assistant = Assistant(store, 'alice', Provider(Config()), Trace(tmp_path/'more.jsonl'))
    second = await assistant.ask(store.snapshot('alice', 'm'), 'Why was the decision made for SQLite?', 'c4', 'auto')
    add_feedback(store, 'alice', second['snapshot_id'], 'c4', 'Why was the decision made for SQLite?', 'down', 'missed the tradeoff')
    adjust = [s for s in (await suggest(store, 'alice', store.snapshot('alice', 'm')))['suggestions'] if s['kind'] == 'adjust_skill']
    assert len(adjust) == 1 and adjust[0]['skill'] == 'trace_decision' and adjust[0]['feedback_notes'] == ['too long', 'missed the tradeoff']
    with pytest.raises(ValueError, match='no stored answer'):
        add_feedback(store, 'alice', sid, 'c1', 'never asked', 'up')
    with pytest.raises(ValueError, match='rating'):
        add_feedback(store, 'alice', why['snapshot_id'], 'c3', 'Why does Atlas choose SQLite?', 'meh')


async def test_history_is_isolated_per_user_and_dropped_after_revocation(store, history, tmp_path):
    store.grant('alice', 'm', 'bob')
    bob_sid = store.snapshot('bob', 'm')
    assert private_turns(store, 'bob', bob_sid) == []                      # bob never sees alice's questions
    report = await suggest(store, 'bob', bob_sid)
    assert report['scope']['turns_analysed'] == 0 and [s['kind'] for s in report['suggestions']] == ['no_change']
    with pytest.raises(Denied):
        private_turns(store, 'bob', store.snapshot('alice', 'm'))          # nor through alice's snapshot
    await Assistant(store, 'bob', Provider(Config()), Trace(tmp_path/'bob.jsonl')).ask(store.snapshot('bob', 'm'), 'Who owns evaluation?', 'b', 'auto')
    assert len(private_turns(store, 'bob', store.snapshot('bob', 'm'))) == 1
    store.grant('alice', 'm', 'bob', False)
    with pytest.raises(Denied):
        await suggest(store, 'bob', bob_sid)
    # Private-history tools are refused for every contract except meta_suggest, including an unbound session.
    sid = store.snapshot('alice', 'm')
    for skill in (None, 'trace_decision', 'clarify', 'direct'):
        service = EvidenceService(store, 'alice', sid, None, policy_for(load_contract(skill), 'mcp') if skill else None)
        with pytest.raises(ToolDenied, match='tool_not_allowed_for_skill'):
            service.call('list_private_turns', {})
    meta = EvidenceService(store, 'alice', sid, None, policy_for(load_contract('meta_suggest'), 'mcp'))
    turns = meta.call('list_private_turns', {})
    assert len(turns) == 7 and not {'text', 'answer', 'claims_text', 'citations'} & set(turns[0])
    with pytest.raises(ToolDenied):
        meta.call('get_transcript', {})                                    # and meta_suggest cannot read evidence


async def test_model_suggestions_must_cite_real_turns_and_registered_tools(store, history):
    sid = store.snapshot('alice', 'm'); turns = private_turns(store, 'alice', sid); ids = [t['turn_id'] for t in turns]
    base = {'title': 'T', 'observed': 'O', 'recommended_action': 'A'}
    rows = [
        {**base, 'kind': 'new_skill', 'evidence_turn_ids': ids[:3], 'specification': {'name': 'Action Items', 'purpose': 'List owners and dates',
            'trigger_phrases': ['action items'], 'output_fields': ['items'], 'tools': ['search_evidence'], 'test_cases': [{'question': 'Action items?', 'expect': 'cites owners'}]}},
        {**base, 'kind': 'new_skill', 'evidence_turn_ids': ids[:3], 'specification': {'name': 'mailer', 'tools': ['send_email']}},
        {**base, 'kind': 'new_skill', 'evidence_turn_ids': ids[:3], 'specification': {'name': 'clarify', 'tools': []}},
        {**base, 'kind': 'preference', 'evidence_turn_ids': [ids[0], 10**6], 'preference': 'Be brief.'},
        {**base, 'kind': 'adjust_skill', 'evidence_turn_ids': ids[:1], 'skill': 'clarify'},
        {**base, 'kind': 'adjust_skill', 'evidence_turn_ids': ids[:2], 'skill': 'shell'},
        {**base, 'kind': 'rewrite_everything', 'evidence_turn_ids': ids[:2]},
        {**base, 'kind': 'preference', 'evidence_turn_ids': ids[:2], 'preference': 'Answer in Chinese.'}]
    kept, dropped = model_suggestions(json.dumps({'suggestions': rows}), turns)
    assert [(k['kind'], k['source']) for k in kept] == [('new_skill', 'model'), ('preference', 'model')]
    assert kept[0]['specification']['draft_contract']['name'] == 'action_items' and kept[0]['specification']['draft_contract']['allowed_tools']['model'] == ['read_evidence', 'search_evidence']
    assert dropped == {'tool_or_name_outside_registry': 2, 'cites_unknown_turns': 1, 'too_little_support': 1, 'unknown_skill': 1, 'invalid_kind': 1}
    provider = ScriptedProvider([Completion(json.dumps({'suggestions': rows}), 40, 20)])
    report = await suggest(store, 'alice', sid, None, provider)
    assert report['dropped_model_suggestions'] == dropped and report['model_usage'] == {'input_tokens': 40, 'output_tokens': 20}
    assert sum(s['source'] == 'model' for s in report['suggestions']) == 2 and 'validated for cited turns' in report['mode']
    sent = json.dumps(provider.requests[0]['messages'])
    assert 'Who owns microphone capture?' in sent and FACTS[0] not in sent      # questions go out; evidence and answers do not
    assert draft_contract('x', 'p', ['Mixed Case '])['routing']['phrases'] == {'mixed case': 3}


def test_preferences_are_bounded_and_per_user(store):
    assert set_preferences(store, 'alice', ['Keep answers brief.', 'Keep answers brief.', ' Answer in Chinese. ']) == ['Keep answers brief.', 'Answer in Chinese.']
    assert preferences(store, 'bob') == []
    for bad in (['x']*6, ['a\nb'], [''], 'brief'):
        with pytest.raises(ValueError):
            set_preferences(store, 'alice', bad)


async def test_cli_feedback_prefs_suggest_and_stdio_private_tools(tmp_path):
    config = tmp_path/'host.json'; config.write_text(json.dumps({'principal': 'local-host', 'data_dir': 'runtime'}))
    app = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); env = {**os.environ, 'PYTHONUTF8': '1'}
    def cli(*args, ok=True):
        done = subprocess.run([sys.executable, '-m', 'meeting_copilot', '--config', str(config), *args], capture_output=True, text=True, encoding='utf-8', cwd=app, env=env, timeout=120)
        assert (done.returncode == 0) == ok, done.stderr
        return json.loads(done.stdout) if ok else done.stderr
    cli('create', 'design'); cli('replay', 'design', os.path.join(app, 'examples', 'replay.jsonl'))
    answers = [cli('ask', 'design', q, '--conversation', 'n') for q in ('Who owns microphone capture? briefly', 'Who owns security policy? briefly', 'Who owns evaluation?')]
    assert cli('feedback', answers[0]['snapshot_id'], 'Who owns microphone capture? briefly', '--rating', 'down', '--note', 'too long', '--conversation', 'n')['rating'] == 'down'
    report = cli('suggest', 'design')
    assert {s['kind'] for s in report['suggestions']} >= {'preference'} and report['signals']['feedback']['down'] == 1
    assert 'needs a configured API provider' in cli('suggest', 'design', '--use-model', ok=False)
    assert cli('prefs', 'add', 'Keep answers brief.')['preferences'] == [{'number': 1, 'text': 'Keep answers brief.'}]
    assert cli('prefs', 'remove', '1')['preferences'] == []
    sid = answers[0]['snapshot_id']
    async def session(skill):
        params = StdioServerParameters(command=sys.executable, args=['-m', 'meeting_copilot', '--config', str(config), 'mcp', sid, '--skill', skill], env=env, cwd=app)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                return await client.call_tool('list_private_turns', {}), await client.call_tool('get_transcript', {})
    turns, transcript = await session('meta_suggest')
    assert not turns.is_error and 'microphone' in json.dumps(turns.model_dump()) and transcript.is_error
    turns, transcript = await session('direct')
    assert turns.is_error and not transcript.is_error
