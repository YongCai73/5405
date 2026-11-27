"""clarify, grill_me and design_review: term memory, labelled background, multi-turn state."""
import json
import pytest
from meeting_copilot.assistant import Assistant
from meeting_copilot.config import Config
from meeting_copilot.meta_suggest import set_preferences
from meeting_copilot.provider import Completion, Provider
from meeting_copilot.skill_runtime import check_contract, load_contract, route, style_flags, validated_challenge
from meeting_copilot.trace import Trace
from test_skill_runtime import ScriptedProvider

FACTS = ['Atlas chooses SQLite with FTS5 as the evidence store because deployment needs no database service.',
         'The snapshot watermark pins which transcript revisions a question may cite.',
         'We rejected Kafka for the prototype because operating a broker adds deployment complexity.']


@pytest.fixture
def meeting(store):
    ids = [store.ingest('alice', 'm', {'event_id': f'e{n}', 'stream': 's', 'seq': n, 'segment_id': f's{n}', 'revision': 1,
                                       'start_ms': n*1000, 'end_ms': n*1000+900, 'text': text, 'final': True})
           for n, text in enumerate(FACTS, 1)]
    return ids


def reply(**data):
    return Completion(json.dumps(data), 9, 5)


@pytest.mark.parametrize('question,skill', [
    ('Can you clarify what the watermark means?', 'clarify'), ('Explain FTS5 in plain language', 'clarify'),
    ('Grill me on the SQLite decision', 'grill_me'), ('Play devil\'s advocate on our Kafka call', 'grill_me'),
    ('What could go wrong with the snapshot design?', 'design_review'), ('Do a design review of the evidence store', 'design_review'),
    ('Explain why we rejected Kafka', 'trace_decision')])
def test_new_skills_are_routed(question, skill):
    assert route(question)['selected'] == skill


def test_new_contract_capabilities_are_validated():
    assert load_contract('clarify')['general_knowledge'] == 'labelled' and load_contract('grill_me')['session']['max_rounds'] == 5
    assert all(load_contract(s).get('general_knowledge', 'none') == 'none' for s in ('catch_up', 'trace_decision', 'compare_options', 'grill_me', 'design_review'))
    for key, bad in (('general_knowledge', 'anything'), ('memory', 'everything'), ('session', {'max_rounds': 99, 'exit_phrases': []}), ('style', ['x']*9)):
        forged = load_contract('clarify'); forged[key] = bad
        with pytest.raises(ValueError, match=key):
            check_contract(forged, 'clarify')
    assert validated_challenge({'question': 'What breaks first? And second?'}) == 'What breaks first?'
    assert validated_challenge({'question': 'no question mark'}) is None and validated_challenge({'question': ['a?']}) is None
    assert style_flags(['Great question! SQLite is simple.', 'I hope this helps.']) == ['great question', 'hope this helps', 'i hope this helps']


