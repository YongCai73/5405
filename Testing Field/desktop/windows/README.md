# Meeting Copilot for Windows (developer MVP)

This is a Windows host for the existing local Python backend and shared desktop
web UI. It is deliberately a developer MVP: it is not yet a signed installer or
a cross-platform release claim.

On 2026-11-21, this project compiled with .NET 8 (zero warnings/errors) and
loaded the local Atlas demo through the real Python bridge on Windows. No
microphone, permission prompt, audio import/export, or packaging claim was made
in that check.

## What it reuses

- `../bridge.py` remains the only backend protocol and persistence owner.
- `../ui/` is the same HTML/CSS/JavaScript used by the macOS host.
- The WPF/WebView2 shell only supplies the Windows window, file dialogs and a
  local JSON Lines subprocess connection.

Audio import/export uses a private temporary copy. The Python backend therefore
never receives the original user-selected path; an export is copied to the final
path only after the backend has written its temporary JSON file.

After a finished microphone capture, the shared **Summary** view can show its
local Session Health card: the effective preset plus allowlisted operational
counters and timings. Unknown fields, transcript text, questions and raw errors
are never rendered by this view.

## Open with one click

Double-click **Start Meeting Copilot.cmd** in `Testing Field`. It builds the
developer bundle when needed and opens it, with no environment variables.
`desktop\launch.cmd doctor` reports anything missing, and
[LAUNCH.md](../LAUNCH.md) lists every launcher command, including saving an API
key in Windows Credential Manager. The first build needs the .NET 8 SDK.

## Run on Windows

Install the .NET 8 SDK and the Microsoft Edge WebView2 Runtime, then create the
project Python environment with the audio extra. From `Testing Field`:

```powershell
$env:MEETING_COPILOT_DESKTOP_ROOT = (Resolve-Path .).Path
$env:MEETING_COPILOT_PYTHON = 'C:\absolute\path\to\python.exe'
dotnet run --project .\desktop\windows\MeetingCopilot.Windows.csproj
```

`MEETING_COPILOT_PYTHON` must point to the Python environment that has this
project and its audio dependencies. If the project owns `.venv\Scripts\python.exe`,
the variable may be omitted. Optional `MEETING_COPILOT_DESKTOP_DATA_DIR` and
`MEETING_COPILOT_DESKTOP_CONFIG` must be absolute paths, matching the macOS host
contract.

## Build an unpackaged developer bundle

The source launcher above is for development. To copy the WPF host, Python
environment, backend and shared UI into one movable folder, run from `Testing
Field` with a fresh output path:

```powershell
.\desktop\build-windows.ps1 -PythonPath $env:MEETING_COPILOT_PYTHON `
  -OutputDirectory .\build\windows-dev
```

Use `-Plan` first to print the resolved paths without writing files. The script
never overwrites an existing bundle. The result is an unpackaged developer
bundle, not an installer: it still needs the .NET desktop runtime and WebView2,
and it has not been code signed.

Before calling this supported for a Windows release, validate: a consented live
microphone session, device enumeration, a deliberate input interruption/resume,
audio import, JSON export (including `sessionHealth`), shutdown, and a published
build on a clean Windows machine. The existing bridge and UI tests cover the
shared behavior but do not prove WebView2, Windows privacy settings or microphone
drivers.
