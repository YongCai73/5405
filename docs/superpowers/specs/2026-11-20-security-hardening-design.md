# Security hardening — November 20, 2026

## Intent and scope

Improve Yong's existing Security & Guardrails work without changing worker ownership,
meeting authorization semantics, transcription, model selection, or evaluation scores.
User authorization is to implement and verify this work on an independent branch.
Base: `c9f1c55` (`zimo/crash-recovery-integration`). Main remains the planning branch.

## Reproduced gaps

1. With a normal POSIX umask of 022, the runtime directory is 0755 and the SQLite
   database, WAL, SHM and trace are 0644. Application ACLs do not protect direct
   filesystem reads by another account if the containing directories are accessible.
2. Project indexing filters sensitive filenames, but a normal `settings.py` holding
   a synthetic GitHub PAT is retained. Approved-folder consent is not consent to
   disclose embedded credentials.

## Approach and tradeoffs

Keep existing deterministic authorization and add two small defenses at their
boundaries: private application files and conservative credential exclusion before
indexing. Encryption/key management would expand deployment scope; model-based
redaction would add calls and could transmit the credential before detecting it.
Neither is needed for these reproduced gaps. This implementation choice is the
maintainer's, not a separately negotiated product requirement.

New runtime directories use 0700 and sensitive files use 0600 on POSIX. Existing
database sidecars are tightened on open. Sensitive writes reject symlinks,
nonregular files and multiply linked files; JSON exports use private temporary
files and atomic replacement. Existing regular exports may still be overwritten.
Windows continues to rely on the host account's filesystem ACL; chmod is not a
Windows DACL guarantee. Do not change a process-wide umask in production.

Project text containing recognized credential formats, private-key blocks, or
literal credential assignments is omitted before persistence. Source files are
never edited. Executable expressions such as `os.getenv('API_KEY')` remain useful
code evidence. This intentionally favors a false exclusion over transmitting a
recognized secret; it is not an exhaustive secret detector or semantic injection
classifier. Already indexed sources require explicit refresh and old snapshots
retain historical versions until cleanup.

## Verification and delivery

Write and observe failing regression tests before each code change. Verify real
SQLite files/sidecars, reopen/migration behavior, append/atomic failure safety,
source immutability and end-to-end indexing exclusion. Run the complete offline
suite, recovery tests and a deterministic demo with synthetic data. Check patch
compatibility with the current desktop branch in isolation. No recording, real
credentials, paid model requests, shared-main changes or remote pushes are needed.
Deliver the branch, reviewable patch, test evidence and security handoff with
explicit limitations, including local-host trust and logical deletion semantics.
