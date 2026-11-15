import numpy as np

from meeting_copilot.audio import PauseCutter, _latency_summary

RATE = 16000


def tone(seconds, amplitude):
    t = np.arange(int(RATE * seconds)) / RATE
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype('<i2').tobytes()


def quiet(seconds, amplitude=20):
    rng = np.random.default_rng(0)
    return rng.integers(-amplitude, amplitude, int(RATE * seconds)).astype('<i2').tobytes()


def feed(cutter, pcm, batch_seconds=0.1):
    """Feed audio in 100 ms batches like the capture loop; return the time (s) the cutter fired."""
    step = int(RATE * batch_seconds) * 2
    for i in range(0, len(pcm), step):
        if cutter.update(pcm[i:i + step], RATE, 1):
            return (i + step) / 2 / RATE
    return None


def test_cuts_shortly_after_speech_ends():
    cutter = PauseCutter(pause_ms=500, min_seconds=1.0)
    fired = feed(cutter, quiet(0.5) + tone(2.0, 3000) + quiet(2.0))
    assert fired is not None and 2.9 <= fired <= 3.2


def test_cuts_in_a_noisy_room():
    # Background noise (RMS ~350) is louder than the absolute speech floor.
    cutter = PauseCutter(pause_ms=500, min_seconds=1.0)
    noisy = quiet(1.0, 600) + tone(2.0, 3000) + quiet(2.0, 600)
    fired = feed(cutter, noisy)
    assert fired is not None and 3.4 <= fired <= 3.8
    assert 250 < cutter.noise < 500


def test_silence_alone_never_cuts():
    cutter = PauseCutter(pause_ms=500, min_seconds=1.0)
    assert feed(cutter, quiet(5.0)) is None
    assert cutter.speech is False


def test_short_pause_inside_speech_does_not_cut():
    cutter = PauseCutter(pause_ms=500, min_seconds=1.0)
    assert feed(cutter, tone(1.5, 3000) + quiet(0.2) + tone(1.0, 3000)) is None


def test_reset_starts_a_new_chunk_but_keeps_noise_estimate():
    cutter = PauseCutter()
    feed(cutter, quiet(1.0) + tone(1.0, 3000))
    noise = cutter.noise
    cutter.reset()
    assert cutter.speech is False and cutter.chunk_ms == 0 and cutter.noise == noise


def test_quiet_speech_is_not_treated_as_silence():
    # Speech at RMS ~210 never reaches the 400 speech threshold, but must not be skipped.
    cutter = PauseCutter(speech_rms=400)
    feed(cutter, quiet(0.5) + tone(2.0, 300))
    assert cutter.speech is False
    assert cutter.is_silent(50) is False


def test_near_silence_is_skippable():
    cutter = PauseCutter(speech_rms=400)
    feed(cutter, quiet(2.0, 20))
    assert cutter.is_silent(50) is True


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def fake_capture(monkeypatch, audio_module, batches, sources=('sounddevice:7',)):
    """Patch capture/ASR: the backend returns `batches` in order, then no more audio."""
    from types import SimpleNamespace
    pending = list(batches)

    class Backend:
        def list_sources(self):
            return [SimpleNamespace(id=s, kind='microphone', name='Fake mic') for s in sources]

        def start(self, **kwargs):
            pass

        def drain_frames(self):
            return pending.pop(0) if pending else None

        def stop(self):
            pass

        def shutdown(self):
            pass

    class ASR:
        def __init__(self, *args):
            pass

        def transcribe(self, path):
            return [SimpleNamespace(text='Before the headset dropped.', start_ms=0, end_ms=500)]

        def close(self):
            pass

    clock = FakeClock()
    monkeypatch.setattr(audio_module, 'create_audio_capture_backend', Backend)
    monkeypatch.setattr(audio_module, 'LocalASR', ASR)
    monkeypatch.setattr(audio_module.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(audio_module.time, 'sleep', clock.sleep)


def test_unknown_device_id_gives_a_clear_error(monkeypatch, store):
    import pytest
    import meeting_copilot.audio as audio
    fake_capture(monkeypatch, audio, [], sources=('sounddevice:7',))
    with pytest.raises(ValueError, match="'sounddevice:99' not found.*mic-list.*sounddevice:7"):
        audio.listen(store, 'alice', 'm', store.root, 'sounddevice:99', 5, True)


def test_disconnect_is_reported_and_earlier_speech_is_kept(monkeypatch, store):
    from types import SimpleNamespace
    import meeting_copilot.audio as audio
    speech = tone(1.0, 3000)
    batch = SimpleNamespace(sample_rate=RATE, channels=1, start_frame=0, end_frame=RATE, pcm_s16le=speech)
    fake_capture(monkeypatch, audio, [batch])
    events = []
    result = audio.listen(store, 'alice', 'm', store.root, 'sounddevice:7', 10, True,
                          on_status=events.append, status_interval=100)
    stalled = [e for e in events if e.get('event') == 'capture_stalled']
    assert len(stalled) == 1 and 'disconnected' in stalled[0]['warning']
    assert result['capture_stalls'] == 1 and result['longest_stall_seconds'] >= 3
    assert result["processed"] == 1, result


def test_latency_summary():
    s = _latency_summary([1.0, 2.0, 3.0, 4.0, 10.0])
    assert s['speech_to_ready_mean_s'] == 4.0
    assert s['speech_to_ready_p50_s'] == 3.0
    assert s['speech_to_ready_max_s'] == 10.0
    assert _latency_summary([])['speech_to_ready_count'] == 0


def test_glossary_prompt_dedupes_and_limits():
    from meeting_copilot.audio import glossary_prompt, read_terms
    assert glossary_prompt([]) is None
    assert glossary_prompt(['SQLite', ' sqlite ', 'Kafka', '']) == 'Glossary: SQLite, Kafka.'
    long = glossary_prompt([f'term{i}' for i in range(500)])
    assert long.endswith('.') and len(long) <= 600
    assert read_terms('SQLite, Kafka\nLangGraph') == ['SQLite', 'Kafka', 'LangGraph']


def test_glossary_reaches_faster_whisper_request(monkeypatch):
    import meeting_copilot.audio as audio
    seen = {}

    class Engine:
        def __init__(self, *args):
            pass

        async def transcribe(self, request, *args):
            seen['options'] = request.provider_options
            from types import SimpleNamespace
            return SimpleNamespace(segments=[])

    import local_meeting_ai.adapters.transcription.faster_whisper as fw
    monkeypatch.setattr(fw, 'FasterWhisperEngine', Engine)
    audio.LocalASR('.', 'base.en', False, ['SQLite', 'Kafka']).transcribe('x.wav')
    assert seen['options'] == {'initial_prompt': 'Glossary: SQLite, Kafka.'}
    audio.LocalASR('.', 'base.en', False).transcribe('x.wav')
    assert seen['options'] == {}
