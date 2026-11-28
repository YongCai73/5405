"""Retrieval measured in content, and questions about the whole meeting, its opening or its end.

Live transcription cuts speech into short fragments. A real ten-minute recording had 134 segments
with a median of 58 characters; "summarize the meeting" was answered from its last eight fragments.
"""
import json

import pytest

from meeting_copilot.assistant import Assistant
from meeting_copilot.config import Config
from meeting_copilot.language import bridged
from meeting_copilot.provider import Completion, Provider
from meeting_copilot.retrieval import (SCOPE_BUDGET, context_rows, older_rows, passages, same_word, scoped_rows, split_quote, time_scope,
                                       to_segments, window_rows, windows)
from meeting_copilot.store import Store
from meeting_copilot.trace import Trace

OPENING = ['Our speaker could not come today.', 'So instead you have two options.', 'Option one is a podcast about data centers.',
           'Option two is a town hall recording.']
CLOSING = ['That is it from me.', 'Questions?', 'Okay, enjoy your Thursday.']


class ScriptedProvider(Provider):
    def __init__(self, replies):
        super().__init__(Config(provider='openai', model='scripted', send_evidence=True, tool_rounds=0, research_retries=0))
        self.replies = list(replies); self.requests = []

    async def chat(self, messages, schemas=()):
        self.requests.append(json.loads(json.dumps(messages)))
        return self.replies.pop(0)

    def supplied(self):
        """The passages of the last request, in the order the model saw them."""
        block = self.requests[-1][0]['content'].split('<meeting_evidence>')[1].split('</meeting_evidence>')[0]
        return [row['text'] for row in json.loads(block)]


def fragments(store, lines, gap_ms=0):
    store.create('alice', 'm', 'Seminar')
    clock = 0
    for number, text in enumerate(lines, 1):
        store.ingest('alice', 'm', {'event_id': f'e{number}', 'stream': 'capture', 'seq': number, 'segment_id': f's{number}',
                                    'revision': 1, 'start_ms': clock, 'end_ms': clock + 2000, 'text': text, 'final': True})
        clock += 2000 + gap_ms


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / 'evidence.db')
    yield store
    store.close()


def quote(store, sid, text):
    row = next(row for row in store.evidence('alice', sid, kind='segment', limit=100) if row['text'] == text)
    return {'source_id': row['id'], 'quote': text}


@pytest.mark.parametrize('question,scope', [
    ('What happened at the beginning of the seminar?', 'opening'), ('How did the meeting start?', 'opening'),
    ('What was said at the end of the meeting?', 'closing'), ('Summarize this meeting', 'overview'),
    ('What were the main points?', 'overview'), ('Explain the lecture to me', 'overview'),
    ('请向我概括这个会议的主要内容', 'overview'), ('解释一下会议开头讲了什么', 'opening'), ('会议最后说了什么？', 'closing'),
    ('Catch me up on the discussion.', None), ('Why was Kafka rejected?', None), ('What do previous meetings require?', None),
    ('What is the launch budget?', None), ('谁负责评估？', None)])
def test_questions_about_a_part_of_the_meeting_are_recognised(question, scope):
    assert time_scope(bridged(question)) == scope


