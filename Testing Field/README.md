# Meeting Copilot v0.2 prototype

A local CLI for retained meeting/project evidence, private cited questions, three LangGraph task skills, background summaries and read-only MCP. No-key demo mode uses **SIMULATED transcript replay and deterministic extractive quotations**. Configured API mode adds model synthesis, explicitly separated from exact supporting quotes. Quote/reference checks do **not** prove semantic entailment. [source: src/meeting_copilot/assistant.py]

Python 3.12 is recommended. From a clean clone, enter the directory with its space:

```powershell
cd "Testing Field"
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

`init` creates the trusted host `config.json`; `data_dir` is relative to that config. Do not rerun `init` over an existing config. Demo needs no audio package, model download, key or GPU. Its synthetic project is created in `demo-project/` beside `data/`: approved sources must be separate from runtime data. Linux is tested; Windows/macOS install and hardware smoke remain pending. [source: src/meeting_copilot/cli.py; src/meeting_copilot/replay.py; docs/INDEPENDENT_REVIEW.md]

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

Use `--previous MEETING_ID` to opt into an authorized past meeting. Use the same `--conversation` and scope on subsequent `ask` commands for continuity; each new question pins a fresh snapshot. `history SNAPSHOT_ID --across-snapshots --conversation design-notes` shows eligible earlier turns, each retaining its own snapshot. Old context is excluded when its meeting/folder is unselected, revoked, or newer than the question snapshot. Exports include the first 100 sources **plus all sources cited by that snapshot's answers/summary**, and flag remaining truncation. [source: src/meeting_copilot/store.py]

For actual API mode, edit config: `provider` = `openai` or `anthropic`, `model` = the provider model ID without a prefix, `base_url` = OpenAI-compatible URL including `/v1`, or Anthropic origin (adapter appends `/v1/messages`), and `send_evidence` = `true`. Set your own `MEETING_COPILOT_API_KEY` in the shell. Selected excerpts, current question and scoped prior private context go to that endpoint. Optional `streaming: true` normalizes SSE internally and buffers output until citation checks finish; it does not print unchecked partial answers. `tool_rounds: 1` or `2` enables bounded `search_evidence`/`read_evidence` rounds bound to the same host snapshot. They expose no shell, writes, identity or permission changes. Default is `0`. [source: src/meeting_copilot/provider.py; src/meeting_copilot/model_tools.py]

Set `input_cost_per_million` and `output_cost_per_million` to your actual rates if desired; estimates are null without both, or when provider usage is absent/ambiguous. No live paid API has been validated here. OpenAI Chat Completions and Anthropic Messages have separate local fake HTTP tests covering streaming, tools, usage, errors and cancellation. [source: tests/test_protocols.py]

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

Participant consent is required. Recording/ASR and summary workers are independent of private questions; stderr reports queue depths, drops and summary failures every five seconds. Ctrl+C stops capture, ingests the final partial chunk and drains accepted work. A slow API can delay shutdown up to the configured timeout. Shared summaries use current-meeting transcripts only. The server lacks PortAudio/microphone hardware: selected-device wiring and multi-process concurrency are tested with simulated capture; the separate wall-clock soak uses a public prerecorded file with real local ASR. Neither is a microphone quality test. [source: src/meeting_copilot/audio.py; tests/test_live_processes.py; scripts/prerecorded_soak.py]

`mcp SNAPSHOT_ID` runs ten guarded read-only tools over stdio for an MCP client; Ctrl+C stops it. `get_transcript(offset=...)` and `list_project_files(offset=...)` paginate retained sources. A snapshot never grants access after revocation. `promote`, `grant`, `revoke`, `revoke-folder` and `cleanup` are explicit host CLI operations, not model tools. The trusted-local-machine boundary does not protect against an OS administrator or someone editing the trusted host config. [source: src/meeting_copilot/mcp_server.py; src/meeting_copilot/cli.py]

Optional **paid** evaluation commands, only after configuring your own endpoint:

```bash
# 88 API calls over frozen synthetic questions/ablations, tools disabled for equal retrieval context:
.venv/bin/python -m meeting_copilot evaluate --api --output results/local-api-eval.json
# 22 calls through unchanged upstream LiveAssistantService and repository:
.venv/bin/python -m meeting_copilot native-baseline --output results/local-native-baseline.json
```

The native runner uses a documented common-transport bridge and synthetic parent DB rows; it is not the full desktop/native RAG stack. Compare runs using the same provider/model/hardware; expected-term scores are not human correctness or semantic support. The policy ablation runs only against fresh synthetic evaluator data. [source: src/meeting_copilot/native_baseline.py; src/meeting_copilot/evaluation.py]

Chinese handoff: [handoff.md](handoff.md). Independent findings, validation and limitations: [docs/INDEPENDENT_REVIEW.md](docs/INDEPENDENT_REVIEW.md). Upstream MIT attribution: [docs/UPSTREAM_SOURCE.json](docs/UPSTREAM_SOURCE.json), [docs/UPSTREAM_LICENSE.txt](docs/UPSTREAM_LICENSE.txt). The original v0.1 tag and historical build report are preserved.
