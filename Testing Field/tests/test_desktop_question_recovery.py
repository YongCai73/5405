"""Exercise the real desktop transport and SQLite with a controlled model response."""

from contextlib import contextmanager
import json
from pathlib import Path
import queue
import subprocess
import sys
from threading import Thread
import time

from meeting_copilot.store import Store


APP = Path(__file__).resolve().parents[1]
QUESTION = {'meetingId': 'design', 'question': 'Why SQLite?', 'skill': 'trace_decision'}
MODEL_FIXTURE = '''
import asyncio, json, sys, threading
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'desktop'))
from bridge import DesktopBridge, serve
from meeting_copilot.config import Config
from meeting_copilot.provider import Completion

root = Path(sys.argv[1])
app = DesktopBridge(Config(_base=root, principal='viewer', provider='openai', send_evidence=True))

async def respond(question, evidence, contract, **kwargs):
    (root / 'provider-entered').write_text(str(len(evidence)))
    while not (root / 'allow-response').exists():
        await asyncio.sleep(0.01)
    claim = {'source_id': evidence[0]['id'], 'quote': evidence[0]['text']}
    return Completion(json.dumps({'claims': [claim]}))

class CaptureFixture:
    meeting = 'design'
    finished = threading.Event()
    error = None
    recording_status = {}
    def stop(self, **kwargs):
        self.finished.set()

if sys.argv[2] == 'recording':
    app.capture = CaptureFixture()
app.assistant.provider.complete = respond
try:
    serve(app, sys.stdout)
finally:
    app.close()
'''


def wait_for(predicate, timeout=8):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError('Expected desktop state did not appear')


@contextmanager
def desktop(root, *, recording=False):
    process = subprocess.Popen(
        [sys.executable, '-u', '-c', MODEL_FIXTURE, str(root), 'recording' if recording else 'idle'],
        cwd=APP, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, encoding='utf-8',
    )
    replies = queue.Queue()
    def read_replies():
        for line in process.stdout:
            replies.put(json.loads(line))

    reader = Thread(target=read_replies, daemon=True)
    reader.start()
    request_id = 0

    def request(action, payload):
        nonlocal request_id
        request_id += 1
        process.stdin.write(json.dumps({'id': request_id, 'action': action, 'payload': payload}) + '\n')
        process.stdin.flush()
        reply = replies.get(timeout=8)
        assert reply['id'] == request_id
        return reply

    try:
        yield process, request
    finally:
        if process.poll() is None:
            (root / 'allow-response').touch()
            process.stdin.close()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=8)
        reader.join(timeout=8)
        process.stdout.close()
        if not process.stdin.closed:
            process.stdin.close()


def seed(root):
    store = Store(root / 'data' / 'evidence.db')
    store.create('owner', 'design', 'Synthetic design meeting')
    store.grant('owner', 'design', 'viewer')
    store.ingest('owner', 'design', {
        'event_id': 'early', 'segment_id': 'early', 'seq': 1, 'revision': 1,
        'start_ms': 0, 'end_ms': 900, 'text': 'SQLite runs locally.', 'final': True,
    })
    return store


def turns(request):
    reply = request('questionHistory', {'meetingId': 'design'})
    assert reply['ok'], reply
    return reply['result']['turns']


def test_desktop_controls_remain_responsive_while_answer_is_in_flight(tmp_path):
    store = seed(tmp_path)
    try:
        with desktop(tmp_path, recording=True) as (_, request):
            submitted = request('beginQuestion', QUESTION)
            assert submitted['ok']
            wait_for((tmp_path / 'provider-entered').exists)
            assert turns(request)[0]['pending']
            assert request('meeting', {'meetingId': 'design'})['result']['recording']
            assert not request('stopRecording', {'meetingId': 'design'})['result']['recording']
            (tmp_path / 'allow-response').touch()
            result = wait_for(lambda: turns(request)[0].get('answer'))
            assert result['citations'][0]['quote'] == 'SQLite runs locally.'
    finally:
        store.close()


def test_reopen_resumes_held_question_with_original_evidence_and_one_saved_turn(tmp_path):
    store = seed(tmp_path)
    try:
        with desktop(tmp_path) as (process, request):
            snapshot = request('beginQuestion', QUESTION)['result']['id']
            wait_for((tmp_path / 'provider-entered').exists)
            process.kill()
            process.wait(timeout=8)
        store.ingest('owner', 'design', {
            'event_id': 'late', 'segment_id': 'late', 'seq': 2, 'revision': 1,
            'start_ms': 950, 'end_ms': 1100, 'text': 'SQLite late note.', 'final': True,
        })
        (tmp_path / 'allow-response').touch()
        with desktop(tmp_path) as (_, request):
            assert turns(request) == [{'id': snapshot, 'question': QUESTION['question'], 'resumable': True}]
            assert request('resumeQuestion', {'meetingId': 'design', 'questionId': snapshot})['ok']
            result = wait_for(lambda: turns(request)[0].get('answer'))
            assert result['citations'][0]['quote'] == 'SQLite runs locally.'
            assert (tmp_path / 'provider-entered').read_text() == '1'
        with desktop(tmp_path) as (_, request):
            assert turns(request)[0]['answer'] == result
            assert request('resumeQuestion', {'meetingId': 'design', 'questionId': snapshot})['ok']
        assert len(store.private_get('viewer', snapshot, 'desktop-private')) == 1
    finally:
        store.close()


def test_revoked_prior_scope_hides_completed_and_incomplete_questions(tmp_path):
    store = seed(tmp_path)
    try:
        store.create('owner', 'prior', 'Synthetic prior meeting')
        store.grant('owner', 'prior', 'viewer')
        scoped_question = {**QUESTION, 'previous': ['prior']}
        (tmp_path / 'allow-response').touch()
        with desktop(tmp_path) as (_, request):
            saved = request('beginQuestion', scoped_question)['result']['id']
            wait_for(lambda: any(turn.get('answer') for turn in turns(request)))
        (tmp_path / 'allow-response').unlink()
        (tmp_path / 'provider-entered').unlink()
        with desktop(tmp_path) as (process, request):
            unfinished = request('beginQuestion', scoped_question)['result']['id']
            wait_for((tmp_path / 'provider-entered').exists)
            process.kill()
            process.wait(timeout=8)
        store.grant('owner', 'prior', 'viewer', False)
        with desktop(tmp_path) as (_, request):
            assert request('meeting', {'meetingId': 'design'})['ok']
            assert turns(request) == []
            for snapshot in (saved, unfinished):
                assert not request('resumeQuestion', {'meetingId': 'design', 'questionId': snapshot})['ok']
    finally:
        store.close()
