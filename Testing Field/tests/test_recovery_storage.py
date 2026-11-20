"""Durability, replay safety and authorization for the storage recovery contract."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
import threading

import pytest

from meeting_copilot.retrieval import excerpt
from meeting_copilot.store import Denied, Store


@pytest.fixture
def recovery(store, event):
    rid = store.ingest('alice', 'm', event)
    sid = store.snapshot('alice', 'm')
    row = excerpt(store.raw('alice', sid, rid), 'SQLite')
    state = {
        'version': 1,
        'snapshot': sid,
        'question': 'Why SQLite?',
        'skill': 'trace_decision',
        'step': 'retrieved',
        'source_ids': [rid],
        'evidence': [row],
    }
    result = {
        'snapshot_id': sid,
        'skill': 'trace_decision',
        'text': f"{event['text']} [E{rid}]",
        'claims': [{'source_id': rid, 'quote': event['text']}],
        'citations': [],
    }
    return sid, state, result


def test_private_replay_returns_original_turn_and_answer(store, recovery):
    sid, state, result = recovery
    first = store.private_put('alice', sid, 'thread', state['question'], result)
    repeated = store.private_put('alice', sid, 'thread', state['question'], {'text': 'replacement'})
    assert type(first) is int and first > 0
    assert repeated == first
    assert store.private_get('alice', sid, 'thread') == [{'question': state['question'], 'answer': result}]
    rows = store.db.execute('SELECT * FROM conversation_turns').fetchall()
    assert len(rows) == 1 and rows[0]['id'] == first
    assert json.loads(rows[0]['answer']) == result


def test_private_identity_keeps_other_questions_conversations_and_snapshots(store, recovery):
    sid, state, result = recovery
    first = store.private_put('alice', sid, 'a', state['question'], result)
    other_question = store.private_put('alice', sid, 'a', 'A different question', result)
    other_conversation = store.private_put('alice', sid, 'b', state['question'], result)
    fresh = store.snapshot('alice', 'm')
    other_snapshot = store.private_put('alice', fresh, 'a', state['question'], {'text': 'new task'})
    assert len({first, other_question, other_conversation, other_snapshot}) == 4


def test_private_replay_survives_reopen(tmp_path, event):
    path = tmp_path / 'runtime/evidence.db'
    first = Store(path)
    first.create('alice', 'm', 'Meeting')
    first.ingest('alice', 'm', event)
    sid = first.snapshot('alice', 'm')
    turn = first.private_put('alice', sid, 'c', 'q', {'text': 'first'})
    first.close()
    resumed = Store(path)
    try:
        assert resumed.private_put('alice', sid, 'c', 'q', {'text': 'retry'}) == turn
        assert resumed.private_get('alice', sid, 'c')[0]['answer']['text'] == 'first'
        assert resumed.db.execute('SELECT COUNT(*) FROM conversation_turns').fetchone()[0] == 1
    finally:
        resumed.close()


def test_private_double_submit_from_independent_connections(tmp_path):
    path = tmp_path / 'runtime/evidence.db'
    one = Store(path)
    one.create('alice', 'm', 'Meeting')
    sid = one.snapshot('alice', 'm')
    two = Store(path)
    start = threading.Barrier(2)

    def save(store, text):
        start.wait(timeout=10)
        return store.private_put('alice', sid, 'c', 'q', {'text': text})

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(save, one, 'one')
            b = pool.submit(save, two, 'two')
            ids = [a.result(timeout=15), b.result(timeout=15)]
        assert type(ids[0]) is int and ids[0] == ids[1]
        rows = one.db.execute('SELECT * FROM conversation_turns').fetchall()
        assert len(rows) == 1
        assert one.private_get('alice', sid, 'c')[0]['answer'] == json.loads(rows[0]['answer'])
    finally:
        two.close()
        one.close()


def test_private_answer_and_turn_rollback_together(store, recovery):
    sid, state, result = recovery
    with store.tx() as db:
        db.execute("""CREATE TRIGGER fail_turn BEFORE INSERT ON conversation_turns
                      BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='simulated storage failure'):
        store.private_put('alice', sid, 'thread', state['question'], result)
    assert store.private_get('alice', sid, 'thread') == []
    assert store.db.execute('SELECT COUNT(*) FROM conversation_turns').fetchone()[0] == 0
    with store.tx() as db:
        db.execute('DROP TRIGGER fail_turn')
    assert type(store.private_put('alice', sid, 'thread', state['question'], result)) is int


