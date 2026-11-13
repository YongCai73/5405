# Live ASR experiments: chunking, VAD, latency and lost audio (Zimo Li, 2026-11-12)

Setup: Windows laptop, built-in Realtek microphone (`pyaudio:9`), CPU only, 60 s recordings,
English script read aloud with ~1 s pauses between sentences, Claude Haiku for `catch_up`.
Sample counts are small (12–23 segments per run), so p95 equals max in most runs.

## Changes on branch `zimo/asr-chunk-length`

1. Fixed chunk length 3 s → 10 s (now `MC_CHUNK_SECONDS`, the maximum chunk length).
2. `vad_filter` False → True (upstream Meet2Notes default).
3. New latency metric `speech_to_ready_*`: time from the end of a spoken segment to the moment it is committed and usable for Q&A (mean / p50 / p95 / max). The old `ingestion_lag_*` excludes the time spent waiting for a chunk to fill.
4. Pause-based cutting (`MC_CUT_MODE=pause`, default): a chunk is sent once speech is followed by a pause (`MC_PAUSE_MS`, default 500; loudness threshold `MC_SPEECH_RMS`, default 100). Silence-only chunks are skipped.
5. Lost-audio diagnostics: `captured_audio_seconds`, `capture_wall_seconds`, `capture_missing_seconds`, `input_overflows`.
6. WASAPI callback buffer 1024 → 8192 frames (`MC_CAPTURE_BUFFER_FRAMES`).
7. Experiment knob `MC_ASR_THREADS` (default 2).

## 1. Accuracy: chunk length and VAD

| Setting | ASR model | Accuracy (plain script) | Hallucination on silence | Sentences cut at chunk edge |
|---|---|---|---|---|
| 3 s, VAD off (prototype) | base.en | poor; technical terms wrong | yes ("good night", "James?") | often |
| 3 s, VAD off | small.en | slightly better; terms still wrong | yes | often |
| 10 s, VAD off | small.en | 3 of 4 sentences correct | not observed | sometimes |
| 10 s, VAD on | small.en | nearly all correct | none in 30 s of silence | sometimes |
| 5 s, VAD on | base.en | 1 sentence wrong, 1 half lost | none | sometimes |
| 3 s, VAD on | base.en | poor ("ride the kill this") | none | often |

Technical terms (SQLite, Kafka) were misrecognized in every 3 s run with both models.

## 2. Latency (`speech_to_ready`, seconds)

| Run | Model | Cutting | ASR threads | Capture buffer | mean | p50 | p95 / max | Audio missing |
|---|---|---|---|---|---|---|---|---|
| A | small.en | fixed 10 s | 2 | 1024 | 8.5 | 7.9 | 13.2 | not measured |
| B | small.en | fixed 10 s | 4 | 1024 | 8.4 | 8.8 | 11.6 | not measured |
| C | base.en | fixed 5 s | 4 | 1024 | 6.2 | 6.0 | 8.4 | not measured |
| D | base.en | fixed 3 s | 4 | 1024 | 6.7 | 6.1 | 10.0 / 11.7 | not measured |
| E | base.en | pause, threshold 400, pause 300 ms | 4 | 1024 | 6.1 | 5.9 | 9.5 | 5.3 s of 60.3 s |
| **F** | base.en | pause, threshold 400, pause 300 ms | 4 | **8192** | **5.1** | **4.9** | **7.1** | 3.6 s of 60.3 s |

- More ASR threads helped little (A → B).
- Fixed 3 s chunks were slower than 5 s: each chunk has a fixed processing overhead.
- Pause cutting works once the loudness threshold is above breath/echo noise: with threshold 100 only 5 of 8 cuts were at pauses; with 400 every cut was (18 / 0).
- With threshold 400 and 300 ms pauses, set `MC_SPEECH_RMS=400` and `MC_PAUSE_MS=300`. These are not yet the defaults; a quieter speaker may need a lower threshold.

## 3. Bug found: audio lost while ASR is busy

| Recording | Wall clock | Audio received | Missing |
|---|---|---|---|
| Speaking (ASR running), buffer 1024 | 60.3 s | 55.0 s | 5.3 s (~9%) |
| Silent (ASR idle), buffer 1024 | 60.2 s | 59.7 s | 0.5 s |
| Speaking, buffer 8192 | 60.3 s | 56.7 s | 3.6 s (~6%) |

The microphone callback runs late while local ASR keeps the CPU busy, and PortAudio drops input.
`dropped_segments` stayed 0 because it only checks continuity of the audio it received.
Effects: lost words (some sentences lost their first half) and inflated latency.
A larger buffer reduces the loss but does not remove it. A full fix is to run ASR in a separate process.

## Not yet tested / next steps

- Run ASR in a separate process so it cannot starve audio capture.
- Check `input_overflows` (reported from now on) in the next run.
- Vocabulary prompt for technical terms.
- 20-minute run (planned with a played-back English technical talk) and microphone unplug test.
- macOS (`portaudio.py` backend has not been changed).
