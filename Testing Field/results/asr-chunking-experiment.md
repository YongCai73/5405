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
- **Opt-in preset (2026-11-20):** `listen ... --low-latency` uses 4 ASR threads and a 300 ms pause cut. It is not the default until it is checked on macOS (team decision with Xiaolong). The speech threshold stays at the default 100, because 400 was tuned for one laptop microphone. Explicit `MC_*` variables still override the preset.

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

## 7. Technical terms: glossary prompt (2026-11-14)

New option: `listen ... --terms "SQLite, Kafka"` or `--terms-file terms.txt` (also on `asr`). The terms become a Whisper prompt (`Glossary: SQLite, Kafka.`, max 60 terms / 600 characters). Off by default. With a glossary, `condition_on_previous_text` is turned off (see below). The one-line change in the upstream `faster_whisper.py` adapter is recorded in `docs/UPSTREAM_SOURCE.json`.

Offline A/B on three earlier recordings of the SQLite/Kafka script (each read twice: SQLite said 6 times, Kafka 12 times in total), base.en, counted by hand:

| Audio given to ASR | Variant | SQLite correct | Kafka correct | Side effects |
|---|---|---|---|---|
| 5 s chunks (like live) | no glossary | 2 / 6 | 4 / 12 | "cough cough", "half cup", "killed night" |
| 5 s chunks | **glossary prompt** | **4 / 6** | **10 / 12** | a few words dropped at chunk edges, one "Mm." |
| 5 s chunks | `hotwords` | 4 / 6 | 11 / 12 | junk output ("M. M. M. M…", "a bad ass") |
| whole 60 s file | no glossary | 2 / 6 (+2 "SQL Lite") | 12 / 12 | — |
| whole 60 s file | glossary prompt | 6 / 6 | 12 / 12 | **invented sentences** ("Kafka is a very good user", "SQLite with the framework in Mac") |

Findings:
- **Context matters more than vocabulary.** Given the whole file, base.en already gets almost every term right. The original "SQ9" / "half cup" errors came mainly from cutting audio into 3 s pieces.
- On live-length chunks, the glossary prompt clearly helps (SQLite 2→4 of 6, Kafka 4→10 of 12) without inventing content.
- On long audio, prompt + carry-over of earlier text made Whisper invent sentences. Those would become false meeting evidence, which is worse than a misspelling. So the glossary is opt-in, carry-over is off when a glossary is used, and it is not recommended for whole-file `asr`.
- `hotwords` was similar for terms but produced more junk, so it is not used.

### Live microphone run with `--terms` (2026-11-14)

The SQLite/Kafka script was read three times (SQLite 3×, Kafka 6×) with the glossary on. The saved recording was then re-transcribed offline with the same pause-based chunking (15 chunks), with and without the glossary, so both use identical audio.

| | Segments | Junk / invented segments | SQLite correct | Kafka correct |
|---|---|---|---|---|
| Without glossary | 15 | 0 | 1 / 3 ("SQ light", "QL eyes") | 3 / 6 ("cough call", "cough cotton") |
| With glossary (offline) | 31 | **16** | 3 / 3 | 6 / 6 |
| With glossary (the live run) | 29 | ~15 | 2 / 3 ("adsqlite") | 6 / 6 |

Junk with the glossary included fillers ("Okay.", "call.", "Mhm.") and **invented sentences**: "And I love you boys.", "I will see you in the next one.", "I have to say, this isn't a problem."

The live run was also slower: mean latency 10.0 s, p95 14.1 s. There were twice as many segments, and processing fell behind (the queue took 13 s to drain after stopping).

**Conclusion: the glossary fixes the terms but makes Whisper invent speech in the short gaps between sentences.** Invented sentences become false meeting evidence, so the glossary stays **off by default** and is not recommended for real meetings yet.

### Junk filter (implemented 2026-11-14)

Whisper's own scores separate real speech from invented fragments in this recording:

