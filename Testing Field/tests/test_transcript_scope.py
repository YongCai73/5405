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
from meeting_copilot.retrieval import SCOPE_BUDGET, context_rows, older_rows, scoped_rows, time_scope
from meeting_copilot.store import Store
from meeting_copilot.trace import Trace

OPENING = ['Our speaker could not come today.', 'So instead you have two options.', 'Option one is a podcast about data centers.',
           'Option two is a town hall recording.']
CLOSING = ['That is it from me.', 'Questions?', 'Okay, enjoy your Thursday.']


class ScriptedProvider(Provider):
    def __init__(self, replies):
        super().__init__(Config(provider='openai', model='scripted', send_evidence=True))
        self.replies = list(replies); self.requests = []

    async def chat(self, messages, schemas=()):
        self.requests.append(json.loads(json.dumps(messages)))
        return self.replies.pop(0)

    def supplied(self):
        """The evidence texts of the last request, in the order the model saw them."""
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
    assert result['skill'] == 'catch_up' and provider.supplied() == lines
    system = provider.requests[-1][0]['content']
    assert 'Transcript coverage' in system and 'cover the whole meeting' in system
    assert [c['quote'] for c in result['claims']] == OPENING[:2] and not result['abstained']

    # The "Catch me up" button names no scope. A meeting this short is still handed over whole, in spoken order.
    caught_up = await assistant.ask(store.snapshot('alice', 'm'), 'Catch me up on the discussion.', 'd', 'catch_up')
    assert provider.supplied() == lines and [c['quote'] for c in caught_up['claims']] == OPENING[:2]


async def test_long_meeting_opening_and_recent_parts(store, tmp_path):
    lines = OPENING + [f'Segment {n:04d} ' + 'about hyperscale data center investment and power demand. ' * 2 for n in range(400)] + CLOSING
    fragments(store, lines)
    sid = store.snapshot('alice', 'm')
    reply = Completion(json.dumps({'claims': [quote(store, sid, OPENING[1])], 'inferences': [], 'general_answer': None}), 1, 1)
    provider = ScriptedProvider([reply, reply])
    assistant = Assistant(store, 'alice', provider, Trace(tmp_path / 't.jsonl'))
    await assistant.ask(sid, 'What happened at the beginning of the meeting?', 'c', 'auto')
    opening = provider.supplied()
    assert opening[:4] == OPENING and CLOSING[0] not in opening and sum(map(len, opening)) <= SCOPE_BUDGET // 2
    assert 'the opening part of the meeting only' in provider.requests[-1][0]['content']

    await assistant.ask(store.snapshot('alice', 'm'), 'Catch me up on the discussion.', 'd', 'catch_up')
    recent = provider.supplied()
    assert recent[-3:] == CLOSING and OPENING[0] not in recent
    # Far more than eight fragments, and bounded: about eight minutes of speech.
    assert 8 * 800 <= sum(map(len, recent)) <= 8 * 800 + 200 and 'the most recent part' in provider.requests[-1][0]['content']


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
    provider = ScriptedProvider([Completion(json.dumps({'claims': [quote(store, sid, KAFKA[3])], 'inferences': [],
                                                        'general_answer': None}), 1, 1)])
    result = await Assistant(store, 'alice', provider, Trace(tmp_path / 't.jsonl')).ask(sid, 'What happened with Kafka?', 'c', 'direct')
    # One fragment matches "Kafka"; the reason is in the next one. The model reads all five, in spoken order.
    assert provider.supplied() == KAFKA and 'cover the whole meeting' in provider.requests[-1][0]['content']
    assert [c['quote'] for c in result['claims']] == [KAFKA[3]]

    # The local mode still quotes only the rows that match the question.
    local = await Assistant(store, 'alice', Provider(Config()), Trace(tmp_path / 'l.jsonl')).ask(sid, 'What happened with Kafka?', 'd', 'direct')
    assert [c['quote'] for c in local['claims']] == [KAFKA[2]]
    # A question that matches nothing still retrieves nothing: there is no hit to give context to.
    nothing = await Assistant(store, 'alice', Provider(Config()), Trace(tmp_path / 'n.jsonl')).ask(sid, 'Cafeteria sandwich price?', 'e', 'direct')
    assert nothing['abstained'] and nothing['abstain_reason'] == 'no_evidence_retrieved'


async def test_keyword_hit_in_a_long_meeting_brings_its_neighbours(store, tmp_path):
    filler = [f'Filler remark number {n:03d} on an unrelated subject, long enough to count.' for n in range(300)]
    spoken(store, KAFKA + filler, KAFKA_STARTS + [120000 + n * 30000 for n in range(300)])
    sid = store.snapshot('alice', 'm')
    provider = ScriptedProvider([Completion(json.dumps({'claims': [quote(store, sid, KAFKA[3])], 'inferences': [],
                                                        'general_answer': None}), 1, 1)])
    result = await Assistant(store, 'alice', provider, Trace(tmp_path / 't.jsonl')).ask(sid, 'What happened with Kafka?', 'c', 'direct')
    # The hit, then what was said right before and after it. Not the line 46 seconds later, and no filler.
    assert provider.supplied() == [KAFKA[2], KAFKA[1], KAFKA[3]]
    assert 'Transcript coverage' not in provider.requests[-1][0]['content']
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