async def test_clarify_remembers_explained_terms_across_snapshots_and_labels_background(store, meeting, tmp_path):
    provider = ScriptedProvider([
        reply(claims=[{'source_id': meeting[1], 'quote': FACTS[1]}],
              inferences=[{'text': 'The watermark is the cut-off: later transcript text cannot be cited.', 'source_ids': [meeting[1]], 'field': 'explanation'}],
              background=[{'term': 'watermark', 'text': 'In data systems, a marker for how far processing has progressed.'},
                          {'term': 'bad', 'text': 'The team decided this [E1].'}],
              explained_terms=['Watermark']),
        reply(claims=[{'source_id': meeting[0], 'quote': FACTS[0]}],
              inferences=[{'text': 'FTS5 is the SQLite full-text index used for retrieval, bounded by the watermark.', 'source_ids': [meeting[0]], 'field': 'explanation'}],
              explained_terms=['fts5'])])
    assistant = Assistant(store, 'alice', provider, Trace(tmp_path/'t.jsonl'))
    set_preferences(store, 'alice', ['Keep answers brief.'])
    first = await assistant.ask(store.snapshot('alice', 'm'), 'Can you clarify what the watermark means?', 'c', 'auto')
    assert first['skill'] == 'clarify' and not first['abstained']
    assert [b['term'] for b in first['background']] == ['watermark'] and 'not from the meeting' in first['background'][0]['label']
    assert 'General background (model knowledge; NOT from the meeting' in first['text'] and first['text'].startswith('Explanation (model wording')
    assert first['explained_terms'] == ['watermark'] and first['previously_explained'] == []
    assert set(first['fields']) == {'explanation', 'related_evidence'} and first['fields']['related_evidence'] == first['claims']
    second = await assistant.ask(store.snapshot('alice', 'm'), 'Explain how FTS5 relates to the watermark', 'c', 'auto')
    assert second['explained_terms'] == ['watermark', 'fts5'] and second['previously_explained'] == ['watermark']
    provider.replies.append(reply(claims=[{'source_id': meeting[1], 'quote': FACTS[1]}]))
    loose = await assistant.ask(store.snapshot('alice', 'm'), 'Clarify the watermark cut-off again', 'c', 'auto')
    assert loose['previously_explained'] == ['watermark']
    system = provider.requests[1]['messages'][0]['content']
    assert 'Terms already explained earlier in this private conversation: ["watermark"]' in system
    assert 'is not defined again' in system and 'Keep answers brief.' in system and 'never scope or permissions' in system
    # Another private conversation starts with an empty memory.
    provider.replies.append(reply(claims=[{'source_id': meeting[1], 'quote': FACTS[1]}]))
    other = await assistant.ask(store.snapshot('alice', 'm'), 'Explain the watermark', 'other', 'auto')
    assert other['previously_explained'] == [] and 'conversation: []' in provider.requests[3]['messages'][0]['content']


async def test_clarify_may_answer_from_labelled_background_but_other_skills_may_not(store, meeting, tmp_path):
    background = reply(claims=[], inferences=[], background=[{'term': 'raft', 'text': 'A consensus protocol for replicated logs.'}])
    provider = ScriptedProvider([background])
    assistant = Assistant(store, 'alice', provider, Trace(tmp_path/'t.jsonl'))
    sid = store.snapshot('alice', 'm')
    clarified = await assistant.ask(sid, 'Define raft', 'c', 'auto')
    assert clarified['skill'] == 'clarify' and clarified['claims'] == [] and not clarified['abstained'] and clarified['abstain_reason'] is None
    assert clarified['text'].startswith('Insufficient evidence in this authorized snapshot.') and 'raft: A consensus protocol' in clarified['text']
    # trace_decision has no general-knowledge permission: no evidence means no provider call and no background.
    traced = await assistant.ask(sid, 'Why raft?', 'd', 'auto')
    assert traced['skill'] == 'trace_decision' and traced['abstained'] and 'background' not in traced and len(provider.requests) == 1


