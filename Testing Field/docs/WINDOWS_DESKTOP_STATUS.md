# Windows desktop integration status

**Recorded 2026-11-27** on
`integration/windows-desktop-candidate` at `140a4e4`.

This is a developer-candidate status record, not a Windows release claim. The
application is still an unpackaged, unsigned developer bundle and has not yet
been tested on a clean PC.

## Current scope

- WPF + WebView2 host reuses `desktop/bridge.py` and the shared web UI.
- Supports the existing desktop action allowlist and local JSON Lines bridge.
- Import/export use private temporary copies.
- Summary shows allowlisted Session Health metrics only; transcript text,
  questions, citations, raw errors and unknown fields are not rendered.
- `desktop/build-windows.ps1` can publish an unpackaged developer bundle without
  overwriting an existing output directory.
- `177425d` merged background-Q&A/recovery and security hardening work into this
  candidate. `140a4e4` makes Windows capture startup/shutdown compatible with
  the local process runtime: it checks a parent-process handle instead of
  unreliable child `getppid()` output and uses a private stop marker instead of
  unsupported child `SIGINT` delivery.

## Evidence collected on this Windows machine

| Area | Result | Boundary |
|---|---|---|
| Deterministic integration gate | Passed: 22/22 skills, 0 future-evidence leaks, 10/10 security-policy checks, strict gate passed. | Regression evidence only; not live ASR, real-model, or human-quality evidence. |
| Full Python suite before the lifecycle-only fix | 226 passed, 10 skipped. | Recorded on the same candidate ancestry before `140a4e4`; the later change is covered by the focused desktop suite below. |
| Desktop Python tests after the fix | 28 passed. | Covers bridge protocol, capture status, Windows parent liveness, and private-stop cleanup. |
| Shared desktop UI tests | 10 passed. | JavaScript contract tests, not a manual WebView2 interaction test. |
| WPF host build | Debug build succeeded with 0 warnings and 0 errors. | Compilation only. |
| Developer bundle | A fresh bundle started for 8 seconds with source-root/Python variables cleared and created a separate local data directory. | Same-machine isolation only; not a clean-PC or installer validation. |
| Consented local microphone flow | Default WASAPI microphone enumerated; a 30-second local capture committed 7 transcript events with no drops, stalls, or input overflows. | Non-sensitive test speech only; no external API/model call. |
| Desktop bridge end-to-end flow | Imported the local test audio, started/stopped a desktop bridge capture, persisted Session Health, produced a local JSON export, and closed cleanly. | Bridge protocol was exercised directly; the WPF file-picker interaction itself was not manually driven. |

No recorded audio, transcript, local model cache, configuration, export, build
output, or IDE metadata was committed to Git.

## Run on this Windows machine

From the `Testing Field` directory in a PyCharm PowerShell terminal:

```powershell
$env:MEETING_COPILOT_DESKTOP_ROOT = (Resolve-Path .).Path
$env:MEETING_COPILOT_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
dotnet run --project .\desktop\windows\MeetingCopilot.Windows.csproj --configuration Debug
```

The app uses `%LOCALAPPDATA%\Meeting Copilot\Workspace`; if empty, it creates
the simulated Atlas sample. Inspect that sample without recording or using an
API key. A project-local `.venv\Scripts\python.exe` is used automatically;
otherwise set `MEETING_COPILOT_PYTHON` as above.

## Current stopping point

The normal Windows capture, import, export, and graceful-close paths now have
enough local evidence for this development stage. Do not add more synthetic or
repeated hardware checks unless a code change touches those paths or a concrete
failure is reported.

## Deliberately deferred validation and delivery work

- **Input interruption/resume:** not performed because this machine uses an
  internal microphone with no meaningful physical unplug path. The code and UI
  status contracts are covered by tests; a real external-device test can be run
  later if needed.
- **Clean-PC bundle test:** needed before any distribution claim to prove the
  package has no dependency on the source checkout, local virtual environment,
  or current machine's caches.
- **Release delivery:** code signing and installer design remain separate work;
  the current bundle is not an installer or release.
- **Product-quality evidence:** human review of real meetings, ASR accuracy,
  and any paid-provider evaluation require separate consent, data, model, and
  budget decisions.
