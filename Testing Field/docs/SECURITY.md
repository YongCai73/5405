# Security and Guardrails — Yong Cai

## Boundary and responsibilities

Meeting Copilot is a trusted-local-host prototype. The host configuration supplies
the principal; model tools cannot choose an identity, grant access, select a new
snapshot, run a shell, or send messages. It is not an authenticated multi-user
web service and does not defend against an OS administrator or another process
running as the same account with access to the configuration and database.

Meeting ACL checks, explicit opt-in to earlier meetings/project folders, and pinned
evidence IDs are enforced by Store/EvidenceService. Private turns and checkpoints
are scoped by snapshot, principal and conversation. Authorization is checked again
when evidence is read and after model generation; restoring a checkpoint does not
grant access. Speech, retrieved files and earlier private answers are untrusted
reference data. Exact quotation checks establish source membership, not semantic
truth or a universal prompt-injection defense.

## November 20 hardening

### Private local files

New application data directories are created with POSIX mode `0700`. The SQLite
database and existing WAL/SHM sidecars are set to `0600` before connection. SQLite
then inherits the private database mode when it creates new sidecars. Opening a
legacy regular database tightens file modes while retaining meetings, turns and
checkpoints. Existing directories/ancestors are not recursively chmodded.
Permission repair uses metadata-only operations on existing SQLite files:
opening and closing a live SHM file can otherwise release this process's POSIX
record locks. A synchronized external-writer regression checks that a second
Store initialization cannot unlock the first Store's active transaction.

Trace logs, CLI-created configuration, and CLI JSON/trace exports are also `0600`.
Application writes do not change the process-wide umask. Symbolic links, hardlinks
and nonregular artifact files are refused. Export replacement uses a temporary
file in the destination directory: a failed write or replace leaves the old
export intact and removes the temporary file. This is atomic replacement, not
a guarantee of power-loss durability or secure erasure. Config initialization
is exclusive, so it cannot overwrite an existing configuration.

On Windows, these helpers do not create a DACL. Use a private user workspace and
the operating system's access controls. POSIX-specific permission tests are
explicitly skipped on other platforms rather than reported as Windows protection.
Audio files, third-party caches, backups and copies outside these writer paths
remain subject to their own filesystem permissions and retention.

### Credentials in approved project documents

Filename exclusions are supplemented with pre-retention content checks for common
GitHub/provider tokens, AWS/Google/Slack credential formats, private-key blocks,
literal credential assignments, Authorization headers and credential-bearing URLs.
A matching file is omitted as a whole before Store.refresh persists it. Its
contents are not logged, modified, partially redacted, or sent to a model. Dynamic
lookups such as `os.getenv('SERVICE_API_KEY')` remain indexable. The existing
4 MiB scan budget counts excluded credential files too.

These checks are deliberately conservative: sample credentials may cause false
exclusions, and arbitrary or obfuscated secrets can be missed. They are not a
replacement for reviewing and sanitizing an approved folder. Transcripts are not
subject to this project-file scanner. Refresh does not erase older retained
revisions; historical snapshots and cached answers may still contain previously
indexed text. If a credential was previously indexed, revoke the affected folder,
rotate the credential and explicitly clean up affected retained data. Never test
this with a real key.

## Retention and deletion policy

- Classroom demonstrations use synthetic or expressly consented meeting data.
- Raw meeting evidence, private turns and recovery checkpoints remain local until
  the owner explicitly deletes the meeting. There is no automatic expiry/TTL.
- `cleanup MEETING --confirm MEETING` is an owner-only operation. It removes that
  meeting's logical DB records and related snapshots (including cross-meeting
  snapshots), cascading to private turns, summaries and recovery checkpoints;
  the CLI also cleans up the meeting's managed media.
- Previously exported files, redacted traces, model-provider retention and backups
  are separate. SQLite logical deletion is not secure physical erasure; no SSD,
  WAL, backup or previously distributed export erasure guarantee is made.
- Sharing, retention duration and the model evidence-upload setting must be agreed
  with participants before a real recording. Use explicit local cleanup after the
  agreed retention period; a scheduled service is outside this prototype.

## Reproducible checks

From `Testing Field`, in the project development environment:

```sh
python -m pytest tests/test_security_files.py tests/test_security_project_secrets.py -q
python -m pytest tests/test_crash_recovery.py tests/test_recovery_storage.py tests/test_audit_regressions.py -q
python -m pytest -q
```

The new tests use real temporary files, SQLite sidecars, ACLs, indexing and the
deterministic answer path. Synthetic credential-shaped strings never become
retained project evidence, answers or exported JSON; neighboring safe documents
remain usable and source bytes are unchanged. Link fixtures verify that a refused
operation does not modify its target. No microphone or paid model is needed.

Observed before the fix: nine private-file regressions failed, and twelve
credential-exclusion cases failed while six benign cases already passed. A further
regression caught exclusion bypassing the original scan byte limit. Fresh final
results and exact base/compatibility commits are recorded in the handoff evidence.

Final verification on November 20: recovery-base full suite **167 passed**;
the same patch on desktop/audio base `9ea1283` **160 passed**; the desktop UI
handlers **7 passed**. The 31 new security cases are included in those Python
suites, not additional independent experiment repetitions. Both Python runs have
the same 15 upstream LiteLLM/Pydantic deprecation warnings seen in the baseline.
The actual microphone, a fresh Windows run and paid endpoint quality were not
tested in this hardening round.

## Integration and AI attribution

This patch is based on `c9f1c55` (`zimo/crash-recovery-integration`), preserving
Zimo's orchestration and Xiaolong's replay-safe storage changes. It changes only
private-file handling, project credential exclusion and their integration points;
it does not implement their features or claim their authorship.

Yong used OpenAI Codex to inspect the boundaries, reproduce the gaps, implement
these defenses, write and run synthetic regression tests, and prepare this
documentation. Automated checks establish the tested behaviors, not a guarantee
that every privacy threat or prompt-injection attack is prevented.