def test_upgrade_deduplicates_old_turns_without_moving_history_cutoffs(tmp_path):
    path = tmp_path / 'runtime/evidence.db'
    old = Store(path)
    old.create('alice', 'm', 'Meeting')
    sid = old.snapshot('alice', 'm')
    first = old.private_put('alice', sid, 'c', 'q', {'text': 'first committed answer'})
    pinned = old.snapshot('alice', 'm')
    created = old.db.execute('SELECT created FROM conversation_turns WHERE id=?', (first,)).fetchone()[0]
    with old.tx() as db:
        # Reconstruct a database written by the previous, non-idempotent version.
        for index in db.execute('PRAGMA index_list(conversation_turns)').fetchall():
            if index['unique'] and index['origin'] == 'c':
                db.execute('DROP INDEX "' + index['name'].replace('"', '""') + '"')
        db.execute('DELETE FROM app_meta')
        duplicate = db.execute(
            'INSERT INTO conversation_turns(snapshot,user,conversation,question,answer,created) VALUES(?,?,?,?,?,?)',
            (sid, 'alice', 'c', 'q', json.dumps({'text': 'later duplicate answer'}), created + 100),
        ).lastrowid
        db.execute('UPDATE private SET answer=? WHERE snapshot=?', (json.dumps({'text': 'later duplicate answer'}), sid))
    old.close()

    for _ in range(2):
        upgraded = Store(path)
        try:
            rows = upgraded.db.execute('SELECT * FROM conversation_turns').fetchall()
            assert len(rows) == 1
            assert rows[0]['id'] == first and rows[0]['created'] == created
            assert rows[0]['id'] != duplicate
            expected = {'text': 'first committed answer'}
            assert upgraded.private_get('alice', sid, 'c')[0]['answer'] == expected
            history = upgraded.conversation_history('alice', pinned, 'c')
            assert [r['turn_id'] for r in history] == [first]
            assert history[0]['answer'] == expected
            assert upgraded.conversation_history('alice', sid, 'c') == []
            assert upgraded.private_put('alice', sid, 'c', 'q', {'text': 'yet another retry'}) == first
        finally:
            upgraded.close()


def test_recovery_states_persist_across_reopen(store, recovery):
    sid, state, result = recovery
    store.checkpoint_put('alice', sid, 'thread', state)
    path = store.root / 'evidence.db'
    other = Store(path)
    try:
        assert other.checkpoint_get('alice', sid, 'thread')['step'] == 'retrieved'
        assert other.checkpoint_get('alice', sid, 'thread')['evidence'] == state['evidence']
        answered = {**state, 'step': 'answered', 'result': result}
        other.checkpoint_put('alice', sid, 'thread', answered)
        assert store.checkpoint_get('alice', sid, 'thread')['result'] == result
        turn = other.private_put('alice', sid, 'thread', state['question'], result)
        other.checkpoint_put('alice', sid, 'thread', {**answered, 'step': 'saved', 'turn_id': turn})
        saved = store.checkpoint_get('alice', sid, 'thread')
        assert saved['step'] == 'saved' and saved['turn_id'] == turn
        assert saved['result'] == result
    finally:
        other.close()


@pytest.mark.parametrize('step', ['retrieve', 'complete', '', None, True, 3, ['retrieved']])
def test_checkpoint_rejects_invalid_step_without_overwriting(store, recovery, step):
    sid, state, _ = recovery
    store.checkpoint_put('alice', sid, 'c', state)
    with pytest.raises(ValueError):
        store.checkpoint_put('alice', sid, 'c', {**state, 'step': step})
    assert store.checkpoint_get('alice', sid, 'c')['step'] == 'retrieved'


def test_checkpoint_rejects_regression_for_same_question_and_skill(store, recovery):
    sid, state, result = recovery
    answered = {**state, 'step': 'answered', 'result': result}
    store.checkpoint_put('alice', sid, 'c', answered)
    with pytest.raises(ValueError):
        store.checkpoint_put('alice', sid, 'c', state)
    assert store.checkpoint_get('alice', sid, 'c')['step'] == 'answered'


@pytest.mark.parametrize('change', [{'question': 'What else?'}, {'skill': 'catch_up'}])
def test_new_question_or_skill_can_start_new_checkpoint(store, recovery, change):
    sid, state, result = recovery
    store.checkpoint_put('alice', sid, 'c', {**state, 'step': 'answered', 'result': result})
    fresh = {**state, **change}
    store.checkpoint_put('alice', sid, 'c', fresh)
    actual = store.checkpoint_get('alice', sid, 'c')
    assert actual['question'] == fresh['question'] and actual['skill'] == fresh['skill']
    assert actual['step'] == 'retrieved'


