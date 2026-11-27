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

To use the same transcription model and question-answering provider as the CLI, place a `config.json` using the [existing configuration format](../config.example.json) in the desktop Workspace folder and relaunch the app. Its `data_dir` is relative to that configuration file. A custom configuration can also be selected with `MEETING_COPILOT_DESKTOP_CONFIG` at launch. The app uses the configured `api_key_env` and `send_evidence` settings. Save the named credential once with `./desktop/launch.sh set-key`, or set it in the app's launch environment, when using an API provider. Sample meetings always use local excerpts. During recording, the app uses the existing 15-second background summary schedule.

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
