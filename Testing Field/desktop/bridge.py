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


PRINCIPAL = 'local-host'
CONVERSATION = 'desktop-private'
SAMPLE_ID = 'demo'
SKILLS = {'catch_up', 'trace_decision', 'compare_options', 'direct'}
MAX_REQUEST_BYTES = 64 * 1024


class DesktopError(Exception):
    """An error whose message is safe to display in the desktop application."""


def required_text(payload, key, limit):
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DesktopError(f'Enter a valid {key}.')
    return value.strip()


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


class Capture:
    def __init__(self, root, meeting, device, offset):
        self.meeting = meeting
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.started = False
        self.error = None
        self.stop_requested = False
        self.process = subprocess.Popen(
            [sys.executable, '-u', str(Path(__file__).resolve()), '--data-dir', str(root.parent),
             '--capture', meeting, device, str(offset), str(os.getpid())],
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


def capture_audio(root, stream, meeting, device, offset, parent_pid):
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
        result = listen(OffsetStore(store, offset), PRINCIPAL, meeting, root, device, 28800, True,
                        download=True, trace=Trace(root / 'traces.jsonl'), on_status=on_status)
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
    def __init__(self, root):
        from meeting_copilot.assistant import Assistant
        from meeting_copilot.config import Config
        from meeting_copilot.provider import Provider
        from meeting_copilot.store import Store
        from meeting_copilot.trace import Trace

        self.root = root
        self.store = Store(root / 'evidence.db')
        self.trace = Trace(root / 'traces.jsonl')
        self.assistant = Assistant(self.store, PRINCIPAL, Provider(Config()), self.trace)
        self.capture = None
        self.recording_errors = {}

    def close(self):
        try:
            if self.capture and not self.capture.finished.is_set():
                self.capture.stop(closing=True)
        finally:
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
                'WHERE a.user=? ORDER BY m.rowid DESC', (PRINCIPAL,),
            )
            return [self._meeting_header(dict(row)) for row in rows]

    def _meeting_header(self, row):
        sample = row['id'] == SAMPLE_ID
        title = 'Atlas technical design' if sample else row['title']
        return {'id': row['id'], 'title': title, 'mode': 'sample' if sample else 'local'}

    def _scope(self, meeting):
        folders = [folder['id'] for folder in self.store.folders(PRINCIPAL, meeting)]
        return self.store.snapshot(PRINCIPAL, meeting, folders=folders)

    def _current_segments(self, meeting):
        # Polling the transcript should not create persistent question snapshots.
        with self.store.read() as db:
            self.store._check(PRINCIPAL, meeting)
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
            self.store._check(PRINCIPAL, meeting)
            return db.execute('SELECT COALESCE(MAX(end_ms), 0) FROM evidence WHERE meeting=?', (meeting,)).fetchone()[0]

    def _current_capture(self):
        capture = self.capture
        if capture and capture.finished.is_set():
            if capture.error:
                self.recording_errors[capture.meeting] = capture.error
            self.capture = None
        return self.capture

    def _detail(self, meeting):
        row = self.store.metadata(PRINCIPAL, meeting)
        summary = self.store.latest_summary(PRINCIPAL, meeting)
        capture = self._current_capture()
        detail = {
            **self._meeting_header(row),
            'segments': self._current_segments(meeting),
            'summary': summary['text'] if summary else '',
            'recording': bool(capture and capture.meeting == meeting),
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
            existing = any(row['id'] == SAMPLE_ID for row in self._meetings())
            if not existing or not self.store.folders(PRINCIPAL, SAMPLE_ID):
                seed(self.store, PRINCIPAL, self.root, meeting=SAMPLE_ID)
        return self._selection(SAMPLE_ID)

    def create(self, payload):
        meeting = uuid.uuid4().hex
        self.store.create(PRINCIPAL, meeting, required_text(payload, 'title', 200))
        return self._selection(meeting)

    def meeting(self, payload):
        return self._detail(required_text(payload, 'meetingId', 80))

    def ask(self, payload):
        meeting = required_text(payload, 'meetingId', 80)
        question = required_text(payload, 'question', 4000)
        skill = payload.get('skill', 'trace_decision')
        if not isinstance(skill, str) or skill not in SKILLS:
            raise DesktopError('Choose a supported question type.')
        snapshot = self._scope(meeting)
        answer = asyncio.run(self.assistant.ask(snapshot, question, conversation=CONVERSATION, skill=skill))
        quotes = {claim['source_id']: claim['quote'] for claim in answer['claims']}
        citations = []
        for source in answer['citations']:
            source_id = source['source_id']
            if source['kind'] == 'file':
                label = f"{source['path']} · line {source['line_start']}"
            else:
                label = timestamp(source.get('start_ms'))
            citations.append({'id': source_id, 'kind': source['kind'], 'label': label, 'quote': quotes[source_id]})
        return {'text': answer['text'], 'citations': citations, 'mode': answer['mode']}

    def _summarize(self, snapshot):
        result = asyncio.run(self.assistant.summary_graph.ainvoke({'snapshot': snapshot}))['result']
        return result['text']

    def summary(self, payload):
        meeting = required_text(payload, 'meetingId', 80)
        return {'text': self._summarize(self._scope(meeting))}

    def export(self, payload):
        meeting = required_text(payload, 'meetingId', 80)
        destination = selected_path(payload)
        if destination.suffix.lower() != '.json' or destination.is_relative_to(self.root.parent):
            raise DesktopError('Choose a JSON file outside the application data folder.')
        snapshot = self._scope(meeting)
        self._summarize(snapshot)
        result = self.store.export(PRINCIPAL, snapshot, 'desktop-export')
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
        if meeting == SAMPLE_ID:
            raise DesktopError('Create a new meeting to import audio.')
        if self._current_capture():
            raise DesktopError('Stop recording before importing an audio file.')
        path = selected_path(payload)
        if not path.is_file():
            raise DesktopError('Choose an existing audio file.')
        offset = self._offset(meeting)
        asr = None
        try:
            asr = LocalASR(self.root, allow_download=True)
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
                self.store.ingest(PRINCIPAL, meeting, {
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
        meeting = required_text(payload, 'meetingId', 80)
        device = required_text(payload, 'deviceId', 300)
        if meeting == SAMPLE_ID:
            raise DesktopError('Create a new meeting to record audio.')
        if self._current_capture():
            raise DesktopError('A meeting is already recording.')
        offset = self._offset(meeting)
        self.recording_errors.pop(meeting, None)
        self.capture = Capture(self.root, meeting, device, offset)
        self.capture.ready.wait(8)
        if not self.capture.started:
            capture = self.capture
            capture.stop(closing=True)
            self._current_capture()
            raise DesktopError(capture.error or 'Microphone recording did not start.')
        return self._detail(meeting)

    def stop_recording(self, payload):
        meeting = required_text(payload, 'meetingId', 80)
        capture = self._current_capture()
        if capture and capture.meeting == meeting:
            capture.stop()
        return self._detail(meeting)


def serve(bridge, stream):
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
        except Exception as exc:
            print(f'Desktop request failed ({type(exc).__name__}).', file=sys.stderr)
            reply = {'id': request_id, 'ok': False, 'error': 'The desktop operation failed. Please try again.'}
        write_json(stream, reply)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--capture', nargs=4, help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = args.data_dir.resolve() / 'data'
    root.mkdir(parents=True, exist_ok=True)
    configure_cache(root)

    # Native libraries may write diagnostics to stdout. Keep the protocol on
    # its original descriptor and route all other process output to stderr.
    with os.fdopen(os.dup(sys.stdout.fileno()), 'w', encoding='utf-8') as protocol:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        if args.capture:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            meeting, device, offset, parent_pid = args.capture
            return capture_audio(root, protocol, meeting, device, int(offset), int(parent_pid))
        bridge = DesktopBridge(root)

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
