"""Local desktop transport for the existing Meeting Copilot Python core."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import uuid

from meeting_copilot.desktop_questions import DesktopQuestions, QueueFull
from meeting_copilot.skill_runtime import AUTO, MANUAL, load_contract


CONVERSATION = 'desktop-private'
SAMPLE_ID = 'demo'
SAMPLE_SOURCE_TITLE = 'Atlas technical design (SIMULATED REPLAY)'
# Every skill contract shipped with the backend is selectable; a new contract needs no desktop change.
SKILLS = {AUTO, *MANUAL}
MAX_REQUEST_BYTES = 64 * 1024
CAPTURE_START_TIMEOUT_SECONDS = 60
PROVIDER_LABELS = {'demo': 'Local extractive', 'openai': 'OpenAI-compatible', 'anthropic': 'Anthropic'}
PROVIDER_MESSAGES = {
    'evidence_upload_not_enabled': 'Enable send_evidence in the desktop configuration to use the configured API.',
    'missing_api_key': 'No API key is available for the configured provider. Add one in Settings.',
    'timeout': 'The configured API did not respond before the timeout.',
    'authentication': 'The provider rejected the API key. Check it in Settings.',
    'badrequest': 'The provider rejected the request. Check the model name and base URL in Settings.',
    'connection': 'The provider could not be reached. Check the base URL in Settings and your network.',
    'ratelimit': 'The provider is rate limiting this key or the account has no balance. Try again later.',
    'invalid_minutes_response': 'The configured API did not return usable cited meeting notes. Try again.',
}
# Choices offered in Settings. The DeepSeek values are the ones recorded in
# results/live-deepseek-validation-2026-11-27.md; the others are starting points to edit.
MODEL_PRESETS = (
    {'id': 'local', 'label': 'Local only (no model)', 'provider': 'demo', 'model': '', 'baseUrl': ''},
    {'id': 'deepseek', 'label': 'DeepSeek', 'provider': 'openai', 'model': 'deepseek-flash',
     'baseUrl': 'https://api.deepseek.com/v1'},
    {'id': 'openai', 'label': 'OpenAI', 'provider': 'openai', 'model': '', 'baseUrl': ''},
    {'id': 'anthropic', 'label': 'Anthropic', 'provider': 'anthropic', 'model': 'claude-fable-5-2', 'baseUrl': ''},
    {'id': 'custom', 'label': 'Other OpenAI-compatible', 'provider': 'openai', 'model': '', 'baseUrl': ''},
)
SUGGESTION_ACTIONS = {
    'preference': 'Save it as a wording preference.',
    'use_existing_skill': 'Choose this skill in the question type menu beside the question box.',
    'no_change': 'Keep asking, and rate answers so that repeated patterns become visible.',
}
MINUTES_SECTIONS = {
    'overview': 'Overview', 'topics': 'Discussion topics', 'decisions': 'Decisions',
    'actionItems': 'Action items', 'openQuestions': 'Open questions',
}
SESSION_HEALTH_COUNTS = {
    'processed', 'dropped_segments', 'errors', 'speech_to_ready_count', 'capture_stalls',
    'input_overflows', 'junk_segments_dropped', 'silent_chunks_skipped', 'quiet_chunks_sent',
}
SESSION_HEALTH_SECONDS = {
    'speech_to_ready_mean_s', 'speech_to_ready_p50_s', 'speech_to_ready_p95_s', 'speech_to_ready_max_s',
    'ingestion_lag_p95_seconds', 'ingestion_lag_max_seconds', 'wall_seconds', 'captured_audio_seconds',
    'capture_wall_seconds', 'capture_missing_seconds', 'longest_stall_seconds', 'chunk_seconds',
}
MAX_SESSION_HEALTH_COUNT = 10_000_000
MAX_SESSION_HEALTH_SECONDS = 31 * 24 * 60 * 60


class DesktopError(Exception):
    """An error whose message is safe to display in the desktop application."""


def required_text(payload, key, limit):
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DesktopError(f'Enter a valid {key}.')
    return value.strip()


def scope_ids(payload, key):
    values = payload.get(key, [])
    if (not isinstance(values, list) or len(values) > 10
            or any(not isinstance(value, str) or not value.strip() or len(value) > 80 for value in values)):
        raise DesktopError(f'Choose valid {key} for this question.')
    return list(dict.fromkeys(value.strip() for value in values))


def selected_path(payload):
    value = payload.get('path')
    if not isinstance(value, str) or not value or len(value) > 4096 or '\0' in value:
        raise DesktopError('Choose a valid file path.')
    path = Path(value)
    if not path.is_absolute():
        raise DesktopError('Choose an absolute file path.')
    return path.resolve()


def timestamp(milliseconds):
    minutes, seconds = divmod((milliseconds or 0) // 1000, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


def write_json(stream, value):
    print(json.dumps(value, ensure_ascii=True, separators=(',', ':')), file=stream, flush=True)


def session_health(payload):
    """Return the non-content, bounded metrics that may leave a capture process."""
    if not isinstance(payload, dict):
        return {}
    health = {}
    for key in SESSION_HEALTH_COUNTS:
        value = payload.get(key)
        if type(value) is int and 0 <= value <= MAX_SESSION_HEALTH_COUNT:
            health[key] = value
    for key in SESSION_HEALTH_SECONDS:
        value = payload.get(key)
        if (type(value) in (int, float) and math.isfinite(value)
                and 0 <= value <= MAX_SESSION_HEALTH_SECONDS):
            health[key] = round(value, 3)
    return health


def minutes_text(minutes):
    sections = []
    for key, heading in MINUTES_SECTIONS.items():
        lines = []
        for item in minutes.get(key, []):
            details = [f'{label}: {item[field]}' for field, label in [('owner', 'Owner'), ('due', 'Due')]
                       if item.get(field)]
            citations = ' '.join(f"[E{source['id']}]" for source in item['sources'])
            suffix = f" ({', '.join(details)})" if details else ''
            lines.append(f"- {item['text']}{suffix} {citations}".rstrip())
        if lines:
            sections.append(f"{heading}\n" + '\n'.join(lines))
    return '\n\n'.join(sections)


class Capture:
    def __init__(self, root, meeting, device, offset, principal, model, *, low_latency=False):
        self.meeting = meeting
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.started = False
        self.error = None
        self.health = None
        self.stop_requested = False
        self._status_lock = threading.Lock()
        self._recording_status = {
            'preset': 'low-latency' if low_latency else 'default',
            'asrThreads': 4 if low_latency else 2,
            'pauseMs': 300 if low_latency else 500,
            'stalled': False,
            'secondsWithoutAudio': 0,
            'warning': '',
        }
        self.stop_file = Path(root) / f'.capture-stop-{uuid.uuid4().hex}'
        command = [sys.executable, '-u', str(Path(__file__).resolve()), '--data-dir', str(root),
                   '--capture', meeting, device, str(offset), str(os.getpid()), principal, model,
                   '--capture-stop-file', str(self.stop_file)]
        if low_latency:
            command.append('--low-latency')
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            start_new_session=True,
            text=True,
            encoding='utf-8',
        )
        self.reader = threading.Thread(target=self.read_status, name='desktop-capture-status', daemon=True)
        self.reader.start()

    @property
    def recording_status(self):
        with self._status_lock:
            return dict(self._recording_status)

    def _update_status(self, status):
        with self._status_lock:
            current = dict(self._recording_status)
            event = status.get('event')
            if event == 'started':
                if status.get('preset') in ('default', 'low-latency'):
                    current['preset'] = status['preset']
                threads, pause = status.get('asr_threads'), status.get('pause_ms')
                if type(threads) is int and 1 <= threads <= 16:
                    current['asrThreads'] = threads
                if type(pause) in (int, float) and math.isfinite(pause) and 100 <= pause <= 3000:
                    current['pauseMs'] = pause
            elif event in ('capture_stalled', 'capture_resumed'):
                seconds = status.get('seconds_without_audio', 0)
                current['secondsWithoutAudio'] = (
                    seconds if type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0 else 0
                )
                current['stalled'] = event == 'capture_stalled'
                warning = status.get('warning')
                current['warning'] = (
                    warning[:1000] if isinstance(warning, str) and warning
                    else 'No microphone audio is arriving. Check the connection; recording will resume when audio returns.'
                ) if current['stalled'] else ''
            elif event == 'recording_finished':
                self.health = session_health(status.get('health'))
            self._recording_status = current

    def read_status(self):
        try:
            with self.process.stdout as stream:
                for line in stream:
                    status = json.loads(line)
                    if not isinstance(status, dict):
                        raise ValueError('Invalid recording status.')
                    self._update_status(status)
                    if status.get('event') == 'started':
                        self.started = True
                        self.ready.set()
                    if status.get('error'):
                        self.error = status['error']
            if self.process.wait() != 0 and not self.error:
                self.error = 'Recording stopped unexpectedly.'
        except (OSError, ValueError):
            self.error = 'Could not read recording status.'
        finally:
            self.ready.set()
            self.finished.set()

    def stop(self, *, closing=False):
        if not self.stop_requested and self.process.poll() is None:
            self.stop_requested = True
            try:
                self.stop_file.touch(exist_ok=True)
            except OSError:
                self.process.terminate()
        if self.finished.wait(8 if closing else 15):
            self.stop_file.unlink(missing_ok=True)
            return
        if not closing:
            raise DesktopError('Recording is still finishing transcription. Try again in a moment.')
        self.process.terminate()
        if not self.finished.wait(3):
            self.process.kill()
            self.finished.wait(3)
        self.stop_file.unlink(missing_ok=True)


class OffsetStore:
    """Put a new capture after audio already present on the meeting timeline."""

    def __init__(self, store, offset):
        self.store = store
        self.offset = offset

    @property
    def lock(self):
        return self.store.lock

    def _check(self, *args):
        return self.store._check(*args)

    def ingest(self, user, meeting, event):
        shifted = {**event, 'start_ms': event['start_ms'] + self.offset, 'end_ms': event['end_ms'] + self.offset}
        return self.store.ingest(user, meeting, shifted)


def configure_cache(root):
    os.environ['HF_HOME'] = str(root / 'cache' / 'huggingface')
    os.environ['XDG_CACHE_HOME'] = str(root / 'cache')
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'


def parent_is_alive(parent_pid):
    """Check whether the desktop host that owns a capture process is alive.

    ``os.getppid()`` is not reliable for a Windows child launched through the
    desktop host: Windows can report the console launcher instead of the Python
    bridge process. Query the supplied process handle there, while retaining the
    parent relationship check used by the POSIX capture path.
    """
    if not isinstance(parent_pid, int) or parent_pid <= 0:
        return False
    if os.name != 'nt':
        return os.getppid() == parent_pid
    process_query_limited_information = 0x1000
    still_active = 259
    handle = ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information, False, parent_pid,
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def capture_audio(root, stream, meeting, device, offset, parent_pid, principal, model, *, low_latency=False,
                  stop_file=None):
    from meeting_copilot.audio import listen
    from meeting_copilot.store import Store
    from meeting_copilot.trace import Trace

    store = Store(root / 'evidence.db')
    stop_path = Path(stop_file) if stop_file else None

    def should_stop():
        return not parent_is_alive(parent_pid) or (stop_path is not None and stop_path.exists())

    def on_status(status):
        if should_stop():
            raise KeyboardInterrupt
        if status['event'] == 'recording_started':
            write_json(stream, {**status, 'event': 'started'})
        elif status['event'] in ('capture_stalled', 'capture_resumed'):
            write_json(stream, status)

    try:
        if should_stop():
            return 0
        result = listen(OffsetStore(store, offset), principal, meeting, root, device, 28800, True,
                        model=model, download=True, trace=Trace(root / 'traces.jsonl'), on_status=on_status,
                        low_latency=low_latency)
        write_json(stream, {'event': 'recording_finished', 'health': session_health(result)})
        if result['errors'] or result['dropped_segments']:
            write_json(stream, {'error': 'Some audio could not be transcribed. Review the transcript for gaps.'})
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        write_json(stream, {'error': 'Could not record from this microphone. Check system microphone access and the selected device.'})
        return 1
    finally:
        store.close()


class DesktopBridge:
    def __init__(self, config, config_path=None):
        from meeting_copilot.assistant import Assistant, Workers
        from meeting_copilot.config import Config
        from meeting_copilot.provider import Provider
        from meeting_copilot.store import Store
        from meeting_copilot.trace import Trace

        self.config = config
        # Where Settings saves the model choice; the workspace default when none was given.
        self.config_path = Path(config_path) if config_path else Path(config._base) / 'config.json'
        self.root = config.root
        self.principal = config.principal
        self.store = Store(self.root / 'evidence.db')
        with self.store.tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS desktop_minutes (
                snapshot TEXT PRIMARY KEY REFERENCES snapshots(id) ON DELETE CASCADE,
                transcript_refs TEXT NOT NULL,
                content TEXT NOT NULL
            )''')
            db.execute('''CREATE TABLE IF NOT EXISTS desktop_session_health (
                meeting TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
                content TEXT NOT NULL
            )''')
        self.trace = Trace(self.root / 'traces.jsonl')
        self.assistant = Assistant(self.store, self.principal, Provider(config), self.trace)
        self.local_assistant = self.assistant if config.provider == 'demo' else Assistant(
            self.store, self.principal, Provider(Config()), self.trace,
        )
        self.workers = Workers(self.assistant)
        self.live_summaries = None
        self.capture = None
        self.recording_errors = {}
        self.sample_id = SAMPLE_ID
        with self.store.read() as db:
            reserved = db.execute('SELECT title, owner FROM meetings WHERE id=?', (SAMPLE_ID,)).fetchone()
            if reserved and (reserved['title'] != SAMPLE_SOURCE_TITLE or reserved['owner'] != self.principal):
                self.sample_id = f'desktop-sample-{uuid.uuid5(uuid.NAMESPACE_OID, self.principal).hex}'
        self.questions = DesktopQuestions(self.store, self.principal, CONVERSATION, self._assistant_for)

    def close(self):
        try:
            self._stop_live_summaries()
            if self.capture and not self.capture.finished.is_set():
                self.capture.stop(closing=True)
        finally:
            try:
                self.questions.close()
            finally:
                self.workers.close()
                self.store.close()

    def run(self, action, payload):
        handlers = {
            'bootstrap': self.bootstrap,
            'demo': self.demo,
            'create': self.create,
            'meeting': self.meeting,
            'ask': self.ask,
            'beginQuestion': self.begin_question,
            'resumeQuestion': self.resume_question,
            'questionHistory': self.question_history,
            'summary': self.summary,
            'export': self.export,
            'importAudio': self.import_audio,
            'devices': self.devices,
            'startRecording': self.start_recording,
            'stopRecording': self.stop_recording,
            'skills': self.skills,
            'feedback': self.feedback,
            'preferences': self.preferences,
            'suggest': self.suggest,
            'settings': self.settings,
            'saveModelSettings': self.save_model_settings,
            'testModel': self.test_model,
            'deleteApiKey': self.delete_api_key,
        }
        if not isinstance(action, str) or action not in handlers:
            raise DesktopError('Unknown desktop action.')
        try:
            return handlers[action](payload)
        except QueueFull:
            raise DesktopError('The question queue is full. Wait for an answer before trying again.') from None

    def _meetings(self):
        with self.store.read() as db:
            rows = db.execute(
                'SELECT m.id, m.title FROM meetings m JOIN acl a ON m.id=a.meeting '
                'WHERE a.user=? ORDER BY m.rowid DESC', (self.principal,),
            )
            return [self._meeting_header(dict(row)) for row in rows]

    def _meeting_header(self, row):
        sample = row['id'] == self.sample_id
        title = 'Atlas technical design' if sample else row['title']
        return {
            'id': row['id'], 'title': title, 'mode': 'sample' if sample else 'local',
            **self._answer_mode(row['id']),
        }

    def _sample_is_local(self):
        # The sample needs no setup. Once a model is chosen and sharing is allowed, it uses that model too.
        return self.config.provider == 'demo' or not self.config.send_evidence

    def _answer_mode(self, meeting):
        provider = 'demo' if meeting == self.sample_id and self._sample_is_local() else self.config.provider
        return {'answerMode': 'local' if provider == 'demo' else 'api', 'providerLabel': PROVIDER_LABELS[provider]}

    def _scope(self, meeting, payload=None):
        requested = payload or {}
        previous = [value for value in scope_ids(requested, 'previous') if value != meeting]
        folders = scope_ids(requested, 'folders')
        if meeting == self.sample_id:
            if previous:
                raise DesktopError('The sample meeting can only use its own evidence.')
            if 'folders' not in requested:
                folders = [folder['id'] for folder in self.store.folders(self.principal, meeting)]
        elif self.sample_id in previous:
            raise DesktopError('Choose a real meeting to include in the evidence scope.')
        if len(previous) >= 10:
            raise DesktopError('Choose up to nine previous meetings.')
        return self.store.snapshot(self.principal, meeting, previous=previous, folders=folders)

    def _current_segments(self, meeting):
        # Polling the transcript should not create persistent question snapshots.
        with self.store.read() as db:
            self.store._check(self.principal, meeting)
            rows = db.execute(
                "SELECT e.id, e.text, e.start_ms, e.end_ms FROM evidence e WHERE e.meeting=? AND e.kind='segment' "
                "AND e.revision=(SELECT MAX(v.revision) FROM evidence v WHERE v.meeting=e.meeting "
                "AND v.kind=e.kind AND v.logical=e.logical) ORDER BY e.start_ms, e.id",
                (meeting,),
            )
            return [{'id': row['id'], 'text': row['text'], 'startMs': row['start_ms'], 'endMs': row['end_ms']}
                    for row in rows]

    def _offset(self, meeting):
        with self.store.read() as db:
            self.store._check(self.principal, meeting, owner=True)
            return db.execute('SELECT COALESCE(MAX(end_ms), 0) FROM evidence WHERE meeting=?', (meeting,)).fetchone()[0]

    def _current_capture(self):
        capture = self.capture
        if capture and capture.finished.is_set():
            self._stop_live_summaries()
            if capture.error:
                self.recording_errors[capture.meeting] = capture.error
            if getattr(capture, 'health', None) is not None:
                status = capture.recording_status
                health = {
                    **capture.health,
                    'preset': status['preset'],
                    'asrThreads': status['asrThreads'],
                    'pauseMs': status['pauseMs'],
                }
                self._save_session_health(capture.meeting, health)
            self.capture = None
        return self.capture

    def _stop_live_summaries(self):
        if self.live_summaries:
            self.live_summaries.close()
            self.live_summaries = None

    def _save_session_health(self, meeting, health):
        with self.store.tx() as db:
            self.store.metadata(self.principal, meeting)
            db.execute('''INSERT INTO desktop_session_health(meeting, content) VALUES (?, ?)
                          ON CONFLICT(meeting) DO UPDATE SET content=excluded.content''',
                       (meeting, json.dumps(health, ensure_ascii=False, allow_nan=False)))

    def _saved_session_health(self, meeting):
        with self.store.read() as db:
            self.store.metadata(self.principal, meeting)
            row = db.execute('SELECT content FROM desktop_session_health WHERE meeting=?', (meeting,)).fetchone()
        if row is None:
            return None
        try:
            stored = json.loads(row['content'])
        except (TypeError, json.JSONDecodeError):
            return None
        health = session_health(stored)
        if isinstance(stored, dict) and stored.get('preset') in ('default', 'low-latency'):
            health['preset'] = stored['preset']
        if isinstance(stored, dict) and type(stored.get('asrThreads')) is int and 1 <= stored['asrThreads'] <= 16:
            health['asrThreads'] = stored['asrThreads']
        if (isinstance(stored, dict) and type(stored.get('pauseMs')) in (int, float)
                and math.isfinite(stored['pauseMs']) and 100 <= stored['pauseMs'] <= 3000):
            health['pauseMs'] = stored['pauseMs']
        return health or None

    def _detail(self, meeting):
        capture = self._current_capture()
        recording = bool(capture and capture.meeting == meeting)
        with self.store.read():
            row = self.store.metadata(self.principal, meeting)
            segments = self._current_segments(meeting)
            minutes = None if recording else self._saved_minutes(meeting, [row['id'] for row in segments])
            summary = minutes or self._latest_live_summary(meeting)
        detail = {
            **self._meeting_header(row),
            'segments': segments,
            'summary': summary['text'] if summary else '',
            'minutes': minutes['minutes'] if minutes else None,
            'recording': recording,
        }
        if recording:
            detail['recordingStatus'] = capture.recording_status
        if meeting in self.recording_errors:
            detail['recordingError'] = self.recording_errors[meeting]
        health = None if recording else self._saved_session_health(meeting)
        if health:
            detail['sessionHealth'] = health
        return detail

    def _selection(self, meeting):
        return {'meetings': self._meetings(), 'meeting': self._detail(meeting)}

    def bootstrap(self, _payload):
        meetings = self._meetings()
        if not meetings:
            return self.demo({})
        return {'meetings': meetings, 'meeting': self._detail(meetings[0]['id'])}

    def demo(self, _payload):
        from meeting_copilot.replay import seed

        with self.store.tx():
            existing = any(row['id'] == self.sample_id for row in self._meetings())
            if not existing or not self.store.folders(self.principal, self.sample_id):
                seed(self.store, self.principal, self.root, meeting=self.sample_id)
        return self._selection(self.sample_id)

    def create(self, payload):
        meeting = uuid.uuid4().hex
        self.store.create(self.principal, meeting, required_text(payload, 'title', 200))
        return self._selection(meeting)

    def meeting(self, payload):
        return self._detail(required_text(payload, 'meetingId', 80))

    def _assistant_for(self, meeting):
        return self.local_assistant if meeting == self.sample_id and self._sample_is_local() else self.assistant

    def _new_question(self, payload):
        meeting = required_text(payload, 'meetingId', 80)
        question = required_text(payload, 'question', 4000)
        skill = payload.get('skill', 'trace_decision')
        if not isinstance(skill, str) or skill not in SKILLS:
            raise DesktopError('Choose a supported question type.')
        with self.store.tx():
            snapshot = self._scope(meeting, payload)
            self.questions.remember(snapshot, question, skill)
        return meeting, snapshot, question, skill

    def begin_question(self, payload):
        self.questions.ensure_capacity()
        meeting, snapshot, _, _ = self._new_question(payload)
        self.questions.start(meeting, snapshot)
        return {'id': snapshot}

    def resume_question(self, payload):
        meeting = required_text(payload, 'meetingId', 80)
        snapshot = required_text(payload, 'questionId', 80)
        self.questions.start(meeting, snapshot)
        return {'id': snapshot}

    def skills(self, _payload):
        """The question types the composer offers, read from the packaged skill contracts."""
        rows = [{'name': AUTO, 'label': 'Auto', 'triggers': [], 'rounds': None}]
        for name in MANUAL:
            contract = load_contract(name)
            rows.append({
                'name': name, 'label': name.replace('_', ' ').capitalize(), 'version': contract['version'],
                'triggers': [str(phrase) for phrase in (contract.get('trigger') or [])][:6],
                'rounds': (contract.get('session') or {}).get('max_rounds'),
            })
        return {'skills': rows}

    def _ratings(self, meeting):
        from meeting_copilot.meta_suggest import ensure

        ensure(self.store)
        with self.store.read() as db:
            return {row['snapshot']: row['rating'] for row in db.execute(
                'SELECT f.snapshot, f.rating FROM answer_feedback f JOIN snapshots s ON s.id=f.snapshot '
                'WHERE f.user=? AND f.conversation=? AND s.user=? AND s.meeting=?',
                (self.principal, CONVERSATION, self.principal, meeting))}

    def feedback(self, payload):
        """Rate one of the user's own finished answers. Read only by the suggest report."""
        from meeting_copilot.meta_suggest import add_feedback

        meeting = required_text(payload, 'meetingId', 80)
        snapshot = required_text(payload, 'questionId', 80)
        rating = payload.get('rating')
        note = payload.get('note', '')
        if rating not in ('up', 'down') or not isinstance(note, str) or len(note) > 500:
            raise DesktopError('Choose a rating and keep the note under 500 characters.')
        with self.store.read():
            question = self.questions._row(meeting, snapshot)['question']
        try:
            add_feedback(self.store, self.principal, snapshot, CONVERSATION, question, rating, note.strip())
        except ValueError:
            raise DesktopError('Only a finished answer can be rated.') from None
        return {'id': snapshot, 'rating': rating}

    def preferences(self, payload):
        """Saved wording preferences. They change the style of API answers only."""
        from meeting_copilot.meta_suggest import MAX_PREFERENCES, preferences, set_preferences

        current = preferences(self.store, self.principal)
        action = payload.get('action', 'list')
        try:
            if action == 'add':
                current = set_preferences(self.store, self.principal, current + [required_text(payload, 'value', 200)])
            elif action == 'remove':
                index = payload.get('index')
                if type(index) is not int or not 0 <= index < len(current):
                    raise DesktopError('That preference is no longer saved.')
                current = set_preferences(self.store, self.principal, current[:index] + current[index + 1:])
            elif action == 'clear':
                current = set_preferences(self.store, self.principal, [])
            elif action != 'list':
                raise DesktopError('Unknown preference action.')
        except ValueError:
            raise DesktopError(
                f'Keep at most {MAX_PREFERENCES} single-line preferences of up to 200 characters.') from None
        return {'preferences': current, 'limit': MAX_PREFERENCES}

    def suggest(self, payload):
        """On request, analyse the user's own question history. Changes nothing."""
        from meeting_copilot.meta_suggest import suggest

        meeting = required_text(payload, 'meetingId', 80)
        use_model = payload.get('useModel') is True
        provider = self._assistant_for(meeting).provider
        if use_model and provider.config.provider == 'demo':
            raise DesktopError('Model suggestions need a configured API provider and a meeting other than the sample.')
        snapshot = self._scope(meeting)
        report = asyncio.run(suggest(self.store, self.principal, snapshot, self.trace, provider if use_model else None))
        suggestions = []
        for item in report['suggestions']:
            # The report words its actions for the command line; these are the desktop equivalents.
            action = SUGGESTION_ACTIONS.get(item['kind'], item['recommended_action'])
            row = {'kind': item['kind'], 'title': item['title'], 'observed': item['observed'],
                   'action': action, 'support': item['support']}
            if isinstance(item.get('preference'), str):
                row['preference'] = item['preference']
            if isinstance(item.get('skill'), str):
                row['skill'] = item['skill']
            suggestions.append(row)
        return {
            'mode': report['mode'], 'turnsAnalysed': report['scope']['turns_analysed'],
            'feedbackRows': report['scope']['feedback_rows'], 'suggestions': suggestions,
            'activation': 'Nothing was changed. A preference is saved only when you choose to, and a new skill '
                          'is a draft for review that is not loaded.',
            'privacy': report['privacy'], 'limits': report['limits'],
        }

    def settings(self, _payload):
        """Provider and API-key status. Never returns a key."""
        from meeting_copilot import credentials

        config = self.config
        name = config.api_key_env
        local = config.provider == 'demo'
        if local:
            preset = 'local'
        elif config.provider == 'anthropic':
            preset = 'anthropic'
        elif not config.base_url:
            preset = 'openai'
        else:
            preset = 'deepseek' if 'deepseek.com' in config.base_url else 'custom'
        in_environment = bool(os.environ.get(name))
        saved = bool(credentials.stored_key(name))
        return {
            'provider': config.provider, 'providerLabel': PROVIDER_LABELS[config.provider],
            'model': '' if local else config.model, 'baseUrl': config.base_url or '',
            'sendEvidence': config.send_evidence, 'preset': preset, 'presets': [dict(row) for row in MODEL_PRESETS],
            'keyName': name, 'keyInEnvironment': in_environment, 'keySaved': saved,
            'keyStoreAvailable': credentials.store_available(), 'configFile': str(self.config_path),
            # True once questions about a real meeting will reach the model.
            'ready': not local and config.send_evidence and (in_environment or saved),
        }

    def _valid_key(self, payload):
        """An optional API key from the request. It is never logged, traced or echoed."""
        key = payload.get('key', '')
        key = key.strip() if isinstance(key, str) else None
        if key is None or (key and (not 8 <= len(key) <= 512 or any(ch.isspace() or not ch.isprintable() for ch in key))):
            raise DesktopError('Enter a valid API key.')
        return key

    def save_model_settings(self, payload):
        """Save the model choice to the workspace configuration and use it at once.

        Only the provider fields change; the data folder, the principal and every
        other setting in an existing configuration are kept.
        """
        from meeting_copilot import credentials
        from meeting_copilot.assistant import Assistant
        from meeting_copilot.config import Config
        from meeting_copilot.private_files import write_private_text
        from meeting_copilot.provider import Provider

        preset = next((row for row in MODEL_PRESETS if row['id'] == payload.get('preset')), None)
        if preset is None:
            raise DesktopError('Choose a provider.')
        key = self._valid_key(payload)
        capture = self._current_capture()
        if capture or self.questions.pending:
            raise DesktopError('Wait for the recording and any running answer to finish before changing the model.')
        values = {name: getattr(self.config, name) for name in Config.__dataclass_fields__ if not name.startswith('_')}
        if preset['id'] == 'local':
            defaults = Config()
            values.update(provider='demo', model=defaults.model, base_url=None, send_evidence=False)
        else:
            model = payload.get('model')
            base_url = payload.get('baseUrl', '')
            consent = payload.get('sendEvidence')
            if not isinstance(model, str) or not 1 <= len(model.strip()) <= 200 or any(ch.isspace() for ch in model.strip()):
                raise DesktopError('Enter the model name the provider expects.')
            if not isinstance(base_url, str) or len(base_url) > 500:
                raise DesktopError('Enter a valid base URL.')
            base_url = base_url.strip()
            local_server = base_url.startswith(('http://localhost', 'http://127.0.0.1'))
            if base_url and not (base_url.startswith('https://') or local_server) or any(ch.isspace() for ch in base_url):
                raise DesktopError('The base URL must start with https://.')
            if not base_url and preset['id'] in ('deepseek', 'custom'):
                raise DesktopError('Enter the base URL of the provider.')
            if type(consent) is not bool:
                raise DesktopError('Choose whether meeting excerpts may be sent to the provider.')
            values.update(provider=preset['provider'], model=model.strip(), base_url=base_url or None,
                          send_evidence=consent)
            # The shipped defaults (20 s, 800 tokens) suit the local mode, not a hosted model.
            values['timeout_seconds'] = max(values['timeout_seconds'], 120)
            values['max_tokens'] = max(values['max_tokens'], 4096)
            if preset['id'] == 'deepseek':
                values['streaming'] = True
        if key and not credentials.store_available():
            raise DesktopError('The system credential store is unavailable in this build, so the key cannot be saved '
                               'here. Set the environment variable instead.')
        text = json.dumps(values, indent=2, ensure_ascii=False)
        draft = self.config_path.with_name(self.config_path.name + '.draft')
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            write_private_text(draft, text)
            Config.load(draft)
            write_private_text(self.config_path, text)
            config = Config.load(self.config_path)
        except (OSError, TypeError, ValueError):
            raise DesktopError('The settings could not be saved to the workspace configuration.') from None
        finally:
            try:
                draft.unlink()
            except OSError:
                pass
        if key and not credentials.save_key(config.api_key_env, key):
            raise DesktopError('The model was saved, but the system credential store did not accept the key.')
        self.config = config
        self.assistant = Assistant(self.store, self.principal, Provider(config), self.trace)
        self.local_assistant = self.assistant if config.provider == 'demo' else Assistant(
            self.store, self.principal, Provider(Config()), self.trace,
        )
        self.workers.a = self.assistant
        return self.settings({})

    def test_model(self, _payload):
        """Send one short request that contains no meeting content, to check the key, model and address."""
        import dataclasses

        from meeting_copilot.provider import Provider

        if self.config.provider == 'demo':
            raise DesktopError('Choose a provider and save it first.')
        probe = dataclasses.replace(self.config, send_evidence=True, max_tokens=32, streaming=False, tool_rounds=0,
                                    timeout_seconds=min(self.config.timeout_seconds, 30))
        asyncio.run(Provider(probe).chat([{'role': 'user', 'content': 'Reply with the single word OK.'}]))
        return {'ok': True, 'model': self.config.model}

    def delete_api_key(self, _payload):
        from meeting_copilot import credentials

        if credentials.store_available() and not credentials.delete_key(self.config.api_key_env):
            raise DesktopError('The saved key could not be removed.')
        return self.settings({})

    def question_history(self, payload):
        meeting = required_text(payload, 'meetingId', 80)
        turns = self.questions.history(meeting)
        ratings = self._ratings(meeting) if turns else {}
        for turn in turns:
            if turn['id'] in ratings:
                turn['rating'] = ratings[turn['id']]
            if 'answer' in turn:
                turn['answer'] = self._desktop_answer(meeting, turn['answer'])
            failure = turn.pop('failure', None)
            if failure:
                turn['error'] = PROVIDER_MESSAGES.get(failure, 'The question could not finish. You can try to continue it.')
        return {'turns': turns}

    def ask(self, payload):
        meeting, snapshot, question, skill = self._new_question(payload)
        assistant = self._assistant_for(meeting)
        answer = asyncio.run(assistant.ask(snapshot, question, conversation=CONVERSATION, skill=skill))
        return self._desktop_answer(meeting, answer)

    def _desktop_answer(self, meeting, answer):
        citations = []
        for source, claim in zip(answer['citations'], answer['claims']):
            source_id = source['source_id']
            if source['kind'] == 'file':
                label = f"{source['path']} · line {source['line_start']}"
            else:
                label = timestamp(source.get('start_ms'))
            citations.append({
                'id': source_id, 'kind': source['kind'], 'label': label,
                'quote': claim['quote'], 'meetingId': source['meeting'],
            })
        routing = answer.get('routing') or {}
        return {'text': answer['text'], 'citations': citations, 'mode': answer['mode'],
                'skill': answer.get('skill'), 'skillRoute': routing.get('status'), 'taskId': answer.get('task_id'),
                **self._answer_mode(meeting)}

    def _summarize(self, snapshot, assistant):
        result = asyncio.run(assistant.summary_graph.ainvoke({'snapshot': snapshot}))['result']
        return result['text']

    def _snapshot_transcript_ids(self, snapshot):
        with self.store.read() as db:
            scope = self.store.snapshot_info(self.principal, snapshot)
            return [row[0] for row in db.execute(
                "SELECT e.id FROM evidence e JOIN json_each(?) ref ON e.id=ref.value "
                "WHERE e.kind='segment' AND e.meeting=? ORDER BY e.id",
                (scope['refs'], scope['meeting']),
            )]

    def _saved_minutes(self, meeting, transcript_ids):
        with self.store.read() as db:
            self.store.metadata(self.principal, meeting)
            row = db.execute('''
                SELECT m.snapshot, m.content FROM desktop_minutes m
                JOIN snapshots s ON s.id=m.snapshot
                WHERE s.meeting=? AND s.user=? AND m.transcript_refs=?
                ORDER BY s.created DESC LIMIT 1
                ''', (meeting, self.principal, json.dumps(sorted(transcript_ids)))).fetchone()
            if row is None:
                return None
            summary = self.store.summary_get(self.principal, row['snapshot'])
            return {**summary, 'minutes': json.loads(row['content'])} if summary else None

    def _latest_live_summary(self, meeting):
        from meeting_copilot.store import Denied

        with self.store.read() as db:
            self.store.metadata(self.principal, meeting)
            rows = db.execute('''
                SELECT s.id FROM snapshots s JOIN summaries m ON s.id=m.snapshot
                WHERE s.meeting=? AND s.user=?
                AND NOT EXISTS (SELECT 1 FROM desktop_minutes d WHERE d.snapshot=s.id)
                ORDER BY s.created DESC
                ''', (meeting, self.principal))
            for row in rows:
                try:
                    return self.store.summary_get(self.principal, row['id'])
                except Denied:
                    continue
        return None

    def _final_summary(self, meeting, assistant):
        from minutes import generate_minutes

        capture = self._current_capture()
        if capture and capture.meeting == meeting:
            raise DesktopError('Stop recording before generating final meeting notes.')
        snapshot = self.store.snapshot(self.principal, meeting)
        transcript_ids = self._snapshot_transcript_ids(snapshot)
        if not transcript_ids:
            return {'text': '', 'minutes': None}
        mode = 'local' if assistant.provider.config.provider == 'demo' else 'api'
        existing = self._saved_minutes(meeting, transcript_ids)
        if existing and existing['minutes']['mode'] == mode:
            return {'text': existing['text'], 'minutes': existing['minutes']}
        minutes = asyncio.run(generate_minutes(self.store, self.principal, snapshot, assistant.provider))
        text = minutes_text(minutes)
        refs = sorted({source['id'] for key in MINUTES_SECTIONS for item in minutes.get(key, [])
                       for source in item['sources']})
        with self.store.tx() as db:
            self.store.summary_put(self.principal, snapshot, text, refs)
            db.execute('INSERT INTO desktop_minutes VALUES (?,?,?)',
                       (snapshot, json.dumps(transcript_ids), json.dumps(minutes, ensure_ascii=False)))
        return {'text': text, 'minutes': minutes}

    def summary(self, payload):
        meeting = required_text(payload, 'meetingId', 80)
        assistant = self._assistant_for(meeting)
        if payload.get('final') is True:
            return self._final_summary(meeting, assistant)
        return {'text': self._summarize(self._scope(meeting), assistant)}

    def export(self, payload):
        meeting = required_text(payload, 'meetingId', 80)
        destination = selected_path(payload)
        if destination.suffix.lower() != '.json' or destination.is_relative_to(self.root):
            raise DesktopError('Choose a JSON file outside the application data folder.')
        snapshot = self._scope(meeting)
        transcript_ids = self._snapshot_transcript_ids(snapshot)
        minutes = self._saved_minutes(meeting, transcript_ids)
        latest = minutes or self._latest_live_summary(meeting)
        refs = set(json.loads(self.store.snapshot_info(self.principal, snapshot)['refs']))
        if latest and set(latest['refs']).issubset(refs):
            with self.store.tx() as db:
                self.store.summary_put(self.principal, snapshot, latest['text'], latest['refs'])
                if minutes:
                    # Keep the export's copied summary out of the live-summary list.
                    db.execute('INSERT INTO desktop_minutes VALUES (?,?,?)',
                               (snapshot, json.dumps(transcript_ids), json.dumps(minutes['minutes'], ensure_ascii=False)))
        else:
            self._summarize(snapshot, self.local_assistant)
        result = self.store.export(self.principal, snapshot, 'desktop-export')
        if minutes:
            result['minutes'] = minutes['minutes']
        health = self._saved_session_health(meeting)
        if health:
            result['sessionHealth'] = health
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent, delete=False) as file:
                temporary = Path(file.name)
                json.dump(result, file, ensure_ascii=False, indent=2)
                file.write('\n')
            os.replace(temporary, destination)
        except OSError as exc:
            raise DesktopError('Could not save the meeting export to the selected folder.') from exc
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return {'path': str(destination)}

    def import_audio(self, payload):
        from meeting_copilot.audio import LocalASR

        meeting = required_text(payload, 'meetingId', 80)
        if meeting == self.sample_id:
            raise DesktopError('Create a new meeting to import audio.')
        if self._current_capture():
            raise DesktopError('Stop recording before importing an audio file.')
        path = selected_path(payload)
        if not path.is_file():
            raise DesktopError('Choose an existing audio file.')
        offset = self._offset(meeting)
        asr = None
        try:
            asr = LocalASR(self.root, model=self.config.asr_model, allow_download=True)
            segments = asr.transcribe(path)
        except Exception as exc:
            raise DesktopError('Could not transcribe this file. Check the audio format and the connection for the first model download.') from exc
        finally:
            if asr:
                asr.close()
        if not segments:
            raise DesktopError('No speech was detected in this file.')
        stream = uuid.uuid4().hex
        with self.store.tx():
            for number, segment in enumerate(segments, 1):
                event_id = f'{stream}-{number}'
                self.store.ingest(self.principal, meeting, {
                    'event_id': event_id, 'segment_id': event_id, 'stream': stream, 'seq': number,
                    'revision': 1, 'start_ms': offset + segment.start_ms, 'end_ms': offset + segment.end_ms,
                    'text': segment.text, 'final': True,
                })
        return self._detail(meeting)

    def devices(self, _payload):
        from meeting_copilot.audio import microphones

        try:
            devices = microphones()
        except Exception as exc:
            raise DesktopError('Could not load microphone devices.') from exc
        return {'devices': [{'id': item['id'], 'name': item['name'], 'isDefault': item['is_default']}
                            for item in devices if item['available']]}

    def start_recording(self, payload):
        from meeting_copilot.assistant import LiveSummaries

        meeting = required_text(payload, 'meetingId', 80)
        device = required_text(payload, 'deviceId', 300)
        low_latency = payload.get('lowLatency', False)
        if type(low_latency) is not bool:
            raise DesktopError('Choose a valid low-latency setting.')
        if meeting == self.sample_id:
            raise DesktopError('Create a new meeting to record audio.')
        if self._current_capture():
            raise DesktopError('A meeting is already recording.')
        offset = self._offset(meeting)
        self.recording_errors.pop(meeting, None)
        self.capture = Capture(self.root, meeting, device, offset, self.principal, self.config.asr_model,
                               low_latency=low_latency)
        # The ASR model is warmed before capture starts; a cold start can exceed 8 seconds.
        self.capture.ready.wait(CAPTURE_START_TIMEOUT_SECONDS)
        if not self.capture.started:
            capture = self.capture
            capture.stop(closing=True)
            self._current_capture()
            raise DesktopError(capture.error or 'Preparing transcription took too long. Try starting the recording again.')
        self.live_summaries = LiveSummaries(self.workers, meeting)
        return self._detail(meeting)

    def stop_recording(self, payload):
        meeting = required_text(payload, 'meetingId', 80)
        capture = self._current_capture()
        if capture and capture.meeting == meeting:
            self._stop_live_summaries()
            capture.stop()
        return self._detail(meeting)


