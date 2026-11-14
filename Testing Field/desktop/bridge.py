"""Local desktop transport for the existing Meeting Copilot Python core."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import uuid


CONVERSATION = 'desktop-private'
SAMPLE_ID = 'demo'
SAMPLE_SOURCE_TITLE = 'Atlas technical design (SIMULATED REPLAY)'
SKILLS = {'catch_up', 'trace_decision', 'compare_options', 'direct'}
MAX_REQUEST_BYTES = 64 * 1024
PROVIDER_LABELS = {'demo': 'Local extractive', 'openai': 'OpenAI-compatible', 'anthropic': 'Anthropic'}
PROVIDER_MESSAGES = {
    'evidence_upload_not_enabled': 'Enable send_evidence in the desktop configuration to use the configured API.',
    'missing_api_key': 'The configured provider API key is not available to the desktop process.',
    'timeout': 'The configured API did not respond before the timeout.',
    'invalid_minutes_response': 'The configured API did not return usable cited meeting notes. Try again.',
}
MINUTES_SECTIONS = {
    'overview': 'Overview', 'topics': 'Discussion topics', 'decisions': 'Decisions',
    'actionItems': 'Action items', 'openQuestions': 'Open questions',
}


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
    def __init__(self, root, meeting, device, offset, principal, model):
        self.meeting = meeting
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.started = False
        self.error = None
        self.stop_requested = False
        self.process = subprocess.Popen(
            [sys.executable, '-u', str(Path(__file__).resolve()), '--data-dir', str(root),
             '--capture', meeting, device, str(offset), str(os.getpid()), principal, model],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            start_new_session=True,
            text=True,
            encoding='utf-8',
        )
        self.reader = threading.Thread(target=self.read_status, name='desktop-capture-status', daemon=True)
        self.reader.start()

    def read_status(self):
        try:
            with self.process.stdout as stream:
                for line in stream:
                    status = json.loads(line)
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
                self.process.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
        if self.finished.wait(8 if closing else 15):
            return
        if not closing:
            raise DesktopError('Recording is still finishing transcription. Try again in a moment.')
        self.process.terminate()
        if not self.finished.wait(3):
            self.process.kill()
            self.finished.wait(3)


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


def capture_audio(root, stream, meeting, device, offset, parent_pid, principal, model):
    from meeting_copilot.audio import listen
    from meeting_copilot.store import Store
    from meeting_copilot.trace import Trace

    store = Store(root / 'evidence.db')

    def on_status(status):
        if os.getppid() != parent_pid:
            raise KeyboardInterrupt
        if status['event'] == 'recording_started':
            write_json(stream, {'event': 'started'})

    try:
        if os.getppid() != parent_pid:
            return 0
        result = listen(OffsetStore(store, offset), principal, meeting, root, device, 28800, True,
                        model=model, download=True, trace=Trace(root / 'traces.jsonl'), on_status=on_status)
        if result['errors'] or result['dropped_segments']:
            write_json(stream, {'error': 'Some audio could not be transcribed. Review the transcript for gaps.'})
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        write_json(stream, {'error': 'Could not record from this microphone. Check macOS microphone access and the selected device.'})
        return 1
    finally:
        store.close()


class DesktopBridge:
    def __init__(self, config):
        from meeting_copilot.assistant import Assistant, Workers
        from meeting_copilot.config import Config
        from meeting_copilot.provider import Provider
        from meeting_copilot.store import Store
        from meeting_copilot.trace import Trace

        self.config = config
        self.root = config.root
        self.principal = config.principal
        self.store = Store(self.root / 'evidence.db')
        with self.store.tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS desktop_minutes (
                snapshot TEXT PRIMARY KEY REFERENCES snapshots(id) ON DELETE CASCADE,
                transcript_refs TEXT NOT NULL,
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

    def close(self):
        try:
            self._stop_live_summaries()
            if self.capture and not self.capture.finished.is_set():
                self.capture.stop(closing=True)
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
            'summary': self.summary,
            'export': self.export,
            'importAudio': self.import_audio,
            'devices': self.devices,
            'startRecording': self.start_recording,
            'stopRecording': self.stop_recording,
        }
        if not isinstance(action, str) or action not in handlers:
            raise DesktopError('Unknown desktop action.')
        return handlers[action](payload)

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

    def _answer_mode(self, meeting):
        provider = 'demo' if meeting == self.sample_id else self.config.provider
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
            self.capture = None
        return self.capture

    def _stop_live_summaries(self):
        if self.live_summaries:
            self.live_summaries.close()
            self.live_summaries = None

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
        if meeting in self.recording_errors:
            detail['recordingError'] = self.recording_errors[meeting]
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

    def ask(self, payload):
        meeting = required_text(payload, 'meetingId', 80)
        question = required_text(payload, 'question', 4000)
        skill = payload.get('skill', 'trace_decision')
        if not isinstance(skill, str) or skill not in SKILLS:
            raise DesktopError('Choose a supported question type.')
        snapshot = self._scope(meeting, payload)
        assistant = self.local_assistant if meeting == self.sample_id else self.assistant
        answer = asyncio.run(assistant.ask(snapshot, question, conversation=CONVERSATION, skill=skill))
        quotes = {claim['source_id']: claim['quote'] for claim in answer['claims']}
        citations = []
        for source in answer['citations']:
            source_id = source['source_id']
            if source['kind'] == 'file':
                label = f"{source['path']} · line {source['line_start']}"
            else:
                label = timestamp(source.get('start_ms'))
            citations.append({
                'id': source_id, 'kind': source['kind'], 'label': label,
                'quote': quotes[source_id], 'meetingId': source['meeting'],
            })
        return {'text': answer['text'], 'citations': citations, 'mode': answer['mode'], **self._answer_mode(meeting)}

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
        assistant = self.local_assistant if meeting == self.sample_id else self.assistant
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
        if meeting == self.sample_id:
            raise DesktopError('Create a new meeting to record audio.')
        if self._current_capture():
            raise DesktopError('A meeting is already recording.')
        offset = self._offset(meeting)
        self.recording_errors.pop(meeting, None)
        self.capture = Capture(self.root, meeting, device, offset, self.principal, self.config.asr_model)
        self.capture.ready.wait(8)
        if not self.capture.started:
            capture = self.capture
            capture.stop(closing=True)
            self._current_capture()
            raise DesktopError(capture.error or 'Microphone recording did not start.')
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
            return capture_audio(root, protocol, meeting, device, int(offset), int(parent_pid), principal, model)
        bridge = DesktopBridge(config)

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
