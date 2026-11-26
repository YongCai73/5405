# Security Hardening Implementation Plan

> **For agentic workers:** Implement inline using superpowers:executing-plans.

**Goal:** Prevent permissive local storage and recognized project credentials from bypassing existing privacy controls.
**Architecture:** Centralize private-file operations; exclude recognized secrets before Store.refresh retains project text. Keep existing authorization/recovery contracts.
**Tech Stack:** Python 3.12, pathlib/os/tempfile, SQLite, pytest; existing project environment.
**Spec:** `docs/superpowers/specs/2026-11-20-security-hardening-design.md`

## Global constraints

- Work on `yong/security-hardening-20261120`, based on `c9f1c55`.
- Preserve teammate history and main; no paid calls or real recordings.
- Use POSIX 0700/0600; no claim of Windows DACL or secure-erasure guarantees.
- Keep original project documents byte-identical; no model-based filtering.

## Review focus

- Existing open WAL files and reopened historical databases must remain usable.
- Symlink, hardlink and nonregular destinations must fail before altering targets.
- Atomic export failure must preserve the previous file and remove temporary files.
- Credential-shaped placeholders may be excluded; dynamic environment lookups remain indexable.
- Snapshot/revocation and crash recovery tests must remain green.

### Task 1 — Private storage and outputs

Files: new `Testing Field/src/meeting_copilot/private_files.py`, new
`Testing Field/tests/test_security_files.py`; modify store.py, trace.py and cli.py.

- [x] Write regressions for private runtime/DB/WAL/SHM/trace/config/export permissions and existing-file upgrade.
- [x] Run them and retain the expected failures.
- [x] Implement `private_directory(path)`, `private_file(path)`, `append_private_text(path,text)` and `write_private_text(path,text,exclusive=False)`; route sensitive writers through them.
- [x] Verify linked-file refusal, atomic failure preservation and the existing CLI/recovery suite.

### Task 2 — Credential exclusion before retention

Files: project.py and new `Testing Field/tests/test_security_project_secrets.py`.

- [x] Write failing scanner and Store.refresh/EvidenceService tests with synthetic credential canaries.
- [x] Add `contains_credentials(text)` for recognizable provider tokens, private keys and literal credential assignments, and skip those files during scan.
- [x] Verify benign environment lookups, source immutability and safe neighboring documents.

### Task 3 — Verification and handoff

- [x] Run the complete project suite and deterministic demo with private outputs.
- [x] Check desktop-branch compatibility in an isolated tree; report conflicts precisely.
- [x] Document threat model, tests, limitations, refresh/cleanup behavior and teammate integration steps.
- [x] Review diff, prepare a local commit and patch; report branch and results.
