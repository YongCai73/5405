"""The agentic loops: the model's tool loop (on by default) and the re-search cycle in the graph.

answer -> rewrite -> retrieve -> answer: an answer that cites no evidence asks the model for
different search terms and searches again, a bounded number of times.
"""
import json

import pytest

from meeting_copilot.assistant import Assistant
from meeting_copilot.config import Config
from meeting_copilot.language import REWRITE_PROMPT
from meeting_copilot.provider import Completion, Provider, ProviderError
from meeting_copilot.store import Store
from meeting_copilot.trace import Trace

FILLER = [f'Filler remark number {n:03d} on an unrelated subject, long enough to count.' for n in range(300)]
FACT = 'Importantly, what I am asking for is just your raw notes.'


class ScriptedProvider(Provider):
    def __init__(self, replies, **config):
        super().__init__(Config(provider='openai', model='scripted', send_evidence=True, **{'tool_rounds': 0, **config}))
        self.replies = list(replies); self.requests = []

    async def chat(self, messages, schemas=()):
        self.requests.append({'messages': json.loads(json.dumps(messages)), 'tools': [s['function']['name'] for s in schemas]})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def kinds(self):
        """Which step each request belonged to."""
        return ['rewrite' if r['messages'][0]['content'].startswith(REWRITE_PROMPT[:40]) else 'answer' for r in self.requests]


@pytest.fixture
def long_meeting(tmp_path):
    """Too long to read whole, so only matching time windows reach the model."""
    store = Store(tmp_path / 'evidence.db')
    store.create('alice', 'm', 'Seminar')
    for number, text in enumerate(FILLER[:150] + [FACT] + FILLER[150:], 1):
        store.ingest('alice', 'm', {'event_id': f'e{number}', 'stream': 'capture', 'seq': number, 'segment_id': f's{number}',
                                    'revision': 1, 'start_ms': number * 30000, 'end_ms': number * 30000 + 4000, 'text': text, 'final': True})
    yield store
    store.close()


def nothing(general='I could not find that in the record.'):
    return Completion(json.dumps({'claims': [], 'inferences': [], 'general_answer': general}), 3, 2)


def grounded(store, sid):
    row = next(r for r in store.evidence('alice', sid, query='raw notes', limit=5) if r['text'] == FACT)
    return Completion(json.dumps({'claims': [{'source_id': row['id'], 'quote': FACT}], 'inferences': [], 'general_answer': None}), 5, 4)


def test_defaults_turn_both_loops_on_and_are_validated(tmp_path):
    assert (Config().tool_rounds, Config().research_retries) == (1, 1)
    path = tmp_path / 'config.json'
    for bad in ({'research_retries': 6}, {'research_retries': -1}, {'research_retries': '1'}, {'tool_rounds': 3}):
        path.write_text(json.dumps(bad), encoding='utf-8')
        with pytest.raises(ValueError):
            Config.load(path)
    path.write_text(json.dumps({'research_retries': 5, 'tool_rounds': 2}), encoding='utf-8')
    assert (Config.load(path).research_retries, Config.load(path).tool_rounds) == (5, 2)


async def test_answer_without_evidence_rewrites_the_question_and_searches_again(long_meeting, tmp_path):
    sid = long_meeting.snapshot('alice', 'm')
    provider = ScriptedProvider([nothing(), Completion('Notes raw, hand-in!', 2, 3), grounded(long_meeting, sid)])
    result = await Assistant(long_meeting, 'alice', provider, Trace(tmp_path / 't.jsonl')).ask(sid, 'What is the homework?', 'c', 'direct')
    assert provider.kinds() == ['answer', 'rewrite', 'answer']
    # "homework" is never said, so the first pass had nothing to read.
    assert '<meeting_evidence>\n[]' in provider.requests[0]['messages'][0]['content']
    rewrite = provider.requests[1]['messages']
    # The rewrite request names the terms already tried and carries the question only.
    assert '"homework"' in rewrite[0]['content'] and rewrite[1] == {'role': 'user', 'content': 'What is the homework?'}
    assert FACT not in json.dumps(rewrite)
    # The second pass searched with the model's terms and found what was actually said.
    assert FACT in provider.requests[2]['messages'][0]['content']
    assert [c['quote'] for c in result['claims']] == [FACT] and not result['abstained']
    assert result['research'] == {'attempts': 1, 'found_evidence': True, 'searches': ['notes raw hand-in']}
    events = [json.loads(line)['event'] for line in (tmp_path / 't.jsonl').read_text(encoding='utf-8').splitlines()]
    assert events.count('retrieval') == 2 and events.count('answer') == 2 and 'research' in events
    # The stored turn is the final answer.
    assert long_meeting.private_get('alice', sid, 'c', 'What is the homework?')[0]['answer']['claims'] == result['claims']


