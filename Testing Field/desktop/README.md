# Meeting Copilot for macOS

Meeting Copilot opens as a desktop window with a meeting list, searchable transcripts, meeting notes and private questions. The first launch loads a sample meeting. Try **Compare SQLite and Kafka** and select a citation to inspect its source. Sample answers use quotes from the existing local retrieval pipeline and require no API key.

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

Create a meeting to import an audio file or record from a microphone. Choose your input and start recording after participants agree. macOS asks for microphone permission on first use. Transcription runs in English with the local Faster Whisper model. The first audio operation may need to download the model.

The app saves its local database and downloaded models under `~/Library/Application Support/Meeting Copilot/Workspace`. For a separate test workspace, launch with `MEETING_COPILOT_DESKTOP_DATA_DIR` set to an absolute path. Use **Export** to save a meeting snapshot to a JSON file you select.

To use the same transcription model and question-answering provider as the CLI, place a `config.json` using the [existing configuration format](../config.example.json) in the desktop Workspace folder and relaunch the app. Its `data_dir` is relative to that configuration file. A custom configuration can also be selected with `MEETING_COPILOT_DESKTOP_CONFIG` at launch. The app uses the configured `api_key_env` and `send_evidence` settings. Set the named credential in the app's launch environment when using an API provider. Sample meetings always use local excerpts. During recording, the app uses the existing 15-second background summary schedule.
