> Historical v0.1 build record. For v0.2 fixes and final independent validation, see [INDEPENDENT_REVIEW.md](INDEPENDENT_REVIEW.md). Earlier deferrals/counts below describe the initial release.

# Actual compatibility experiments

All experiments were local to the dedicated build root. No custom capture/ASR/provider replacement was introduced after a failed dependency import. Logs here are sanitized summaries, not invented attempts. Exact upstream pin and copied-file hashes are in UPSTREAM_SOURCE.json.

## A. Faster Whisper 1.2.1 / PyAV 19.0.2 (actual incompatibility; three methods)

| Method actually attempted | Evidence/outcome | Choice |
|---|---|---|
| 1. Run the unchanged vendored upstream `FasterWhisperEngine` on JFK FLAC using current resolved dependencies | Model downloaded; `TypeError: open() got an unexpected keyword argument 'metadata_errors'`. Faster Whisper `audio.py:46` passes this keyword to PyAV 19.0.2. | Failed file decoder, not model/CPU inference. |
| 2. Explicitly decode the same FLAC with `av.open` + mono 16k resampling, pass float32 ndarray into Faster Whisper | Successful actual CPU transcription, 11.0-second sample, 1.445s inference including model creation in that probe. Text: “And so my fellow Americans ask not what your country can do for you ask what you can do for your country”. | Feasible alternative seam, NOT shipped as custom ASR. |
| 3. Install `av>=16,<17` (16.1.0 wheel on aarch64) and rerun unchanged upstream engine | Successful, 2 segments, 2.054s including model load, CPU int8/2 threads. Exact evidence in ../results/asr-smoke.json. | **Selected dependency pin**, preserves actual upstream source. |

No ASR accuracy/WER claim follows from this single nontechnical recorded sample. Models/audio downloads are not committed. Audio package imports remain lazy; replay works without them.

## B. Native historical MCP integration (architecture adaptation, no failed protocol replacement)

Three integration approaches are exercised and recorded in `results/mcp-compatibility.json`: (1) load original pinned native server with installed SDK, (2) call original `Meet2NotesGateway` against a controlled legacy completed-transcript HTTP mock before/after a rewrite, (3) run adapted native historical registration through actual MCP stdio with a host-bound retained-evidence service. The mock is a protocol probe, not a native desktop baseline. The final choice retains native read-only ToolAnnotations and historical read-tool registration, replaces its desktop gateway boundary with snapshot/ACL checks, and adds project tools. Full native desktop RAG/assistant was **not measured**. There is one new answer path.

## C. Media paths and CPU deployment (deliberate scope adaptations)

Actual upstream `MeetingStorage`, both microphone backends, combined capture/mixer, domain entities and Faster Whisper engine are vendored. Explicit app-owned `AppPaths` replaces home-directory discovery; a small CPU shim replaces CUDA discovery/installation (GPU support is outside scope). This is not a failed attempt to implement capture. Unit tests execute upstream media layout and scoped deletion. Linux server lacks PortAudio (`OSError: PortAudio library not found`); hardware and Windows/macOS microphone checks are deferred to Zimo. No fake microphone pass is reported.

## D. Provider boundary

Actual upstream LiteLLM parameter filter is reused by the async adapter; synchronous upstream retry helper stays available in the vendored module. Async calls use LiteLLM directly for cancellation. Both OpenAI Chat Completions and Anthropic Messages passed loopback HTTP protocol, roles, usage, error, timeout and cancellation tests. Non-streaming evidence selection only; model tool calls, streaming UI and universal vendor compatibility are deferred. No provider was replaced with a hand-written HTTP client; no live paid validation or credentials were available.

## E. Targeted retrieval fix

Frozen initial evaluation: 21/22 skill questions correct. q03 failed because `QUEUE_CAPACITY` was one ranking token while the question used separate words. Normalize identifiers into alphanumeric terms consistently with FTS5 tokenization. Rerun: 22/22 on the same frozen fixture, zero observed future leakage. This is a narrow deterministic fixture improvement, not evidence of general LLM superiority. Full before/after reports are retained.