def test_existing_assistant_checkpoint_payload_remains_compatible(store, recovery):
    sid, state, result = recovery
    legacy = {key: value for key, value in state.items() if key not in ('step', 'version', 'evidence')}
    legacy.update(result=result, next=[])
    store.checkpoint_put('alice', sid, 'thread', legacy)
    resumed = store.checkpoint_get('alice', sid, 'thread')
    assert resumed['result'] == result and resumed['source_ids'] == state['source_ids']
    assert resumed['step'] == 'answered'


@pytest.mark.parametrize('missing', [True, False])
def test_retrieved_checkpoint_requires_persisted_evidence(store, recovery, missing):
    sid, state, _ = recovery
    incomplete = dict(state)
    if missing:
        incomplete.pop('evidence')
    else:
        incomplete['evidence'] = None
    with pytest.raises(ValueError, match='requires evidence'):
        store.checkpoint_put('alice', sid, 'thread', incomplete)
    assert store.checkpoint_get('alice', sid, 'thread') is None


def test_checkpoint_saved_requires_a_real_matching_turn(store, recovery):
    sid, state, result = recovery
    answered = {**state, 'step': 'answered', 'result': result}
    store.checkpoint_put('alice', sid, 'c', answered)
    with pytest.raises((ValueError, Denied)):
        store.checkpoint_put('alice', sid, 'c', {**answered, 'step': 'saved', 'turn_id': 999})
    assert store.checkpoint_get('alice', sid, 'c')['step'] == 'answered'
    turn = store.private_put('alice', sid, 'different-thread', state['question'], result)
    with pytest.raises((ValueError, Denied)):
        store.checkpoint_put('alice', sid, 'c', {**answered, 'step': 'saved', 'turn_id': turn})


def test_checkpoint_saved_cannot_disagree_with_canonical_answer(store, recovery):
    sid, state, result = recovery
    turn = store.private_put('alice', sid, 'c', state['question'], result)
    forged = {**state, 'step': 'saved', 'turn_id': turn, 'result': {**result, 'text': 'different answer'}}
    with pytest.raises((ValueError, Denied)):
        store.checkpoint_put('alice', sid, 'c', forged)


@pytest.mark.parametrize('field,value', [('text', 'forged evidence'), ('hash', 'bad hash'), ('revision', 999), ('text_offset', -1), ('line_offset', 4)])
def test_checkpoint_rejects_forged_excerpt(store, recovery, field, value):
    sid, state, _ = recovery
    forged = deepcopy(state)
    forged['evidence'][0][field] = value
    with pytest.raises((ValueError, Denied)):
        store.checkpoint_put('alice', sid, 'c', forged)
    assert store.checkpoint_get('alice', sid, 'c') is None


def test_checkpoint_rejects_evidence_outside_snapshot_even_if_source_ids_look_safe(store, recovery, event):
    sid, state, _ = recovery
    late_id = store.ingest('alice', 'm', {**event, 'event_id': 'late', 'segment_id': 'late', 'seq': 2})
    newer = store.snapshot('alice', 'm')
    forged = {**state, 'evidence': [store.raw('alice', newer, late_id)]}
    with pytest.raises((ValueError, Denied)):
        store.checkpoint_put('alice', sid, 'c', forged)


def test_checkpoint_read_rechecks_retained_evidence_after_payload_tampering(store, recovery):
    sid, state, _ = recovery
    store.checkpoint_put('alice', sid, 'c', state)
    forged = deepcopy(state)
    forged['evidence'][0]['text'] = 'tampered checkpoint text'
    with store.tx() as db:
        db.execute('UPDATE checkpoints SET state=? WHERE snapshot=? AND user=? AND conversation=?',
                   (json.dumps(forged), sid, 'alice', 'c'))
    with pytest.raises((ValueError, Denied)):
        store.checkpoint_get('alice', sid, 'c')


@pytest.mark.parametrize('change', [
    {'snapshot_id': 'another-snapshot'},
    {'claims': [{'source_id': 999999, 'quote': 'outside scope'}]},
    {'claims': [{'source_id': None, 'quote': 'missing source'}]},
    {'claims': [{'source_id': True, 'quote': 'boolean source ID'}]},
])
def test_answered_checkpoint_rejects_forged_result_scope(store, recovery, change):
    sid, state, result = recovery
    with pytest.raises((ValueError, Denied)):
        store.checkpoint_put('alice', sid, 'c', {**state, 'step': 'answered', 'result': {**result, **change}})
    assert store.checkpoint_get('alice', sid, 'c') is None