def serve(bridge, stream):
    from meeting_copilot.provider import ProviderError
    from meeting_copilot.store import Denied

    while line := sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1):
        request_id = None
        try:
            if len(line) > MAX_REQUEST_BYTES:
                while not line.endswith(b'\n'):
                    line = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
                    if not line:
                        break
                raise DesktopError('Desktop request is too large.')
            request = json.loads(line)
            if not isinstance(request, dict):
                raise DesktopError('Invalid desktop request.')
            request_id = request.get('id')
            if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
                request_id = None
                raise DesktopError('Invalid desktop request ID.')
            payload = request.get('payload', {})
            if not isinstance(payload, dict):
                raise DesktopError('Invalid desktop request payload.')
            result = bridge.run(request.get('action'), payload)
            reply = {'id': request_id, 'ok': True, 'result': result}
        except (json.JSONDecodeError, UnicodeDecodeError):
            reply = {'id': request_id, 'ok': False, 'error': 'Invalid desktop request.'}
        except DesktopError as exc:
            reply = {'id': request_id, 'ok': False, 'error': str(exc)}
        except Denied:
            reply = {'id': request_id, 'ok': False, 'error': 'This meeting is unavailable.'}
        except ProviderError as exc:
            message = PROVIDER_MESSAGES.get(exc.code, 'The configured provider could not complete this request.')
            reply = {'id': request_id, 'ok': False, 'error': message}
        except Exception as exc:
            print(f'Desktop request failed ({type(exc).__name__}).', file=sys.stderr)
            reply = {'id': request_id, 'ok': False, 'error': 'The desktop operation failed. Please try again.'}
        write_json(stream, reply)