def test_scope_selection_is_measured_in_characters():
    rows = [{'id': n, 'text': 'x' * 100, 'start_ms': n * 1000, 'end_ms': n * 1000 + 900} for n in range(400)]
    short, coverage = scoped_rows(rows[:50], 'opening')
    assert len(short) == 50 and coverage == 'the whole meeting'          # fits: handed over whole
    opening, coverage = scoped_rows(rows, 'opening')
    assert [r['id'] for r in opening] == list(range(SCOPE_BUDGET // 2 // 100)) and 'opening part' in coverage
    closing, _ = scoped_rows(rows, 'closing')
    assert closing[-1]['id'] == 399 and len(closing) == SCOPE_BUDGET // 2 // 100
    overview, coverage = scoped_rows(rows, 'overview')
    ids = [r['id'] for r in overview]
    assert ids == sorted(ids) and ids[0] == 0 and ids[-1] > 350 and sum(len(r['text']) for r in overview) <= SCOPE_BUDGET
    assert 'evenly spaced' in coverage
    # "N recent segments" grows backwards until it holds enough speech.
    extra = older_rows(rows[-8:], rows, 8 * 800)
    assert [r['id'] for r in extra] == list(range(336, 392))
    # A fragment hit brings neighbours spoken right around it, and stops at a pause.
    spaced = [dict(r, start_ms=r['id'] * 1000 + (60000 if r['id'] >= 12 else 0), end_ms=r['id'] * 1000 + 900 + (60000 if r['id'] >= 12 else 0)) for r in rows[:20]]
    assert [r['id'] for r in context_rows([spaced[10]], spaced, {10})] == [9, 8, 11]


async def test_summary_of_a_fragmentary_meeting_sees_the_whole_transcript_in_order(store, tmp_path):
    lines = OPENING + [f'Detail number {n} about cooling.' for n in range(40)] + CLOSING
    fragments(store, lines)
    sid = store.snapshot('alice', 'm')
    reply = Completion(json.dumps({'claims': [quote(store, sid, OPENING[0]), quote(store, sid, OPENING[1])],
                                   'inferences': [], 'general_answer': None}), 1, 1)
    provider = ScriptedProvider([reply, reply])
    assistant = Assistant(store, 'alice', provider, Trace(tmp_path / 't.jsonl'))
    result = await assistant.ask(sid, 'Summarize this meeting', 'c', 'auto')
    # Routed to catch_up, whose contract says "8 recent segments": the last eight fragments are the goodbye.
    # The model reads passages of consecutive fragments; together they are the whole transcript in order.
    assert result['skill'] == 'catch_up' and ' '.join(provider.supplied()) == ' '.join(lines)
    assert 1 < len(provider.supplied()) < len(lines) / 4
    system = provider.requests[-1][0]['content']
    assert 'Transcript coverage' in system and 'cover the whole meeting' in system
    assert [c['quote'] for c in result['claims']] == OPENING[:2] and not result['abstained']

    # The "Catch me up" button names no scope. A meeting this short is still handed over whole, in spoken order.
    caught_up = await assistant.ask(store.snapshot('alice', 'm'), 'Catch me up on the discussion.', 'd', 'catch_up')
    assert ' '.join(provider.supplied()) == ' '.join(lines) and [c['quote'] for c in caught_up['claims']] == OPENING[:2]


async def test_long_meeting_opening_and_recent_parts(store, tmp_path):
    lines = OPENING + [f'Segment {n:04d} ' + 'about hyperscale data center investment and power demand. ' * 2 for n in range(400)] + CLOSING
    fragments(store, lines)
    sid = store.snapshot('alice', 'm')
    reply = Completion(json.dumps({'claims': [quote(store, sid, OPENING[1])], 'inferences': [], 'general_answer': None}), 1, 1)
    provider = ScriptedProvider([reply, reply])
    assistant = Assistant(store, 'alice', provider, Trace(tmp_path / 't.jsonl'))
    await assistant.ask(sid, 'What happened at the beginning of the meeting?', 'c', 'auto')
    opening = ' '.join(provider.supplied())
    assert opening.startswith(' '.join(OPENING)) and CLOSING[0] not in opening and len(opening) <= SCOPE_BUDGET // 2 + 100
    assert 'the opening part of the meeting only' in provider.requests[-1][0]['content']

    await assistant.ask(store.snapshot('alice', 'm'), 'Catch me up on the discussion.', 'd', 'catch_up')
    recent = ' '.join(provider.supplied())
    assert recent.endswith(' '.join(CLOSING)) and OPENING[0] not in recent
    # Far more than eight fragments, and bounded: about eight minutes of speech.
    assert 8 * 800 <= len(recent) <= 8 * 800 + 300 and 'the most recent part' in provider.requests[-1][0]['content']


def spoken(store, lines, starts):
    store.create('alice', 'm', 'Seminar')
    for number, (text, start) in enumerate(zip(lines, starts), 1):
        store.ingest('alice', 'm', {'event_id': f'e{number}', 'stream': 'capture', 'seq': number, 'segment_id': f's{number}',
                                    'revision': 1, 'start_ms': start, 'end_ms': start + 2000, 'text': text, 'final': True})


KAFKA = ['We looked at three vendors.', 'In the end we rejected', 'Kafka for the prototype', 'because a broker adds work.',
         'Lunch is at noon.']
KAFKA_STARTS = [0, 10000, 12100, 14200, 60000]            # the three middle fragments are one sentence


async def test_keyword_question_on_a_short_meeting_reads_the_whole_transcript(store, tmp_path):
    spoken(store, KAFKA, KAFKA_STARTS)
    sid = store.snapshot('alice', 'm')
    ids = [quote(store, sid, text)['source_id'] for text in KAFKA]
    # The model quotes one sentence that transcription cut into three fragments, and cites the passage.
    sentence = 'we rejected Kafka for the prototype because a broker adds work.'
    provider = ScriptedProvider([Completion(json.dumps({
        'claims': [{'source_id': ids[1], 'quote': sentence}],
        'inferences': [{'text': 'Kafka was rejected because of the broker.', 'source_ids': [ids[1]], 'field': 'evidence'}],
        'general_answer': None}), 1, 1)])
    result = await Assistant(store, 'alice', provider, Trace(tmp_path / 't.jsonl')).ask(sid, 'What happened with Kafka?', 'c', 'direct')
    # One fragment matches "Kafka"; the reason is in the next one. The model reads the whole short meeting as
    # passages: the three fragments spoken back to back are one passage, the other two stand alone.
    assert provider.supplied() == [KAFKA[0], ' '.join(KAFKA[1:4]), KAFKA[4]]
    assert 'cover the whole meeting' in provider.requests[-1][0]['content']
    # The quotation comes back as one exact piece per original segment, so each citation can be checked.
    assert result['claims'] == [{'source_id': ids[1], 'quote': 'we rejected'}, {'source_id': ids[2], 'quote': KAFKA[2]},
                                {'source_id': ids[3], 'quote': KAFKA[3]}]
    assert [c['source_id'] for c in result['citations']] == ids[1:4] and all(c['kind'] == 'segment' for c in result['citations'])
    assert result['inferences'][0]['source_ids'] == ids[1:4] and result['validation_report']['claims_rejected'] == 0

    # The local mode still quotes only the rows that match the question.
    local = await Assistant(store, 'alice', Provider(Config()), Trace(tmp_path / 'l.jsonl')).ask(sid, 'What happened with Kafka?', 'd', 'direct')
    assert [c['quote'] for c in local['claims']] == [KAFKA[2]]
    # A question that matches nothing still retrieves nothing: there is no hit to give context to.
    nothing = await Assistant(store, 'alice', Provider(Config()), Trace(tmp_path / 'n.jsonl')).ask(sid, 'Cafeteria sandwich price?', 'e', 'direct')
    assert nothing['abstained'] and nothing['abstain_reason'] == 'no_evidence_retrieved'


async def test_keyword_question_on_a_long_meeting_reads_the_matching_time_windows(store, tmp_path):
    filler = [f'Filler remark number {n:03d} on an unrelated subject, long enough to count.' for n in range(300)]
    spoken(store, KAFKA + filler, KAFKA_STARTS + [120000 + n * 30000 for n in range(300)])
    sid = store.snapshot('alice', 'm')
    provider = ScriptedProvider([Completion(json.dumps({'claims': [quote(store, sid, KAFKA[3])], 'inferences': [],
                                                        'general_answer': None}), 1, 1)])
    result = await Assistant(store, 'alice', provider, Trace(tmp_path / 't.jsonl')).ask(sid, 'What happened with Kafka?', 'c', 'direct')
    # Too long to read whole. The question is matched against 45-second windows: the window around the hit
    # is supplied as passages, in spoken order. Not the line 46 seconds later, and no filler.
    assert provider.supplied() == [KAFKA[0], ' '.join(KAFKA[1:4])]
    assert 'stretches of the meeting that match the question' in provider.requests[-1][0]['content']
    assert [c['quote'] for c in result['claims']] == [KAFKA[3]]


async def test_transcript_context_off_restores_the_segment_limits(store, tmp_path):
    lines = OPENING + [f'Detail number {n} about cooling.' for n in range(40)] + CLOSING
    fragments(store, lines)
    sid = store.snapshot('alice', 'm')
    provider = ScriptedProvider([Completion(json.dumps({'claims': [], 'inferences': [], 'general_answer': 'nothing'}), 1, 1)])
    provider.config.transcript_context = False
    await Assistant(store, 'alice', provider, Trace(tmp_path / 't.jsonl')).ask(sid, 'Summarize this meeting', 'c', 'auto')
    # The strict behaviour that the API evaluation keeps: the last eight fragments and nothing else.
    assert provider.supplied() == lines[-8:] and 'Transcript coverage' not in provider.requests[-1][0]['content']
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'transcript_context': 'no'}), encoding='utf-8')
    with pytest.raises(ValueError, match='transcript_context'):
        Config.load(path)