async def test_loop_stops_when_the_model_has_no_other_terms_or_the_search_finds_nothing_new(long_meeting, tmp_path):
    sid = long_meeting.snapshot('alice', 'm')
    # The model says the question is not about a meeting at all: no second search, no second answer.
    provider = ScriptedProvider([nothing('Paris is the capital of France.'), Completion('NONE', 1, 1)])
    result = await Assistant(long_meeting, 'alice', provider, Trace(tmp_path / 'a.jsonl')).ask(sid, 'What is the capital of France?', 'a', 'direct')
    assert provider.kinds() == ['answer', 'rewrite'] and result['general_answer']['text'] == 'Paris is the capital of France.'
    assert result['research'] == {'attempts': 1, 'found_evidence': False, 'searches': []}

    # New terms that match nothing either: the first answer stands, without asking the model again.
    provider = ScriptedProvider([nothing(), Completion('cafeteria sandwich price', 1, 1)])
    result = await Assistant(long_meeting, 'alice', provider, Trace(tmp_path / 'b.jsonl')).ask(sid, 'What is the homework?', 'b', 'direct')
    assert provider.kinds() == ['answer', 'rewrite'] and result['grounding'] == 'model_only'
    assert result['research']['searches'] == ['cafeteria sandwich price'] and not result['research']['found_evidence']

    # A provider failure while rewriting ends the loop the same way.
    provider = ScriptedProvider([nothing(), ProviderError('timeout')])
    result = await Assistant(long_meeting, 'alice', provider, Trace(tmp_path / 'c.jsonl')).ask(sid, 'What is the homework?', 'd', 'direct')
    assert provider.kinds() == ['answer', 'rewrite'] and result['general_answer']


async def test_retries_are_bounded_by_the_setting(long_meeting, tmp_path):
    sid = long_meeting.snapshot('alice', 'm')
    # Two retries allowed: the first new search finds unrelated filler, the second finds the fact.
    provider = ScriptedProvider([nothing(), Completion('filler remark', 1, 1), nothing(), Completion('raw notes', 1, 1),
                                 grounded(long_meeting, sid)], research_retries=2)
    result = await Assistant(long_meeting, 'alice', provider, Trace(tmp_path / 'a.jsonl')).ask(sid, 'What is the homework?', 'a', 'direct')
    assert provider.kinds() == ['answer', 'rewrite', 'answer', 'rewrite', 'answer']
    assert result['research']['attempts'] == 2 and [c['quote'] for c in result['claims']] == [FACT]

    # With one retry the same script stops after the second answer.
    provider = ScriptedProvider([nothing(), Completion('filler remark', 1, 1), nothing('Still nothing.')], research_retries=1)
    result = await Assistant(long_meeting, 'alice', provider, Trace(tmp_path / 'b.jsonl')).ask(sid, 'What is the homework?', 'b', 'direct')
    assert provider.kinds() == ['answer', 'rewrite', 'answer'] and result['abstained'] and result['research']['attempts'] == 1

    # Zero turns the loop off; so does the local mode, which has no model to rewrite with.
    provider = ScriptedProvider([nothing()], research_retries=0)
    result = await Assistant(long_meeting, 'alice', provider, Trace(tmp_path / 'c.jsonl')).ask(sid, 'What is the homework?', 'c', 'direct')
    assert provider.kinds() == ['answer'] and 'research' not in result
    local = await Assistant(long_meeting, 'alice', Provider(Config()), Trace(tmp_path / 'd.jsonl')).ask(sid, 'What is the homework?', 'd', 'direct')
    assert local['abstained'] and 'research' not in local


async def test_a_grounded_answer_does_not_loop_and_the_tool_loop_is_offered_by_default(long_meeting, tmp_path):
    sid = long_meeting.snapshot('alice', 'm')
    provider = ScriptedProvider([grounded(long_meeting, sid)], tool_rounds=Config().tool_rounds)
    result = await Assistant(long_meeting, 'alice', provider, Trace(tmp_path / 't.jsonl')).ask(sid, 'What raw notes are asked for?', 'c', 'direct')
    assert provider.kinds() == ['answer'] and 'research' not in result and not result['abstained']
    # With the default configuration the model is offered the read-only evidence tools.
    assert provider.requests[0]['tools'] == ['search_evidence', 'read_evidence']