def main():
    from meeting_copilot.config import Config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path, help='Trusted configuration file using the existing Meeting Copilot schema.')
    parser.add_argument('--capture', nargs=6, help=argparse.SUPPRESS)
    parser.add_argument('--capture-stop-file', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--low-latency', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    workspace = args.data_dir.resolve()
    config = None
    if args.capture:
        root = workspace
    else:
        default_path = workspace / 'config.json'
        config_path = args.config or (default_path if default_path.exists() else None)
        try:
            config = Config.load(config_path) if config_path else Config(_base=workspace)
        except (OSError, TypeError, ValueError):
            print('The desktop configuration could not be loaded.', file=sys.stderr)
            return 1
        root = config.root
    root.mkdir(parents=True, exist_ok=True)
    configure_cache(root)

    # Native libraries may write diagnostics to stdout. Keep the protocol on
    # its original descriptor and route all other process output to stderr.
    with os.fdopen(os.dup(sys.stdout.fileno()), 'w', encoding='utf-8') as protocol:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        if args.capture:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            meeting, device, offset, parent_pid, principal, model = args.capture
            return capture_audio(root, protocol, meeting, device, int(offset), int(parent_pid), principal, model,
                                 low_latency=args.low_latency, stop_file=args.capture_stop_file)
        bridge = DesktopBridge(config, None if args.capture else (config_path or default_path))

        def terminate(_signum, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, terminate)
        try:
            serve(bridge, protocol)
        except KeyboardInterrupt:
            pass
        finally:
            bridge.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
