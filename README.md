# Agentic Meeting Assistant (Meeting Copilot)

**ENMGT 5405 final project.** A private, local-first meeting assistant. It
transcribes a meeting on your own computer, keeps the transcript in a local
store, and answers private questions with citations that point back to the
exact transcript segments or approved project files.

The application is called **Meeting Copilot**. Current version: **0.3.1**.

## Download

| Package | File | SHA-256 |
|---|---|---|
| **0.3.0 Windows portable zip** (pre-release) | [MeetingCopilot_0.3.0-windows-x64.zip](https://github.com/YongCai73/5405/releases/download/v0.3.0/MeetingCopilot_0.3.0-windows-x64.zip) | `555d96465fc6ab829d50a9d2fe6b26db5bffbc79fa4c4b36ef2d01e033900323` |

Unzip it anywhere and double-click `Start Meeting Copilot.cmd`. Nothing is
installed, and the PC needs no Python, .NET SDK or source checkout. It needs
the Microsoft Edge WebView2 Runtime, which Windows 11 includes. The build is
unsigned, so Windows may warn about an unknown publisher. See the
[release page](https://github.com/YongCai73/5405/releases/tag/v0.3.0).

Version 0.3.1 changes documentation and repository layout only, so the 0.3.0
package is still the current program. There is no macOS package; on a Mac,
build from source as described below.

## What it does

- **Local transcription.** Records from a microphone or imports audio and
  transcribes it on the computer with Faster Whisper.
- **Private questions with citations.** Each answer quotes the transcript or
  approved project files, and a click on a citation opens the source. A
  question only sees what had been said when it was asked.
- **Skills.** A router picks a skill for each question (`catch_up`,
  `compare_options`, `trace_decision`, `clarify`, `grill_me`,
  `design_review`), or you choose one. Each skill's tool limits are enforced
  in code.
- **Agentic loops, both bounded.** The model may look up more evidence itself,
  and when a first search finds no support the assistant rewrites the query
  and searches again.
- **Retrieval for long meetings.** Matching runs on overlapping time windows,
  the model reads continuous passages, and citations map back to the original
  segments.
- **Answers beyond the record.** Background knowledge from the model is
  allowed and is labelled apart from quotations.
- **Meeting notes.** A summary with decisions, action items and open
  questions, each linked to the transcript.
- **Desktop app for Windows and macOS** on one shared backend and window, with
  the model, address and API key set once in Settings. Keys are stored in the
  system credential store, never in a file.
- **Read-only MCP server** for external clients, behind the same access checks.
- **Works without a model.** With no API key the app answers from local
  excerpts, and the sample meeting always loads.

## Quick start from source

Everything runnable lives in [`Testing Field/`](Testing%20Field/). You need
Python 3.11 or newer (3.12 recommended). Building the Windows app also needs
the .NET 8 SDK; building the macOS app needs the Xcode Command Line Tools.

```powershell
git clone https://github.com/YongCai73/5405.git
cd "5405\Testing Field"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,audio,desktop]"
```

Then double-click `Start Meeting Copilot.cmd` (Windows) or
`Start Meeting Copilot.command` (macOS). The first launch builds the app.
`desktop\launch.cmd doctor` reports anything that is missing.

To use a real model, open **Settings** in the app, choose a provider, paste
the API key and allow excerpts to be sent to that provider. The project was
validated with DeepSeek through its OpenAI-compatible address.

The command-line interface, including the no-key demo, is described in the
[application README](Testing%20Field/README.md).

## Repository layout

| Path | Contents |
|---|---|
| `Testing Field/src/` | Python backend: storage, retrieval, skills, providers, MCP server, CLI |
| `Testing Field/desktop/` | Shared web UI and bridge, the Windows (WPF + WebView2) and macOS (Swift) hosts, launchers and build scripts |
| `Testing Field/tests/` | Python test suite and desktop UI tests |
| `Testing Field/docs/` | Design notes, security notes and status records |
| `Testing Field/results/` | Validation records and the evidence index |
| `Testing Field/examples/`, `Testing Field/scripts/` | Sample meeting data and helper scripts |
| `docs/` | The team's original plan: scope, interfaces, milestones, research notes |

## Documentation

| Topic | Document |
|---|---|
| Opening and building the desktop app, saved keys, portable package | [desktop/LAUNCH.md](Testing%20Field/desktop/LAUNCH.md) |
| Desktop features | [desktop/README.md](Testing%20Field/desktop/README.md), [Windows host](Testing%20Field/desktop/windows/README.md) |
| CLI, configuration, live recording, MCP | [Testing Field/README.md](Testing%20Field/README.md) |
| Step-by-step handoff guide (Chinese) | [handoff.md](Testing%20Field/handoff.md) |
| Skills, routing, tool limits, agentic loops | [MCP_SKILLS.md](Testing%20Field/docs/MCP_SKILLS.md), [DISCUSSION_SKILLS.md](Testing%20Field/docs/DISCUSSION_SKILLS.md) |
| Security and guardrails | [SECURITY.md](Testing%20Field/docs/SECURITY.md) |
| Crash recovery and storage | [CRASH_RECOVERY.md](Testing%20Field/docs/CRASH_RECOVERY.md), [RECOVERY_STORAGE.md](Testing%20Field/docs/RECOVERY_STORAGE.md) |
| Evaluation method and status | [docs/evaluation.md](docs/evaluation.md), [EVALUATION_STATUS.md](Testing%20Field/docs/EVALUATION_STATUS.md) |
| Validation records | [results/README.md](Testing%20Field/results/README.md) |
| Release history | [CHANGELOG.md](CHANGELOG.md) |
| Original project plan | [PROJECT_SCOPE.md](docs/PROJECT_SCOPE.md), [INTERFACES.md](docs/INTERFACES.md), [MILESTONES.md](docs/MILESTONES.md), [RESEARCH.md](docs/RESEARCH.md), [original README](docs/ORIGINAL_ROOT_README.md) |

## Testing

From `Testing Field`:

```powershell
desktop\launch.cmd test
```

This runs the Python suite and the desktop UI tests. On Windows 11 the Python
suite gives 390 passed and 10 platform-specific skips, and the 14 UI tests
pass. `python -m meeting_copilot evaluate --stress --strict` runs the
deterministic release gate. Real-endpoint checks are recorded in
[results/](Testing%20Field/results/README.md).

## Known limits

- The macOS app was not compiled or run on a Mac for 0.3.0 or 0.3.1. It shares
  the window and backend with Windows, but the host and launcher are untested
  there.
- The Windows package is unsigned and has not been tried on a PC that never
  had Python or .NET installed.
- Real-model checks used one provider and model (`deepseek-flash`). Other
  OpenAI-compatible endpoints and the Anthropic preset are untested.
- Citation checks confirm that a quotation exists in the source. They do not
  prove that the answer follows from it, so important conclusions still need a
  human check.
- Real microphone sessions of a full-length meeting, and speaker
  identification, are not validated.

## Workstreams

| Workstream | Scope |
|---|---|
| Orchestration and integration | Transcription ingestion, concurrent summary and private Q&A, routing, bounded retries (LangGraph) |
| Memory and context | Timestamped transcript store, meeting-scoped retrieval, provenance (SQLite) |
| MCP and skills | Read-only tools, skill routing, contracts |
| Security and guardrails | Authorization, private/shared separation, prompt-injection defence |
| Evaluation and observability | Replayable tests, traces, baseline comparison, metrics |

Meeting speech and retrieved documents are treated as evidence, never as
instructions. Private questions do not enter shared notes automatically.

## Provenance and license

The prototype reuses attributed code from
[Meet2Notes](https://github.com/estebanstifli/Meet2Notes) under the MIT
license; see the [upstream record](Testing%20Field/docs/UPSTREAM_SOURCE.json)
and [MIT notice](Testing%20Field/docs/UPSTREAM_LICENSE.txt). No license has
been chosen yet for the team's own code. Third-party components keep their
own licenses.

Use synthetic or consented recordings. Do not send private meeting audio or
transcripts to a third party without the participants' consent.