async def test_quotations_that_inferences_cite_are_kept_first(store, tmp_path):
    lines = [f'Point {n} was made.' for n in range(1, 21)]
    fragments(store, lines)
    sid = store.snapshot('alice', 'm')
    claims = [quote(store, sid, text) for text in lines[:14]]
    cited = claims[12]['source_id']
    reply = Completion(json.dumps({'claims': claims, 'inferences': [
        {'text': 'The thirteenth point matters most.', 'source_ids': [cited], 'field': 'recent_evidence'}], 'general_answer': None}), 1, 1)
    provider = ScriptedProvider([reply])
    result = await Assistant(store, 'alice', provider, Trace(tmp_path / 't.jsonl')).ask(sid, 'Summarize this meeting', 'c', 'catch_up')
    assert len(result['claims']) == 8 and result['claims'][0]['source_id'] == cited
    assert [i['text'] for i in result['inferences']] == ['The thirteenth point matters most.']


async def test_unusable_reply_with_evidence_is_reported_and_not_replaced_by_a_guess(store, tmp_path):
    fragments(store, OPENING + CLOSING)
    sid = store.snapshot('alice', 'm')
    provider = ScriptedProvider([Completion('{"claims": [{"source_id": 1, "quote": "Our speaker could', 1, 1)])   # cut off
    result = await Assistant(store, 'alice', provider, Trace(tmp_path / 't.jsonl')).ask(sid, 'Summarize this meeting', 'c', 'auto')
    # One request only: asking for a plain answer here would tell the user the record is empty.
    assert len(provider.requests) == 1 and 'general_answer' not in result and result['abstained']
    assert result['text'] == ('Insufficient evidence in this authorized snapshot.\n\n'
                              'Note: The model reply could not be used (it was cut off or not in the expected format). Ask again.')


