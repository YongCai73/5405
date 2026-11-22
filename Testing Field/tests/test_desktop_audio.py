"""Desktop recording options and live status without a microphone or model."""

import importlib.util
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from meeting_copilot.config import Config
from meeting_copilot.store import Store


MODULE_PATH = Path(__file__).resolve().parents[1] / 'desktop' / 'bridge.py'
SPEC = importlib.util.spec_from_file_location('desktop_audio_bridge', MODULE_PATH)
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)


@pytest.fixture
def desktop(tmp_path):
    bridge = BRIDGE.DesktopBridge(Config(principal='alice', _base=tmp_path))
    bridge.store.create('alice', 'm', 'Meeting')
    yield bridge
    bridge.close()


class FakeSummaries:
    def __init__(self, workers, meeting):
        self.closed = False

    def close(self):
        self.closed = True


class ReadyAfterWarmup:
    def __init__(self, capture):
        self.capture = capture
        self.waited = []

    def wait(self, timeout):
        self.waited.append(timeout)
        self.capture.started = timeout >= 13
        return self.capture.started


class FakeCapture:
    instances = []

    def __init__(self, root, meeting, device, offset, principal, model, *, low_latency=False):
        self.meeting = meeting
        self.low_latency = low_latency
        self.started = False
        self.finished = threading.Event()
        self.ready = ReadyAfterWarmup(self)
        self.error = None
        self.health = None
        self.recording_status = {
            'preset': 'low-latency' if low_latency else 'default',
            'asrThreads': 4 if low_latency else 2,
            'pauseMs': 300 if low_latency else 500,
            'stalled': False, 'secondsWithoutAudio': 0, 'warning': '',
        }
        self.instances.append(self)

    def stop(self, *, closing=False):
        self.finished.set()


@pytest.mark.parametrize('options,expected', [({}, False), ({'lowLatency': False}, False), ({'lowLatency': True}, True)])
def test_desktop_low_latency_is_opt_in_and_cold_warmup_can_finish(desktop, monkeypatch, options, expected):
    import meeting_copilot.assistant as assistant_module

    monkeypatch.setattr(BRIDGE, 'Capture', FakeCapture)
    monkeypatch.setattr(assistant_module, 'LiveSummaries', FakeSummaries)
    detail = desktop.start_recording({'meetingId': 'm', 'deviceId': 'mic', **options})
    capture = desktop.capture
    assert capture.low_latency is expected
    assert capture.ready.waited == [BRIDGE.CAPTURE_START_TIMEOUT_SECONDS]
    assert BRIDGE.CAPTURE_START_TIMEOUT_SECONDS == 60
    assert detail['recording']
    assert detail['recordingStatus']['preset'] == ('low-latency' if expected else 'default')
    assert detail['recordingStatus']['asrThreads'] == (4 if expected else 2)
    assert detail['recordingStatus']['pauseMs'] == (300 if expected else 500)
    stopped = desktop.stop_recording({'meetingId': 'm'})
    assert not stopped['recording'] and 'recordingStatus' not in stopped
    assert desktop.capture is None


@pytest.mark.parametrize('value', [None, 'true', 'false', 0, 1, [], {}])
def test_desktop_rejects_non_boolean_preset_before_capture(desktop, monkeypatch, value):
    def unexpected(*args, **kwargs):
        pytest.fail('Invalid preset must not start capture')

    monkeypatch.setattr(BRIDGE, 'Capture', unexpected)
    with pytest.raises(BRIDGE.DesktopError, match='low-latency'):
        desktop.start_recording({'meetingId': 'm', 'deviceId': 'mic', 'lowLatency': value})
    assert desktop.capture is None


def test_warmup_timeout_uses_preparation_message_and_cleans_capture(desktop, monkeypatch):
    class NotReady(FakeCapture):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.ready = SimpleNamespace(wait=lambda timeout: False)

    monkeypatch.setattr(BRIDGE, 'Capture', NotReady)
    with pytest.raises(BRIDGE.DesktopError, match='Preparing transcription took too long'):
        desktop.start_recording({'meetingId': 'm', 'deviceId': 'mic'})
    assert desktop.capture is None


