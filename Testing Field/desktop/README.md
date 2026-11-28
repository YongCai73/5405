# Meeting Copilot for macOS

Meeting Copilot opens as a desktop window with a meeting list, searchable transcripts, meeting notes and private questions. The first launch loads a sample meeting. Try **Compare SQLite and Kafka** and select a citation to inspect its source. Sample answers use quotes from the existing local retrieval pipeline and require no API key.

## Open with one click

After the one-time setup below, double-click **Start Meeting Copilot.command**
in `Testing Field`. It builds the app when needed and opens it, with no
environment variables. `./desktop/launch.sh doctor` reports anything missing,
and [LAUNCH.md](LAUNCH.md) lists every launcher command, including saving an
API key in the Keychain.

## Build and open

From `Testing Field`, use Python 3.11 or newer and the macOS Xcode Command Line Tools. Reuse the existing `.venv` if it already has the project and audio dependencies installed.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[audio]'
./desktop/build-macos.sh
open 'build/desktop/Meeting Copilot.app'
```

Pass an output directory to `build-macos.sh` to place the bundle elsewhere. After building, double-click **Meeting Copilot.app** in Finder. The build stages its Python runtime under `~/Library/Application Support/Meeting Copilot/Runtime` and uses the base Python installed on this Mac. Build locally on each Mac and quit the app before rebuilding. Set `MEETING_COPILOT_RUNTIME_ROOT` to an absolute path at build time if you need a separate runtime.

## Skills, feedback and settings in the window

Both desktop apps share `bridge.py`, the web UI in `ui/` and the Python backend,
so everything in this section behaves the same on macOS and Windows.

- **Question type.** The menu beside the question box lists `Auto` and every
  skill contract shipped in `src/meeting_copilot/skills/`. The list is read from
  the backend, so a new contract appears without a desktop change. `Auto` routes
  by the trigger phrases in the question, and each answer shows the skill that
  produced it. A `grill_me` session continues across messages while `Auto` is
  selected.
- **Ratings.** Each finished answer has helpful and not-helpful buttons; a
  not-helpful rating can carry a short note. Ratings are only read by
  **Suggest improvements**.
- **Suggest improvements** (sidebar) runs the `suggest` report on your own
  questions and ratings for the open meeting. It changes nothing; a suggested
  wording preference is saved only if you choose it. Sending question texts to
  the configured model is an explicit checkbox.
- **Settings** (sidebar) is where the model is set up, once: provider, model,
  base URL, API key and the permission to send excerpts to that provider. It
  takes effect from the next question without a restart, and **Test
  connection** checks it with a request that contains no meeting content. The
  key is typed into a password field, sent once to the local backend, and
  stored in the system credential store (macOS Keychain, Windows Credential
  Manager). It is never written to a file, logged, or sent back to the window.
  Each provider address has its own saved key: choosing another provider never
  sends the previous provider's key there, and Settings asks for that provider's
  key instead.
  The environment variable named by `api_key_env` still works and takes
  priority. Settings also holds up to five wording preferences. Until a model
  is set up, a hint under the question box links here. See
  [LAUNCH.md](LAUNCH.md#using-a-real-model).

Adding a bridge action needs one more step than adding a skill: the action name
must be added to the allowlist in `main.swift` and in
`windows/MainWindow.xaml.cs`. `tests/test_desktop_bridge.py` fails when either
list differs from the bridge.

## Answers beyond the meeting record

With a model set up, a question the meeting cannot answer is still answered.
What comes from the meeting is shown as exact quotations with citations, as
before. What comes from the model appears under its own heading, "Beyond the
meeting record (model knowledge and reasoning; NOT from the meeting or project
files, not verified)", and the answer's tag says "model knowledge, not from the
meeting" or "meeting + model knowledge". Quotation checks are unchanged. See
[MCP_SKILLS.md](../docs/MCP_SKILLS.md#open-answers-default-with-a-configured-model-added-2026-11-27)
for the rules and for `"general_answers": false`, which restores abstention.

## Questions about the whole meeting or a part of it

"Summarize this meeting", "what was said at the beginning" and similar questions
are answered from that part of the transcript rather than from keyword hits or
the last few lines, and a short meeting is read in full for any question. See
[MCP_SKILLS.md](../docs/MCP_SKILLS.md#how-much-of-the-transcript-a-question-gets-added-2026-11-27).

## Asking in Chinese

Questions can be typed in Chinese. The transcript, the citations and the window
itself stay English; the answer and its headings come back in Chinese, with the
quotations exactly as they are in the English record. How routing and retrieval
handle a Chinese question is described in
[MCP_SKILLS.md](../docs/MCP_SKILLS.md#questions-in-chinese-added-2026-11-27).

## Answer speed

Measured on 2026-11-27 with `deepseek-flash` on the sample meeting ("Who owns
evaluation?"), on a Windows laptop that was throttling its CPU to about 22 %:

| Stage | Time |
|---|---|
| Skill routing | 3 to 24 ms |
| Retrieval | about 1 ms |
| Model request | 1.0 to 1.6 s; thinking on, off or default made no measurable difference for this question |
| Showing the answer | the window used to look for it every 1.5 s; it now looks every 0.3 s while one is pending |
| Loading the model client, first question after each start | LiteLLM: 39 to 62 s throttled, 5.5 s when not. Direct HTTPS client: 2 s throttled |

The first question after every start was slow because the request went through
LiteLLM, whose import loads about three thousand modules. For an
OpenAI-compatible address (a provider with a base URL, such as DeepSeek or a
local server) the backend now sends the chat-completions request itself over
HTTPS and never loads LiteLLM. In the built app, in the same throttled state,
the first question right after opening went from 38 s (after waiting 12 s) and
173 s (reported as a provider timeout) to 3.5 s; later questions take 2.3 to
2.8 s, and an open answer 4.6 to 5.7 s because it is longer.

Anthropic and OpenAI's own address (no base URL) still use LiteLLM, where its
per-model parameter handling matters. For those the client is loaded in the
background when the app opens, and its loading time no longer counts against
the provider timeout. Only DeepSeek was measured.

## Work with local meetings

Private questions run in the background, so the transcript and recording controls
remain available while the model responds. After reopening the app, select the
meeting to see its recent private questions. Choose **Continue question** on an
unfinished one to reuse the evidence from when it was submitted. The app checks
current meeting and source access before showing or continuing a saved question.
Private questions are kept out of shared meeting notes and desktop exports.

Create a meeting to import an audio file or record from a microphone. Choose your input and start recording after participants agree. macOS asks for microphone permission on first use. Transcription runs in English with the local Faster Whisper model. The first audio operation may need to download the model.

### Recording presets and input status

The recording dialog has an optional **Low latency** setting. It is off each time
the dialog opens. Enabling it uses the same `listen(..., low_latency=True)` preset
as the CLI's `--low-latency`: four ASR threads and a 300 ms pause cut, instead of
the standard two threads and 500 ms. The speech-volume threshold is unchanged.
These settings were measured on Windows; the option remains experimental on Mac
until real-microphone comparisons establish its latency and transcription quality.

While recording, the desktop shows the active preset and effective settings.
Existing `MC_ASR_THREADS` and `MC_PAUSE_MS` environment overrides still take
precedence, so confirm the displayed values when comparing runs. Selecting a
preset does not change the application-wide defaults.

If audio frames stop arriving, the desktop displays a persistent input warning.
This means no audio has been received; it does not prove the microphone is
disconnected. Recording stays open so audio can resume, and **Stop recording**
remains available. When audio returns, the warning clears. Normal silence still
delivers audio frames and should not produce this warning.

Transcription model preparation now happens before capture begins. The desktop
allows up to 60 seconds for startup instead of treating a normal model warm-up as
an eight-second microphone failure. Wait until recording starts before speaking.

After you stop recording, the app finishes transcription and opens **Summary**. Meeting notes include an overview, discussion topics, decisions, action items, and open questions when the transcript supports them. Select a timestamp to see the source. The app saves the notes with the meeting and includes them in JSON exports. Use **Generate summary** for an imported recording or to retry after an error. The local mode extracts from the transcript. A configured API provider uses the same evidence sharing settings as question answering.

When a microphone capture finishes, the desktop also saves a local `sessionHealth`
record and includes it in the meeting detail and JSON export. It contains only
bounded operational counters/timings (for example dropped segments,
speech-to-ready latency, missing audio, input overflows, stalls and the active
preset). It does not contain audio, transcript text, questions, citations or raw
error text. This record is a local diagnostic aid, not an ASR-quality score or
proof that a microphone remained connected.

The app saves its local database and downloaded models under `~/Library/Application Support/Meeting Copilot/Workspace`. For a separate test workspace, launch with `MEETING_COPILOT_DESKTOP_DATA_DIR` set to an absolute path. Use **Export** to save a meeting snapshot to a JSON file you select.

Choose the question-answering model in **Settings**. To also change the transcription model or other advanced options, place a `config.json` using the [existing configuration format](../config.example.json) in the desktop Workspace folder and relaunch the app. Its `data_dir` is relative to that configuration file. A custom configuration can also be selected with `MEETING_COPILOT_DESKTOP_CONFIG` at launch. The app uses the configured `api_key_env` and `send_evidence` settings. Save the named credential once with `./desktop/launch.sh set-key`, or set it in the app's launch environment, when using an API provider. The sample meeting uses local excerpts until a model is set up and sharing excerpts is allowed. During recording, the app uses the existing 15-second background summary schedule.

## Developer checks

From `Testing Field`, with the development dependencies installed:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
node --test tests/test_desktop_audio_ui.mjs
```

The desktop audio tests use simulated capture events and the UI's actual event
handlers in an isolated DOM. They do not record microphone audio or measure ASR
latency. A real Mac comparison should check the active preset/effective settings,
speech-to-ready latency, missing audio, transcript quality, and recovery after
the input stops delivering frames.
