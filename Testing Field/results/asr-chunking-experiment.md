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

## 4. 20-minute run (2026-11-12)

Setup: English technical lecture (YouTube) played through the laptop speakers and picked up by the built-in microphone (`pyaudio:9`), 1200 s.
Settings: base.en, pause cutting, `MC_ASR_THREADS=4`, `MC_SPEECH_RMS=400`, `MC_PAUSE_MS=300`, buffer 8192.

| Metric | Value |
|---|---|
| errors / summary failures | 0 / 0 (20 background summaries completed) |
| Audio missing | 3.0 s of 1200 s (0.25%); `input_overflows` 0 |
| Segments committed | 60 |
| `speech_to_ready` mean / p50 / p95 / max | 7.0 / 6.3 / 12.4 / 13.7 s |
| Cuts | 86 at pauses, 95 at the 10 s maximum |
| Chunks skipped as silence | **94** |
| Frames classified as speech | 6% |
| Audio queue | peaked at 4 of 4; 1 chunk dropped (`dropped_segments` 1) |

Findings:
- **Stable**: 20 minutes with no errors, no summary failures and almost no lost audio.
- **Problem: quiet speech is skipped.** The 400 RMS threshold was tuned for someone speaking close to the microphone. Speech played from the speakers was quieter, so 94 chunks were treated as silence and never transcribed, and only 60 segments were committed for a 20-minute lecture. In a real meeting, people far from the microphone could be lost the same way.
- Continuous lecture speech has few pauses, so many chunks ran to the 10 s maximum. Latency was higher than in the 60 s read-aloud runs (mean 7.0 s vs 5.1 s).
- ASR fell behind once (queue full), dropping one chunk.

Proposed fix (not implemented yet): use separate thresholds for cutting (keep ~400) and for skipping silence (much lower, e.g. 50), so quiet speech is still transcribed.

## 5. Microphone disconnect test (2026-11-14)

Setup: AirPods used as the microphone (`pyaudio:14`, Bluetooth hands-free), 120 s run. After reading the script (~43 s), the AirPods were put back in their case. Recording was stopped with Ctrl+C at 66 s.

| Time (s) | Audio received (s) | Missing (s) |
|---|---|---|
| 41 | 36.9 | 4.2 |
| 46 | 38.9 | 7.3 |
| 51 | 38.9 | 12.3 |
| 61 | 38.9 | 22.3 |
| 66 (Ctrl+C) | 38.9 | 27.3 |

Findings:
- **No crash**: `errors` 0. Segments transcribed before the disconnect (5) were kept, and Ctrl+C shut down cleanly with full stats.
- **Disconnect is not detected**: after the device disappeared, no new audio arrived but the program kept "recording" silently with no error or warning. A user would not know that later speech is being lost. `capture_missing_seconds` shows it, but nothing acts on it.
- **Device IDs change** whenever a Bluetooth device connects or disconnects (the built-in mic moved between `pyaudio:9`, `12` and `15`). Run `mic-list` right before each recording. A wrong ID gives a generic `ValidationError` ("Operation failed") that does not say the device was not found.
- Bluetooth hands-free mic: about 1–2 s of audio is missing at the start while the AirPods switch profile.

Proposed fixes (not implemented yet): warn when no new audio has arrived for a few seconds ("microphone may be disconnected"), and show the real reason when the selected device does not exist.

## 6. Fixes for sections 3–5 and verification (2026-11-14)

### Fixes
1. **Quiet speech kept**: a chunk is skipped only if it never exceeds `MC_SILENCE_RMS` (default 50), separate from the speech threshold used for cutting (`MC_SPEECH_RMS`). New stat `quiet_chunks_sent`. The noise estimate is not used for skipping, because if recording starts mid-sentence it begins at speech level and hid real speech (found by a unit test).
2. **Disconnect warning**: if no audio arrives for `MC_STALL_SECONDS` (default 3), the CLI prints a `capture_stalled` event with a warning, pending audio is transcribed immediately, and `capture_resumed` is printed if audio returns. New stats `capture_stalls`, `longest_stall_seconds`.
3. **Clear error for a wrong device ID**: `listen` checks `--source` first and lists the current microphones, instead of the generic "Operation failed".
4. **ASR model loaded before capture starts**: the model used to load on the first chunk (6.6 s of CPU on this laptop), starving the microphone callback. This, not general CPU load, was the cause of the lost audio in section 3: about 3 s was lost once per recording, whether it lasted 1 or 20 minutes.

### Finding: the laptop microphone cancels its own speaker output
The built-in microphone applies echo cancellation, so a video played from the same laptop's speakers barely reaches it. This explains most of the 94 skipped chunks in section 4.

| Playback (60 s, built-in mic) | Segments | Speech frames | Pause / max cuts | Mean latency |
|---|---|---|---|---|
| Laptop speakers | 2 | 1% | 2 / 5 | 9.0 s |
| Phone next to the laptop | 19 | 49% | 14 / 0 | 5.1 s |

Use a separate device for played-back test audio.

### Verification (real devices)

| Run | Audio missing | Stalls | mean / p50 / p95 latency | Notes |
|---|---|---|---|---|
| Phone playback, before warm-up | 3.45 s | 1 (3.7 s) | 5.1 / 4.5 / 9.1 s | the startup loss shows up as a stall |
| **Phone playback, with warm-up** | **0.41 s** | **0** | **2.0 / 1.5 / 4.1 s** | 17 segments, 1 quiet chunk kept |
| AirPods, put in case at ~25 s | 2.3 s at start, then stop | 1 | 3.6 s | `capture_stalled` warning printed ~3 s after audio stopped; 5 segments from before the disconnect kept |

With AirPods, ~2.3 s is still missing at the start; this is the Bluetooth headset switching to hands-free mode, not the ASR model (built-in mic: 0.1 s).

Overall latency progression: ~16 s worst case (prototype settings) → 6.2 s (base.en, 5 s chunks) → 5.1 s (pause cutting, larger buffer) → **2.0 s mean, 4.1 s p95** (model warm-up).

## Not yet tested / next steps

- Repeat the 20-minute run with phone playback and the fixes above.
- Vocabulary prompt for technical terms.
- macOS (`portaudio.py` backend has not been changed); needs a teammate with a Mac.
