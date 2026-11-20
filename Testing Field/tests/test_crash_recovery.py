"""Crash recovery for private Q&A (docs/CRASH_RECOVERY.md, cases C0-C4)."""
import pytest
from meeting_copilot.assistant import Assistant
from meeting_copilot.config import Config
from meeting_copilot.provider import Provider
from meeting_copilot.store import Denied
from meeting_copilot.trace import Trace

QUESTION = 'Why was SQLite chosen?'


class Crash(Exception):
    """Stands in for a process crash at a chosen point."""


class Counting(Assistant):
    """Counts graph steps and can crash once at a named point."""

    def __init__(self, store, user, tmp_path, crash=None):
        self.calls = {'retrieve': 0, 'answer': 0}
        self.crash = crash
        super().__init__(store, user, Provider(Config()), Trace(tmp_path / f'trace-{user}.jsonl'))

    def _fire(self, point):
        if self.crash == point:
            self.crash = None
            raise Crash(point)

    async def retrieve(self, state):
        self.calls['retrieve'] += 1
        self._fire('in_retrieve')            # C0: before the 'retrieved' checkpoint
        return await super().retrieve(state)

    async def answer(self, state):
        self.calls['answer'] += 1
        self._fire('before_answer')          # C1: 'retrieved' written, provider not called
        return await super().answer(state)

    def _checkpoint(self, state, step, evidence, result=None, turn_id=None):
        if step == 'answered':
            self._fire('before_answered')    # C2: provider finished, answer not yet stored
        return super()._checkpoint(state, step, evidence, result, turn_id)


@pytest.fixture
def sid(store, event):
    store.ingest('alice', 'm', event)
    return store.snapshot('alice', 'm')


def turns(store, sid, conversation='c'):
    return store.db.execute('SELECT COUNT(*) FROM conversation_turns WHERE snapshot=? AND conversation=?',
                            (sid, conversation)).fetchone()[0]


def step(store, sid, conversation='c'):
    saved = store.checkpoint_get('alice', sid, conversation)
    return saved and saved['step']


async def crash_then_resume(store, sid, tmp_path, point):
    a = Counting(store, 'alice', tmp_path, crash=point)
    with pytest.raises(Crash):
        await a.ask(sid, QUESTION, 'c')
    after_crash = step(store, sid)
    result = await a.ask(sid, QUESTION, 'c')
    return a, after_crash, result


async def test_normal_ask_checkpoints_every_step(store, sid, tmp_path):
    a = Counting(store, 'alice', tmp_path)
    result = await a.ask(sid, QUESTION, 'c')
    saved = store.checkpoint_get('alice', sid, 'c')
    assert saved['step'] == 'saved' and saved['result'] == result and saved['question'] == QUESTION
    assert saved['source_ids'] == [e['id'] for e in saved['evidence']]
    assert a.calls == {'retrieve': 1, 'answer': 1} and turns(store, sid) == 1


async def test_c0_crash_during_retrieve_restarts_retrieval(store, sid, tmp_path):
    a, after_crash, result = await crash_then_resume(store, sid, tmp_path, 'in_retrieve')
    assert after_crash is None
    assert a.calls == {'retrieve': 2, 'answer': 1}
    assert not result['abstained'] and turns(store, sid) == 1


async def test_c1_crash_after_retrieved_skips_retrieval(store, sid, tmp_path):
    a, after_crash, result = await crash_then_resume(store, sid, tmp_path, 'before_answer')
    assert after_crash == 'retrieved'
    assert a.calls == {'retrieve': 1, 'answer': 2}
    assert not result['abstained'] and turns(store, sid) == 1


async def test_c2_crash_before_answer_stored_calls_provider_again(store, sid, tmp_path):
    a, after_crash, result = await crash_then_resume(store, sid, tmp_path, 'before_answered')
    assert after_crash == 'retrieved'
    assert a.calls == {'retrieve': 1, 'answer': 2}
    assert turns(store, sid) == 1


async def test_c3_crash_before_turn_saved_does_not_call_provider_again(store, sid, tmp_path, monkeypatch):
    a = Counting(store, 'alice', tmp_path)
    original = store.private_put
    failures = {'left': 1}

    def flaky_private_put(*args, **kwargs):
        if failures['left']:
            failures['left'] -= 1
            raise Crash('before_saved')
        return original(*args, **kwargs)

    monkeypatch.setattr(store, 'private_put', flaky_private_put)
    with pytest.raises(Crash):
        await a.ask(sid, QUESTION, 'c')
    assert step(store, sid) == 'answered' and turns(store, sid) == 0
    first = store.checkpoint_get('alice', sid, 'c')['result']
    result = await a.ask(sid, QUESTION, 'c')
    assert result == first
    assert a.calls == {'retrieve': 1, 'answer': 1}
    assert turns(store, sid) == 1 and step(store, sid) == 'saved'


async def test_c4_resubmit_after_success_returns_stored_answer(store, sid, tmp_path):
    a = Counting(store, 'alice', tmp_path)
    first = await a.ask(sid, QUESTION, 'c')
    again = await a.ask(sid, QUESTION, 'c')
    assert again['text'] == first['text'] and again['claims'] == first['claims']
    assert again['resumed_from_checkpoint'] == 'saved'
    assert again['usage'] == {'input_tokens': 0, 'output_tokens': 0, 'estimated_cost_usd': 0.0}
    assert a.calls == {'retrieve': 1, 'answer': 1} and turns(store, sid) == 1


async def test_checkpoint_never_grants_access_to_another_user(store, sid, tmp_path):
    a, _, _ = await crash_then_resume(store, sid, tmp_path, 'before_answer')
    bob = Counting(store, 'bob', tmp_path)
    with pytest.raises(Denied):
        await bob.ask(sid, QUESTION, 'c')
    assert bob.calls == {'retrieve': 0, 'answer': 0}


async def test_different_question_starts_fresh(store, sid, tmp_path):
    a = Counting(store, 'alice', tmp_path, crash='before_answer')
    with pytest.raises(Crash):
        await a.ask(sid, QUESTION, 'c')
    await a.ask(sid, 'What was chosen for simplicity?', 'c')
    assert a.calls == {'retrieve': 2, 'answer': 2}
    assert store.checkpoint_get('alice', sid, 'c')['question'] == 'What was chosen for simplicity?'


async def test_old_format_checkpoint_is_ignored(store, sid, tmp_path):
    a = Counting(store, 'alice', tmp_path)
    store.checkpoint_put('alice', sid, 'c', {'snapshot': sid, 'question': QUESTION, 'skill': 'trace_decision',
                                            'source_ids': [], 'result': {'text': 'old'}, 'next': []})
    result = await a.ask(sid, QUESTION, 'c')
    assert result['text'] != 'old' and a.calls == {'retrieve': 1, 'answer': 1}