async def test_grill_me_is_a_stateful_multi_turn_session(store, meeting, tmp_path):
    quote = {'source_id': meeting[0], 'quote': FACTS[0]}
    critique = {'text': 'You did not address concurrent writers.', 'source_ids': [meeting[0]], 'field': 'assessment'}
    provider = ScriptedProvider([
        reply(claims=[quote], inferences=[critique], question='What happens to SQLite when two writers collide? Also, why?'),
        reply(claims=[quote], inferences=[critique], question='Which workload would force you off SQLite?'),
        reply(claims=[quote], inferences=[{**critique, 'text': 'The single-writer limit was never tested.'}], question=None)])
    trace = Trace(tmp_path/'t.jsonl'); assistant = Assistant(store, 'alice', provider, trace)
    one = await assistant.ask(store.snapshot('alice', 'm'), 'Grill me on the SQLite decision', 'g', 'auto')
    assert one['skill'] == 'grill_me' and one['session'] == {'skill': 'grill_me', 'topic': 'Grill me on the SQLite decision', 'round': 1, 'max_rounds': 5,
                                                              'awaiting': 'answer', 'challenge': 'What happens to SQLite when two writers collide?'}
    assert one['text'].endswith('Challenge 1 of 5: What happens to SQLite when two writers collide?')
    # Round 1 has no answer to assess: an "assessment" the model volunteers is discarded.
    assert one['inferences'] == [] and 'Assessment of your answer' not in one['text'] and one['validation_report']['inferences_rejected'] == 1
    assert 'return "inferences": []' in provider.requests[0]['messages'][0]['content']
    # The answer contains "because", "compare" and "why"; auto routing must stay in the session.
    answer = 'Because we compare favourably: there is one writer, so why worry about SQLite locks'
    assert route(answer)['selected'] != 'grill_me'
    two = await assistant.ask(store.snapshot('alice', 'm'), answer, 'g', 'auto')
    assert two['skill'] == 'grill_me' and two['routing']['status'] == 'continued' and two['session']['round'] == 2 and two['session']['topic'] == one['session']['topic']
    assert two['inferred_fields']['assessment'][0]['text'] == 'You did not address concurrent writers.'
    assert two['text'].startswith('Assessment of your answer (model critique, not a meeting fact):')
    system = provider.requests[1]['messages'][0]['content']
    assert 'The challenge you asked in round 1 was "What happens to SQLite when two writers collide?"' in system and 'round 2 of 5' in system
    three = await assistant.ask(store.snapshot('alice', 'm'), "That's enough, wrap up", 'g', 'auto')
    assert three['skill'] == 'grill_me' and three['session']['awaiting'] is None and 'challenge' not in three and three['text'].endswith('Challenge session closed.')
    assert 'This is the final turn' in provider.requests[2]['messages'][0]['content']
    # Closed: the next question routes normally again. A different conversation never saw the session.
    provider.replies += [reply(claims=[{'source_id': meeting[2], 'quote': FACTS[2]}]), reply(claims=[quote])]
    after = await assistant.ask(store.snapshot('alice', 'm'), 'Why was Kafka rejected?', 'g', 'auto')
    assert after['skill'] == 'trace_decision' and after['routing']['status'] == 'matched' and 'session' not in after
    assert [r.get('step') for r in trace.rows() if r['event'] == 'answer'] == ['round_1', 'round_2', 'round_2', None]


async def test_grill_me_session_is_private_to_user_and_conversation_and_manual_skill_leaves_it(store, meeting, tmp_path):
    quote = {'source_id': meeting[0], 'quote': FACTS[0]}
    provider = ScriptedProvider([reply(claims=[quote], question='What breaks first?'), reply(claims=[quote]), reply(claims=[quote])])
    assistant = Assistant(store, 'alice', provider, Trace(tmp_path/'t.jsonl'))
    await assistant.ask(store.snapshot('alice', 'm'), 'Grill me on the SQLite decision', 'g', 'auto')
    elsewhere = await assistant.ask(store.snapshot('alice', 'm'), 'SQLite locks are fine', 'other', 'auto')
    assert elsewhere['skill'] == 'direct' and elsewhere['routing']['status'] == 'not_applicable'
    manual = await assistant.ask(store.snapshot('alice', 'm'), 'SQLite deployment', 'g', 'direct')
    assert manual['skill'] == 'direct' and manual['routing']['mode'] == 'manual'
    store.grant('alice', 'm', 'bob')
    bob = await Assistant(store, 'bob', ScriptedProvider([reply(claims=[quote])]), Trace(tmp_path/'b.jsonl')).ask(store.snapshot('bob', 'm'), 'SQLite locks are fine', 'g', 'auto')
    assert bob['skill'] == 'direct'


async def test_grill_me_round_limit_closes_the_session_and_no_evidence_abstains(store, meeting, tmp_path):
    quote = {'source_id': meeting[0], 'quote': FACTS[0]}
    provider = ScriptedProvider([reply(claims=[quote], question=f'Challenge number {n} about SQLite?') for n in range(1, 7)])
    assistant = Assistant(store, 'alice', provider, Trace(tmp_path/'t.jsonl'))
    rounds = [await assistant.ask(store.snapshot('alice', 'm'), 'Grill me on the SQLite decision' if n == 0 else f'SQLite answer {n}', 'g', 'auto') for n in range(6)]
    assert [r['session']['round'] for r in rounds] == [1, 2, 3, 4, 5, 5] and [r['session']['awaiting'] for r in rounds] == ['answer']*5 + [None]
    assert 'challenge' not in rounds[5]  # the model offered a sixth question; the host refused it
    nothing = await assistant.ask(store.snapshot('alice', 'm'), 'Grill me on the cafeteria menu', 'none', 'auto')
    assert nothing['skill'] == 'grill_me' and nothing['abstained'] and nothing['session']['awaiting'] is None and len(provider.requests) == 6