def timed(texts, step_ms=5000, length_ms=4000):
    return [{'id': n + 1, 'kind': 'segment', 'meeting': 'm', 'text': text, 'start_ms': n * step_ms, 'end_ms': n * step_ms + length_ms}
            for n, text in enumerate(texts)]


def test_windows_overlap_and_rank_by_terms_spread_over_fragments():
    texts = ['Welcome everyone.'] * 12 + ['so the launch', 'budget is', 'five hundred dollars'] + ['Unrelated remark.'] * 30 \
        + ['launch launch launch party'] + ['More unrelated talk.'] * 30
    rows = timed(texts)
    spans = windows(rows)
    # 45-second windows starting every 30 seconds: each shares its last third with the next one.
    assert [row['id'] for row in spans[0]] == list(range(1, 10)) and [row['id'] for row in spans[1]] == list(range(7, 16))
    assert all(spans[n][-1]['id'] >= spans[n + 1][0]['id'] for n in range(len(spans) - 1))
    # No single fragment holds both "launch" and "budget". The window that holds both wins over the fragment
    # that repeats one of them, and the two overlapping windows around it merge into one stretch.
    found = [row['text'] for row in window_rows(rows, 'What is the launch budget?', budget=600)]
    assert found[:3] != ['launch launch launch party'] and 'budget is' in found and 'five hundred dollars' in found
    assert 'launch launch launch party' not in found
    assert window_rows(rows, 'Completely different words', budget=600) == []
    # Word forms need not match exactly: "budgets" and "launching" find the same window.
    assert 'budget is' in [row['text'] for row in window_rows(rows, 'launching budgets', budget=600)]
    assert same_word('submit', 'submission') and same_word('students', 'student') and same_word('notes', 'note')
    assert not same_word('interest', 'internal') and not same_word('hand', 'handle') and not same_word('data', 'date')
    assert window_rows([], 'launch') == [] and windows([]) == []