@pytest.mark.parametrize('low_latency', [False, True])
def test_capture_process_only_receives_flag_when_selected(tmp_path, monkeypatch, low_latency):
    commands = []

    def start(command, **kwargs):
        commands.append(command)
        stream = io.StringIO(json.dumps({
            'event': 'started', 'preset': 'low-latency' if low_latency else 'default',
            'asr_threads': 6, 'pause_ms': 450,
        }) + '\n')
        return SimpleNamespace(stdout=stream, wait=lambda: 0)

    monkeypatch.setattr(BRIDGE.subprocess, 'Popen', start)
    capture = BRIDGE.Capture(tmp_path, 'm', 'mic', 0, 'alice', 'base.en', low_latency=low_latency)
    capture.reader.join(timeout=5)
    assert not capture.reader.is_alive()
    assert ('--low-latency' in commands[0]) is low_latency
    assert capture.started and capture.error is None
    # Actual settings from listen win over the preset defaults or requested values.
    assert capture.recording_status['asrThreads'] == 6
    assert capture.recording_status['pauseMs'] == 450
    assert capture.recording_status['preset'] == ('low-latency' if low_latency else 'default')
    copy = capture.recording_status
    copy['asrThreads'] = 99
    assert capture.recording_status['asrThreads'] == 6


def test_capture_stall_and_resume_are_live_nonfatal_status(tmp_path, monkeypatch):
    begin = threading.Event()
    holder = {}
    observed = []

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            if not begin.wait(5):
                return
            yield json.dumps({'event': 'started', 'preset': 'default', 'asr_threads': 2, 'pause_ms': 500})
            yield json.dumps({'event': 'capture_stalled', 'seconds_without_audio': 3.2,
                              'warning': 'No audio received. The microphone may be disconnected.'})
            capture = holder['capture']
            observed.append((capture.recording_status, capture.finished.is_set(), capture.error))
            yield json.dumps({'event': 'capture_resumed', 'seconds_without_audio': 4.1})
            observed.append((capture.recording_status, capture.finished.is_set(), capture.error))

    monkeypatch.setattr(BRIDGE.subprocess, 'Popen', lambda *a, **k: SimpleNamespace(stdout=Stream(), wait=lambda: 0))
    capture = BRIDGE.Capture(tmp_path, 'm', 'mic', 0, 'alice', 'base.en')
    holder['capture'] = capture
    begin.set()
    capture.reader.join(timeout=5)
    assert not capture.reader.is_alive() and len(observed) == 2
    stalled, finished, error = observed[0]
    assert stalled['stalled'] and stalled['secondsWithoutAudio'] == 3.2
    assert 'may be disconnected' in stalled['warning']
    assert not finished and error is None
    resumed, finished, error = observed[1]
    assert not resumed['stalled'] and resumed['warning'] == ''
    assert resumed['secondsWithoutAudio'] == 4.1
    assert not finished and error is None


@pytest.mark.parametrize('low_latency', [False, True])
def test_capture_child_passes_preset_to_listen_and_forwards_audio_status(tmp_path, monkeypatch, low_latency):
    import meeting_copilot.audio as audio

    store = Store(tmp_path / 'evidence.db')
    store.create('alice', 'm', 'Meeting')
    store.close()
    received = []

    def listen(*args, **kwargs):
        received.append(kwargs['low_latency'])
        notify = kwargs['on_status']
        notify({'event': 'recording_started', 'preset': 'low-latency' if low_latency else 'default',
                'asr_threads': 5, 'pause_ms': 350})
        notify({'event': 'capture_stalled', 'seconds_without_audio': 3.5, 'warning': 'No audio received.'})
        notify({'event': 'capture_resumed', 'seconds_without_audio': 4.5})
        notify({'event': 'recording_status', 'audio_seconds': 10})
        return {
            'errors': 0, 'dropped_segments': 0, 'processed': 3,
            'speech_to_ready_p95_s': 1.23456, 'capture_missing_seconds': 2.5,
            'ignored': 'transcript content must not leave the capture process',
        }

    monkeypatch.setattr(audio, 'listen', listen)
    monkeypatch.setattr(BRIDGE.os, 'getppid', lambda: 123)
    output = io.StringIO()
    assert BRIDGE.capture_audio(tmp_path, output, 'm', 'mic', 0, 123, 'alice', 'base.en',
                                low_latency=low_latency) == 0
    assert received == [low_latency]
    statuses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [status['event'] for status in statuses] == ['started', 'capture_stalled', 'capture_resumed', 'recording_finished']
    assert statuses[0]['preset'] == ('low-latency' if low_latency else 'default')
    assert statuses[0]['asr_threads'] == 5 and statuses[0]['pause_ms'] == 350
    assert statuses[1]['seconds_without_audio'] == 3.5 and statuses[2]['seconds_without_audio'] == 4.5
    assert statuses[3]['health'] == {
        'errors': 0, 'dropped_segments': 0, 'processed': 3,
        'speech_to_ready_p95_s': 1.235, 'capture_missing_seconds': 2.5,
    }