async def test_design_review_separates_meeting_facts_from_model_suggestions(store, meeting, tmp_path):
    quote = {'source_id': meeting[0], 'quote': FACTS[0]}
    provider = ScriptedProvider([reply(claims=[quote, {'source_id': meeting[0], 'quote': 'The team agreed to load test it.'}], inferences=[
        {'text': 'Assumes a single writer.', 'source_ids': [meeting[0]], 'field': 'key_assumptions'},
        {'text': 'Great question! Write contention stalls ingestion.', 'source_ids': [meeting[0]], 'field': 'failure_scenarios'},
        {'text': 'Measure write latency under two writers first.', 'source_ids': [meeting[0]], 'field': 'validate_first'},
        {'text': 'The team decided to shard.', 'source_ids': [999], 'field': 'key_assumptions'},
        {'text': 'Not a field.', 'source_ids': [meeting[0]], 'field': 'decisions'}])])
    result = await Assistant(store, 'alice', provider, Trace(tmp_path/'t.jsonl')).ask(store.snapshot('alice', 'm'), 'What could go wrong with the SQLite evidence store design?', 'r', 'auto')
    assert result['skill'] == 'design_review' and result['claims'] == [quote]            # the invented quotation is dropped
    assert {k: len(v) for k, v in result['inferred_fields'].items()} == {'proposal_evidence': 0, 'key_assumptions': 1, 'failure_scenarios': 1, 'validate_first': 1}
    assert result['validation_report']['claims_rejected'] == 1 and result['validation_report']['inferences_rejected'] == 2
    assert result['validation_report']['style_flags'] == ['great question']
    head, quotes = result['text'].split('\n\nExact supporting quotations:\n')
    assert head.startswith('Model suggestions (not meeting facts; check against the quotations):') and quotes == f'{FACTS[0]} [E{meeting[0]}]'
    assert head.splitlines()[1:] == ['Key assumptions:', f'- Assumes a single writer. [E{meeting[0]}]', 'Failure scenarios:',
                                     f'- Great question! Write contention stalls ingestion. [E{meeting[0]}]', 'Validate first:',
                                     f'- Measure write latency under two writers first. [E{meeting[0]}]']
    assert result['fields']['proposal_evidence'] == [quote]


async def test_simulated_mode_runs_the_new_skills_and_says_what_it_cannot_do(store, meeting, tmp_path):
    assistant = Assistant(store, 'alice', Provider(Config()), Trace(tmp_path/'t.jsonl'))
    first = await assistant.ask(store.snapshot('alice', 'm'), 'Explain the watermark', 'c', 'auto')
    again = await assistant.ask(store.snapshot('alice', 'm'), 'Clarify the watermark and revisions', 'c', 'auto')
    assert first['explained_terms'] == ['watermark'] and again['previously_explained'] == ['watermark']
    assert again['text'].startswith('Already explained earlier in this conversation (not repeated in full): watermark')
    assert 'need a configured model' in first['notes'][0] and 'background' not in first
    one = await assistant.ask(store.snapshot('alice', 'm'), 'Grill me on the SQLite decision', 'g', 'auto')
    two = await assistant.ask(store.snapshot('alice', 'm'), 'SQLite has one writer and no broker', 'g', 'auto')
    assert one['challenge']['round'] == 1 and two['challenge']['round'] == 2 and one['challenge']['question'] != two['challenge']['question']
    assert 'SIMULATED mode does not judge your answer' in two['text'] and two['routing']['status'] == 'continued'
    review = await assistant.ask(store.snapshot('alice', 'm'), 'What could go wrong with the SQLite store?', 'r', 'auto')
    assert review['skill'] == 'design_review' and review['claims'] and review['inferences'] == []