def test_checkpoint_cannot_rewrite_a_source_quotation(store, recovery):
    sid, state, result = recovery
    forged = {**result, 'claims': [{'source_id': state['source_ids'][0], 'quote': 'This was never said.'}]}
    with pytest.raises((ValueError, Denied)):
        store.checkpoint_put('alice', sid, 'c', {**state, 'step': 'answered', 'result': forged})


def test_checkpoint_restores_only_pinned_history_from_the_same_conversation(store, recovery):
    sid, state, result = recovery
    store.private_put('alice', sid, 'c', state['question'], result)
    fresh = store.snapshot('alice', 'm')
    history = store.conversation_history('alice', fresh, 'c')
    checkpoint = {**state, 'snapshot': fresh, 'history': history}
    store.checkpoint_put('alice', fresh, 'c', checkpoint)
    assert store.checkpoint_get('alice', fresh, 'c')['history'] == history
    forged = deepcopy(checkpoint)
    forged['history'][0]['answer']['text'] = 'injected private history'
    with pytest.raises((ValueError, Denied)):
        store.checkpoint_put('alice', fresh, 'c', forged)
    with pytest.raises((ValueError, Denied)):
        store.checkpoint_put('alice', fresh, 'other-thread', checkpoint)
    with pytest.raises((ValueError, Denied)):
        store.checkpoint_put('alice', sid, 'c', {**state, 'history': history})


def test_checkpoint_keeps_original_evidence_after_new_transcript_revision(store, recovery, event):
    sid, state, _ = recovery
    store.checkpoint_put('alice', sid, 'c', state)
    store.ingest('alice', 'm', {**event, 'event_id': 'revision', 'seq': 2, 'revision': 2, 'text': 'A later correction.'})
    resumed = store.checkpoint_get('alice', sid, 'c')
    assert resumed['evidence'] == state['evidence']
    assert resumed['evidence'][0]['text'] == event['text']


def test_revoked_meeting_denies_checkpoint_read_write_and_private_replay(store, event):
    store.ingest('alice', 'm', event)
    store.grant('alice', 'm', 'bob')
    sid = store.snapshot('bob', 'm')
    row = store.evidence('bob', sid)[0]
    state = {'snapshot': sid, 'question': 'q', 'skill': 'direct', 'step': 'retrieved', 'source_ids': [row['id']], 'evidence': [row]}
    store.checkpoint_put('bob', sid, 'c', state)
    store.private_put('bob', sid, 'c', 'q', {'text': 'private answer'})
    store.grant('alice', 'm', 'bob', False)
    operations = [
        lambda: store.checkpoint_get('bob', sid, 'c'),
        lambda: store.checkpoint_put('bob', sid, 'c', state),
        lambda: store.private_put('bob', sid, 'c', 'q', {'text': 'retry'}),
    ]
    for operation in operations:
        with pytest.raises(Denied):
            operation()


def test_revoked_folder_denies_checkpoint_read_and_write(store, tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'notes.md').write_text('SQLite keeps data local.', encoding='utf-8')
    folder = store.approve('alice', 'm', project)
    store.refresh('alice', folder)
    sid = store.snapshot('alice', 'm', folders=[folder])
    row = store.evidence('alice', sid)[0]
    state = {'snapshot': sid, 'question': 'q', 'skill': 'direct', 'step': 'retrieved', 'source_ids': [row['id']], 'evidence': [row]}
    store.checkpoint_put('alice', sid, 'c', state)
    store.revoke_folder('alice', folder)
    with pytest.raises(Denied):
        store.checkpoint_get('alice', sid, 'c')
    with pytest.raises(Denied):
        store.checkpoint_put('alice', sid, 'c', state)


def test_another_authorized_user_cannot_read_private_checkpoint(store, recovery):
    sid, state, _ = recovery
    store.checkpoint_put('alice', sid, 'c', state)
    store.grant('alice', 'm', 'bob')
    with pytest.raises(Denied):
        store.checkpoint_get('bob', sid, 'c')


def test_meeting_cleanup_removes_checkpoint_and_dedupe_record(store, recovery):
    sid, state, result = recovery
    store.checkpoint_put('alice', sid, 'c', state)
    store.private_put('alice', sid, 'c', state['question'], result)
    store.cleanup('alice', 'm')
    for table in ('checkpoints', 'private', 'conversation_turns'):
        assert store.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    with pytest.raises(Denied):
        store.checkpoint_get('alice', sid, 'c')