def test_capture_child_default_keeps_standard_preset(tmp_path, monkeypatch):
    import meeting_copilot.audio as audio

    received = []

    def listen(*args, **kwargs):
        received.append(kwargs['low_latency'])
        return {'errors': 0, 'dropped_segments': 0}

    monkeypatch.setattr(audio, 'listen', listen)
    monkeypatch.setattr(BRIDGE.os, 'getppid', lambda: 123)
    assert BRIDGE.capture_audio(tmp_path, io.StringIO(), 'm', 'mic', 0, 123, 'alice', 'base.en') == 0
    assert received == [False]


def test_capture_failure_message_is_platform_neutral(tmp_path, monkeypatch):
    import meeting_copilot.audio as audio

    def fail(*args, **kwargs):
        raise RuntimeError('simulated microphone failure')

    monkeypatch.setattr(audio, 'listen', fail)
    monkeypatch.setattr(BRIDGE.os, 'getppid', lambda: 123)
    output = io.StringIO()

    assert BRIDGE.capture_audio(tmp_path, output, 'm', 'mic', 0, 123, 'alice', 'base.en') == 1

    error = json.loads(output.getvalue())['error']
    assert 'system microphone access' in error
    assert 'macOS' not in error


def test_finished_stalled_capture_clears_status_but_retains_fatal_error(desktop, monkeypatch):
    capture = FakeCapture(desktop.root, 'm', 'mic', 0, 'alice', 'base.en')
    capture.recording_status.update(stalled=True, warning='No audio received.')
    capture.error = 'Recording stopped unexpectedly.'
    capture.finished.set()
    desktop.capture = capture
    detail = desktop._detail('m')
    assert not detail['recording'] and 'recordingStatus' not in detail
    assert detail['recordingError'] == 'Recording stopped unexpectedly.'


def test_finished_capture_persists_allowlisted_session_health(desktop):
    capture = FakeCapture(desktop.root, 'm', 'mic', 0, 'alice', 'base.en', low_latency=True)
    capture.health = {
        'processed': 8, 'dropped_segments': 1, 'capture_stalls': 2,
        'speech_to_ready_p95_s': 1.357, 'unexpected': 'never persist transcript-like fields',
    }
    capture.finished.set()
    desktop.capture = capture

    detail = desktop._detail('m')

    assert not detail['recording']
    assert detail['sessionHealth'] == {
        'processed': 8, 'dropped_segments': 1, 'capture_stalls': 2,
        'speech_to_ready_p95_s': 1.357,
        'preset': 'low-latency', 'asrThreads': 4, 'pauseMs': 300,
    }


def test_recording_status_is_only_visible_for_the_active_meeting(desktop):
    desktop.store.create('alice', 'other', 'Other meeting')
    desktop.capture = FakeCapture(desktop.root, 'm', 'mic', 0, 'alice', 'base.en')
    desktop.capture.started = True
    desktop.capture.recording_status.update(stalled=True, warning='No audio received.')
    active = desktop._detail('m')
    other = desktop._detail('other')
    assert active['recordingStatus']['stalled']
    assert not other['recording'] and 'recordingStatus' not in other