def test_passages_join_only_speech_that_ran_together():
    rows = timed(['the the budget', 'is is five', 'hundred dollars'], step_ms=2100, length_ms=2000)
    later = {'id': 9, 'kind': 'segment', 'meeting': 'm', 'text': 'Next topic.', 'start_ms': 60000, 'end_ms': 62000}
    file_row = {'id': 20, 'kind': 'file', 'meeting': 'm', 'text': 'BUDGET = 500'}
    blocks = passages([later, file_row] + rows[::-1])
    # A stuttered sentence cut into three fragments reads as one passage, named after its first fragment.
    assert [(block['id'], block['text']) for block in blocks] == [
        (20, 'BUDGET = 500'), (1, 'the the budget is is five hundred dollars'), (9, 'Next topic.')]
    stutter = blocks[1]
    assert split_quote(stutter, 'budget is is five hundred dollars') == [
        {'source_id': 1, 'quote': 'budget'}, {'source_id': 2, 'quote': 'is is five'}, {'source_id': 3, 'quote': 'hundred dollars'}]
    assert split_quote(stutter, 'the budget is five hundred dollars') == []          # tidied up: not what was said
    # A long run of speech is cut into passages of bounded length.
    long_run = passages(timed(['word ' * 20] * 60, step_ms=2100, length_ms=2000))
    assert len(long_run) > 3 and all(len(block['text']) <= 1200 for block in long_run)


def test_reply_citing_passages_is_rewritten_onto_segments():
    rows = timed(['the the budget', 'is is five', 'hundred dollars'], step_ms=2100, length_ms=2000)
    blocks = passages(rows + [{'id': 20, 'kind': 'file', 'meeting': 'm', 'text': 'BUDGET = 500'}])
    reply = {'claims': [{'source_id': 1, 'quote': 'is is five hundred dollars'}, {'source_id': 20, 'quote': 'BUDGET = 500'},
                        {'source_id': 1, 'quote': 'a tidy paraphrase'}, 'not a claim'],
             'inferences': [{'text': 'The budget is five hundred dollars.', 'source_ids': [1, 20], 'field': 'evidence'}],
             'general_answer': None}
    rewritten = to_segments(reply, blocks)
    assert rewritten['claims'] == [{'source_id': 2, 'quote': 'is is five'}, {'source_id': 3, 'quote': 'hundred dollars'},
                                   {'source_id': 20, 'quote': 'BUDGET = 500'}, {'source_id': 1, 'quote': 'a tidy paraphrase'},
                                   'not a claim']
    # The inference now cites the segments its quotation came from; the paraphrase is left for validation to reject.
    assert rewritten['inferences'][0]['source_ids'] == [2, 3, 20] and rewritten['general_answer'] is None
    # Nothing to rewrite when every passage is a single segment.
    assert to_segments(reply, passages(rows[:1])) is reply
