# Meeting Copilot v0.2 prototype

A local CLI for retained meeting/project evidence, private cited questions, three LangGraph task skills, background summaries and read-only MCP. No-key demo mode uses **SIMULATED transcript replay and deterministic extractive quotations**. Configured API mode adds model synthesis, explicitly separated from exact supporting quotes. Quote/reference checks do **not** prove semantic entailment.

**Start here:** [Chinese B/G/C/T/D/R handoff](handoff.md) includes the ready-to-use Windows path, full-repository clone/branch workflow, real-ID PowerShell flow, both-platform API/audio commands and cleanup. The local prepared `Testing Field` is an application copy, not a Git checkout. Develop in a separate full clone of [meeting-copilot/prototype-20261108](https://github.com/cyudaddy/5405/tree/meeting-copilot/prototype-20261108).

**Measured platforms:** actual Windows 11 / Python 3.12.14 repair3 source suite: **84 passed, one symlink-permission skip (85 cases), no failures/errors**; separate concurrency rerun passed, three audio sources enumerated, public-recording tiny.en ASR produced two segments, and native junction checks passed. See [Windows results](results/windows-validation.json), [ASR/preparation](results/windows-prepared.json), [junction](results/windows-junction.json), [source manifest](results/windows-source-manifest.json). Linux latest source: **85/85**; historical v0.2 wheel/README audits: **73 each**. macOS, real microphone recording and paid APIs remain unvalidated. Final completed Linux stability evidence is [audit-soak-final.json](results/audit-soak-final.json); it uses prerecorded audio and simulated QA.

Python 3.12 is recommended. From the full prototype-branch clone, enter the directory with its space:

```powershell
cd "Testing Field"
$env:PYTHONUTF8 = "1"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m meeting_copilot init
.\.venv\Scripts\python.exe -m meeting_copilot demo
.\.venv\Scripts\python.exe -m meeting_copilot evaluate --output results/local-report.json --stress
.\.venv\Scripts\python.exe -m pytest --basetemp data/test-temp -q
```

macOS/Linux equivalent:

```bash
cd "Testing Field"
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m meeting_copilot init
.venv/bin/python -m meeting_copilot demo
.venv/bin/python -m meeting_copilot evaluate --output results/local-report.json --stress
.venv/bin/python -m pytest --basetemp data/test-temp -q
```

`init` creates the trusted host `config.json`; `data_dir` is relative to that config. Do not rerun `init` over an existing config. Demo needs no audio package, model download, key or GPU. Its synthetic project is created in `demo-project/` beside `data/`: approved sources must be separate from runtime data. Linux and Windows source/CLI are tested; macOS and real microphone hardware recording remain pending. Windows Unicode device names/JSON should use `PYTHONUTF8=1`; this is a shell encoding setting.

Replay, project questions and export:

```bash
.venv/bin/python -m meeting_copilot create design --title "Design review"
.venv/bin/python -m meeting_copilot replay design examples/replay.jsonl
.venv/bin/python -m meeting_copilot approve-folder design examples/project
# Substitute the returned opaque ID for FOLDER_ID:
.venv/bin/python -m meeting_copilot refresh-folder FOLDER_ID
.venv/bin/python -m meeting_copilot ask design "Compare SQLite and Kafka deployment" --folder FOLDER_ID --skill compare_options --conversation design-notes
# Substitute snapshot_id from the answer for SNAPSHOT_ID:
.venv/bin/python -m meeting_copilot export SNAPSHOT_ID data/meeting-export.json --conversation design-notes
.venv/bin/python -m meeting_copilot checkpoint SNAPSHOT_ID --conversation design-notes
.venv/bin/python -m meeting_copilot history SNAPSHOT_ID --conversation design-notes
```

Use `--previous MEETING_ID` to opt into an authorized past meeting. Use the same `--conversation` and scope on subsequent `ask` commands for continuity; each new question pins a fresh snapshot. `history SNAPSHOT_ID --across-snapshots --conversation design-notes` shows eligible earlier turns, each retaining its own snapshot. Old context is excluded when its meeting/folder is unselected, revoked, or newer than the question snapshot. Exports include the first 100 sources **plus all sources cited by that snapshot's answers/summary**, and flag remaining truncation.

For actual API mode, edit config: `provider` = `openai` or `anthropic`, `model` = the provider model ID without a prefix, `base_url` = OpenAI-compatible URL including `/v1`, or Anthropic origin (adapter appends `/v1/messages`), and `send_evidence` = `true`. Set your own `MEETING_COPILOT_API_KEY` in the shell. Selected excerpts, current question and scoped prior private context go to that endpoint. Optional `streaming: true` normalizes SSE internally and buffers output until citation checks finish; it does not print unchecked partial answers. `tool_rounds: 1` or `2` enables bounded `search_evidence`/`read_evidence` rounds bound to the same host snapshot. They expose no shell, writes, identity or permission changes. Default is `0`.

The coordinator-prepared local Windows config uses `provider: "demo"` and `timeout_seconds: 120`; shipped defaults/examples still use 20. Recommend **120 seconds for first Windows API initialization**, and 60 only after measuring local startup. `streaming` buffers until citation validation, so an ask can remain quiet while working. Cooperative timeouts cannot preempt blocking synchronous imports. Codex subscription login/quota is separate from this app's API credentials and billing. Full JSON and environment-key examples for both shells are in [handoff.md](handoff.md#3-实际-api-模式自行提供凭据).

Set `input_cost_per_million` and `output_cost_per_million` to your actual rates if desired; estimates are null without both, or when provider usage is absent/ambiguous. No live paid API has been validated here. OpenAI Chat Completions and Anthropic Messages have separate local fake HTTP tests covering streaming, tools, usage, errors and cancellation.

For a live session, install the audio extra and select an enumerated microphone. Commands below use Linux/macOS Python syntax; on Windows substitute `.\.venv\Scripts\python.exe`:

```bash
.venv/bin/python -m pip install -e '.[audio]'
.venv/bin/python -m meeting_copilot mic-list
.venv/bin/python -m meeting_copilot create live --title "Live design discussion"
# Terminal A: substitute DEVICE_ID, e.g. sounddevice:7 or an enumerated WASAPI ID.
# --seconds 0 runs until Ctrl+C. First download needs network; model stays in data/.
.venv/bin/python -m meeting_copilot listen live --source DEVICE_ID --seconds 0 --consent --download-model --summary-interval 15
# Terminal B, same directory/config while A continues:
.venv/bin/python -m meeting_copilot ask live "What have we decided?" --skill catch_up --conversation my-notes
.venv/bin/python -m meeting_copilot ask live "Why was it chosen?" --conversation my-notes
.venv/bin/python -m meeting_copilot live-status live
```

Participant consent is required. Recording/ASR and summary workers are independent of private questions; stderr reports queue depths, drops and summary failures every five seconds. Ctrl+C stops capture, ingests the final partial chunk and drains accepted work. A slow API can delay shutdown up to the configured timeout. Shared summaries use current-meeting transcripts only. The server lacks PortAudio/microphone hardware: selected-device wiring and multi-process concurrency are tested with simulated capture; the separate wall-clock soak uses a public prerecorded file with real local ASR. Neither is a microphone quality test.

`mcp SNAPSHOT_ID` runs ten guarded read-only tools over stdio for an MCP client; Ctrl+C stops it. `get_transcript(offset=...)` and `list_project_files(offset=...)` paginate retained sources. A snapshot never grants access after revocation. `promote`, `grant`, `revoke`, `revoke-folder` and `cleanup` are explicit host CLI operations, not model tools. The trusted-local-machine boundary does not protect against an OS administrator or someone editing the trusted host config.

Optional **paid** evaluation commands, only after configuring your own endpoint:

```bash
# 88 API calls over frozen synthetic questions/ablations, tools disabled for equal retrieval context:
.venv/bin/python -m meeting_copilot evaluate --api --output results/local-api-eval.json
# 22 calls through unchanged upstream LiveAssistantService and repository:
.venv/bin/python -m meeting_copilot native-baseline --output results/local-native-baseline.json
```

The native runner uses a documented common-transport bridge and synthetic parent DB rows; it is not the full desktop/native RAG stack. Compare runs using the same provider/model/hardware; expected-term scores are not human correctness or semantic support. The policy ablation runs only against fresh synthetic evaluator data.

Current feature mapping: [requirements-current.json](results/requirements-current.json). Portable delivery summary: [server-delivery.json](results/server-delivery.json). Historical results and count boundaries: [results index](results/README.md). Chinese handoff: [handoff.md](handoff.md). Independent findings, validation and limitations: [docs/INDEPENDENT_REVIEW.md](docs/INDEPENDENT_REVIEW.md). Upstream MIT attribution: [docs/UPSTREAM_SOURCE.json](docs/UPSTREAM_SOURCE.json), [docs/UPSTREAM_LICENSE.txt](docs/UPSTREAM_LICENSE.txt). The original v0.1 tag and historical build report are preserved.

The build root remains intact for coordinator collection. Collect and verify the docs/source/results and local application copy first; only then may the coordinator remove the dedicated server root. Append a cleanup receipt locally and in the final Git commit **after observing removal**, then create the final verified-handoff tag. Existing release tags remain unchanged.