| | Duration | Mean word probability | `no_speech_prob` |
|---|---|---|---|
| Real segments (both variants) | 1.7 – 7.0 s | 0.40 – 0.95 | ≤ 0.04 |
| Invented fragments (glossary) | ≤ 0.3 s | 0.01 – 0.52 | 0.08 – 0.54 |

A segment is now dropped (`junk_segments_dropped`; `MC_FILTER_JUNK=0` disables it) if it has no letters or digits, or is **shorter than 0.5 s with mean word probability < 0.6**, or has `no_speech_prob > 0.6` and `avg_logprob < -1.0` (Whisper's standard silence rule). A short but confident "Yes." is kept.

Same recording, same chunking, filter on:

| | Kept | Dropped | Invented text kept | SQLite | Kafka |
|---|---|---|---|---|---|
| No glossary | 15 | 0 | 0 | 1 / 3 | 3 / 6 |
| **Glossary + filter** | **15** | **13** | **0** | **3 / 3** | **6 / 6** |

False-drop check on a 60 s lecture recording (phone playback, no glossary): 20 segments kept, 2 dropped ("Thanks.", "Yeah."). Both are fillers we cannot confirm were spoken, and all content sentences were kept.

### Glossary speed: temperature fallback (2026-11-14)

A live run with glossary + filter had clean text (SQLite 3/3, Kafka 6/6, 13 fragments dropped, nothing invented kept) but was still slow: mean 8.2 s, p95 12.8 s, and about 11 s to process each chunk.

Cause: when a decode looks unreliable, Whisper retries at higher temperatures (0.2 … 1.0). With a glossary this happened on many chunks. Same 12 chunks, offline:

| Variant | Time per chunk | Segments before filter |
|---|---|---|
| No glossary | 1.8 s | 13 |
| Glossary, default retries | 8.0 s | 24 |
| Glossary, `temperature=0` (no retries) | 3.0 s | 23 |

With a glossary, `temperature` is now fixed at 0 (passed through `provider_options`; the upstream adapter change is recorded in `UPSTREAM_SOURCE.json`).

Final code path on the same recording (warm model, filter on):

| | Time per chunk | Kept | Dropped | SQLite | Kafka | Fillers that got through |
|---|---|---|---|---|---|---|
| No glossary | 1.7 s | 13 | 0 | 2 / 3 | 3 / 6 | — |
| **Glossary** | **3.6 s** | 15 | 8 | **3 / 3** | **6 / 6** | "Thank you.", "Good.", "Good." |

### Live confirmation (2026-11-14)

Real microphone, glossary on, no retries, filter on, script read ~3.5 times:

| Run | mean / p50 / p95 latency | Processing p95 | Dropped | SQLite | Kafka |
|---|---|---|---|---|---|
| Glossary + filter, with retries | 8.2 / 8.8 / 12.8 s | 11.4 s | 13 | 3 / 3 | 6 / 6 |
| **Glossary + filter, no retries** | **2.6 / 2.4 / 3.4 s** | **2.6 s** | 13 | **4 / 4** | **7 / 7** |

Only one problem got through in the no-retry run: "All right." five times in a row (a Whisper repetition loop). A short segment (≤ 3 words) that exactly repeats the previous kept segment is now also dropped (unit-tested). A single "All right." is still kept.

**Recommendation:** use `--terms` when the meeting's key terms are known. Live, it kept latency at 2.6 s mean / 3.4 s p95 and got every SQLite and Kafka right, with no invented content sentences. Without `--terms`, behaviour is unchanged.

Remaining idea:
- A second pass: transcribe longer windows (e.g. the last 30 s) without a glossary and store the result as a new revision. Section 7 shows full-context transcription gets the terms right without inventing text, and the evidence store already supports revisions.

Not yet done: building the glossary automatically from an approved project folder.

## Not yet tested / next steps

- Live run with `--terms`, and optionally build the glossary from the approved project folder.
- Repeat the 20-minute run with phone playback and the fixes above.
- Vocabulary prompt for technical terms.
- macOS (`portaudio.py` backend has not been changed); needs a teammate with a Mac.
