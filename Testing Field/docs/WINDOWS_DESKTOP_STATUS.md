# Windows desktop MVP

Branch: `feature/windows-desktop-mvp`  
Status (2026-11-22): developer MVP; not a signed installer or microphone
validation claim.

## Current scope

- WPF + WebView2 host reuses `desktop/bridge.py` and the shared web UI.
- Supports the existing desktop action allowlist and local JSON Lines bridge.
- Import/export use private temporary copies.
- Summary shows allowlisted Session Health metrics only; transcript text,
  questions, citations, raw errors and unknown fields are not rendered.

## Validation

- `8c5641d`: .NET 8 build, zero warnings/errors; local Atlas demo loaded through
  the real Python bridge.
- `add0034`: Session Health UI; Node UI 8/8, desktop Python 26 passed, .NET
  build zero warnings/errors.

## Run on this Windows machine

From the `Testing Field` directory in a PyCharm PowerShell terminal:

```powershell
$env:MEETING_COPILOT_DESKTOP_ROOT = (Resolve-Path .).Path
$env:MEETING_COPILOT_PYTHON = 'E:\Files\Cornell\5405_AI_applied\Assignment_4\.venv\Scripts\python.exe'
dotnet run --project .\desktop\windows\MeetingCopilot.Windows.csproj --configuration Debug
```

The app uses `%LOCALAPPDATA%\Meeting Copilot\Workspace`; if empty, it creates
the simulated Atlas sample. Inspect that sample without recording or using an
API key. A project-local `.venv\Scripts\python.exe` is used automatically;
otherwise set `MEETING_COPILOT_PYTHON` as above.

## Remaining validation

Import/export, consented microphone capture, interruption/resume, Session Health
export, packaging/signing and clean-PC validation remain open.
