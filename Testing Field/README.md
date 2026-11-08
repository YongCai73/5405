# Meeting Copilot prototype

A local Python CLI with retained meeting/project evidence, private cited QA, three LangGraph task skills and read-only MCP stdio tools. The default demonstration is **SIMULATED transcript replay and deterministic extractive answers**. Real API mode selects validated quotations; it is an initial evidence assistant, not a validated synthesis or ASR accuracy product.

Python 3.12 is recommended. From a clean clone, enter this directory (the space matters):

```powershell
cd "Testing Field"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m meeting_copilot init
.\.venv\Scripts\python.exe -m meeting_copilot demo
.\.venv\Scripts\python.exe -m meeting_copilot evaluate --output results/local-report.json --stress
.\.venv\Scripts\python.exe -m pytest --basetemp data/test-temp -q
```

macOS/Linux equivalent (no activation required):

```bash
cd "Testing Field"
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m meeting_copilot init
.venv/bin/python -m meeting_copilot demo
.venv/bin/python -m meeting_copilot evaluate --output results/local-report.json --stress
.venv/bin/python -m pytest --basetemp data/test-temp -q
```

`init` creates the single trusted host `config.json`; `data_dir` resolves relative to that file. Keep config out of source control if customized. `demo` prints a snapshot, folder ID, exact citations and an export location. No key, audio package, microphone, model download, GPU or Docker is required. Avoid rerunning `init` over an existing file.

Replay your own events and ask:

```bash
.venv/bin/python -m meeting_copilot create design --title "Design review"
.venv/bin/python -m meeting_copilot replay design examples/replay.jsonl
.venv/bin/python -m meeting_copilot approve-folder design "examples/project"
# Copy the returned folder ID as FOLDER_ID:
.venv/bin/python -m meeting_copilot refresh-folder FOLDER_ID
.venv/bin/python -m meeting_copilot ask design "Compare SQLite and Kafka deployment" --folder FOLDER_ID --skill compare_options
# Copy SNAPSHOT_ID from answer; IDs are opaque strings, not the literal placeholders:
.venv/bin/python -m meeting_copilot export SNAPSHOT_ID "data/meeting-export.json"
.venv/bin/python -m meeting_copilot mcp SNAPSHOT_ID
```

MCP uses stdio and waits for an MCP client; stop with Ctrl+C. It exposes ten read-only tools, and accepts no user identity, filesystem root, shell or write command from models. Host config/OS user is trusted; changing the local config is not authentication against a malicious OS user. API calls receive only retrieved excerpts and the question. Private data is retained locally in SQLite; promotion is an explicit `promote` command labelled `USER SUPPLEMENT (unverified)`.

For microphone/ASR install `.[audio]`, then `mic-list`, `create live` and `listen live --source DEVICE_ID --seconds 30 --consent --download-model`. Select an enumerated `sounddevice:N` / WASAPI source ID. Consent must cover all participants. Downloads stay in configured app data. The server validated an 11-second recorded fixture with upstream Faster Whisper tiny.en CPU int8 and PyAV 16.1.0; it has no usable PortAudio microphone. Windows/macOS install/hardware tests are still required.

For an actual API, edit config: `provider` = `openai` or `anthropic`, `model` = provider model ID without prefix, `base_url` = OpenAI-compatible URL including `/v1`, or Anthropic origin (adapter appends `/v1/messages`), `send_evidence` = `true`. Set `MEETING_COPILOT_API_KEY` in the current shell. Never use Codex CLI authentication as an app key. `input_cost_per_million` and `output_cost_per_million` are optional user-entered prices; cost is null without both. Only non-streaming quotation selection is supported; no model-selected tools are executed. Both wire protocols passed local fake HTTP tests; no live paid endpoint was tested.

Detailed Chinese setup, correction, policy, export, stop and cleanup commands: [handoff.md](handoff.md). Build evidence and limits: [docs/BUILD_REPORT.md](docs/BUILD_REPORT.md), [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md), [results/report.json](results/report.json). Upstream MIT source provenance: [docs/UPSTREAM_SOURCE.json](docs/UPSTREAM_SOURCE.json), [docs/UPSTREAM_LICENSE.txt](docs/UPSTREAM_LICENSE.txt). Teammate root files/history are preserved.
