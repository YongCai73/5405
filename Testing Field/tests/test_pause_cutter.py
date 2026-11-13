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


def test_latency_summary():
    s = _latency_summary([1.0, 2.0, 3.0, 4.0, 10.0])
    assert s['speech_to_ready_mean_s'] == 4.0
    assert s['speech_to_ready_p50_s'] == 3.0
    assert s['speech_to_ready_max_s'] == 10.0
    assert _latency_summary([])['speech_to_ready_count'] == 0
