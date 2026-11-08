# Windows verification and test-repair history

The coordinator actually tested Windows 11 / CPython 3.12.14 at [c763c1489c66b826251cf0ae93712a42a0edfa81](https://github.com/cyudaddy/5405/commit/c763c1489c66b826251cf0ae93712a42a0edfa81): **84 passed, one skipped, zero failures/errors (85 cases)**, plus a separate concurrency rerun passed. The skip is native symlink permission, not a capability pass. [Validation](../results/windows-validation.json), [full JUnit](../results/tests-windows.xml), [concurrency JUnit](../results/tests-windows-concurrency.xml).

Init/demo/evaluation/mic-list succeeded; three sources were enumerated. Actual local tiny.en CPU ASR on public JFK audio produced two segments. No microphone was recorded and no paid API was called. Native NTFS junction scanning blocked escape, root approval was rejected, removal was nonrecursive and the external target was unchanged; this latest junction check names c763c14, independently of the earlier b42ddcf check. [ASR/prepared state](../results/windows-prepared.json), [junction](../results/windows-junction.json), [environment](../results/windows-environment.txt), [source/test hashes](../results/windows-source-manifest.json).

All three repairs changed tests/documentation, **not runtime**: 54 runtime files and package/source version 0.2.0 remain frozen. Existing release tags are preserved. The historical v0.2 tag predates these repaired tests; collect the current [prototype branch](https://github.com/cyudaddy/5405/tree/meeting-copilot/prototype-20261108). See [portable delivery metadata](../results/server-delivery.json).

## Measured sequence

| Windows source/tests | Actual outcome | Interpretation |
|---|---|---|
| Initial audited source 5831912 | 68 passed / four failed / one skipped (73) | Newline fixtures and fixed process-start windows needed correction. |
| Repair1 097fec3 | 73 passed / two failed / one skipped (76) | LF/CRLF cases passed; legitimate venv redirector ancestry and cold provider initialization needed faithful contracts. |
| Repair2 188fcb6 | 83 passed / one failed / one skipped (85) | Newline, ancestry, protocol and cold-start passed; final zero-drop assertion failed at synthetic overload pace. |
| Repair3 c763c14 | 84 passed / zero failed / one skipped (85) | Representative functional pace passes, while deliberate overload accounting remains tested separately. |

Historical sequence and original workload observation are retained in [server-delivery.json](../results/server-delivery.json); repair-time reports saying “Windows pending” describe their publication time, superseded by the actual final results above.

## Repair3: workload-specific queue saturation

The older functional fixture submitted one synthetic event every 0.05s (**20/sec**) and scheduled summaries every 0.1s (**10/sec**), including during Windows cold startup. Windows reported **782 processed, zero ingestion errors, 184 drops**. The real CLI returned a cited answer; ingestion and shared summaries advanced while fake HTTP was gated, and snapshot/cutoff checks passed. The zero-drop assertion failed before the final gap assertion. This is a workload-specific bounded-queue saturation observation on that filesystem, not evidence of failed real microphone/ASR, nor a measured real-ASR throughput limit.

The ordinary functional case now submits at most **two events/sec and one summary/sec**, sleeping after submissions without catch-up bursts. Production uses approximately three-second audio chunks and default 15-second summaries. Synthetic events bypass ASR; neither fixture measures its acoustic quality or throughput. Runtime durability, queues and drop policy are unchanged.

Readiness still requires at least ten committed events and more than five summaries, fresh nonce and faithful PID/PPID ancestry. Once the actual ask subprocess reaches loopback HTTP, its response stays gated until **both** ingestion and completed summaries advance; the summary watermark must exceed evidence observed after endpoint receipt. The test retains a cited answer, snapshot membership/cutoff, later evidence, zero gaps/drops/ingestion or summary errors, matching final identity and clean child exit.

The deliberate overload test remains unchanged: block the consumer, fill capacity two, accept only two of the next twenty submissions, report eighteen drops and depth two, then drain three processed items. Pending-worker bounds remain covered. A successful normal-load case does not promise zero drops at arbitrary load.

Latest Linux repair3: **14 focused / 85 full / one delayed-start passed**, in 17.245s / 62.597s / 50.583s. The controlled delayed run processed 72 synthetic events and 35 summaries with zero observed gaps/drops/errors and queue peaks one. Those Linux loopback results are distinct from the actual Windows tests. [Repair3 report](../results/windows-repair3-validation.json), [functional test](../tests/test_live_processes.py), [overload tests](../tests/test_workers.py).

## Repair2: venv identity, cold-start budget and cancellation

A Windows venv redirector can remain the launcher while its child runs Python. The readiness contract accepts direct `(PID=launcher, PPID=test parent)` or redirected `(PID!=launcher, PPID=launcher)` ancestry and a fresh parent-generated 32-byte nonce; unrelated ancestry/stale nonce is rejected. Shutdown must match readiness identity. Gated progress, snapshots, answer and cleanup assertions remain. The forced Windows `taskkill` tree fallback still lacks an explicit native fault-injection exercise; the successful final suite is not proof that fallback ran.

The earlier first OpenAI timeout occurred before any endpoint request; later cases passed. This supports cold SDK/tokenizer initialization consuming the old 20s budget, without establishing a network defect or profiling all startup times. Successful fake-wire and CLI contracts now allow **120s**, with no retries/success bypass. Explicit nonstream timeout remains 0.1s, stream timeout 0.15s, and pre-HTTP async-start cancellation is tested for both `chat()` and `complete()`. Intentional broken-pipe/reset/Windows `ConnectionAbortedError` during response emission is handled as expected cancellation; request JSON/schema failures are not suppressed.

For first Windows API use, recommend local `"timeout_seconds": 120`; lower to **60 only after measuring startup**. The prepared local demo config already uses 120; shipped runtime/example default remains 20. Cooperative asyncio deadlines cannot preempt blocking synchronous imports, so this is not a hard import wall-clock guarantee. An earlier dependency unawaited-coroutine warning is historical; no dependency internals were changed or generally certified fixed. [Repair2 report](../results/windows-repair2-validation.json), [protocol tests](../tests/test_protocols.py), [provider](../src/meeting_copilot/provider.py).

## Repair1: retained text and bounded startup

The connector deliberately retains decoded UTF-8 with original LF/CRLF, hashing the full revision. Tests now write exact bytes and parameterize both endings instead of comparing Windows-translated CRLF against an LF-only variable. Character offsets are zero-based half-open Unicode string offsets; original line numbers are one-based. Each CR and LF counts separately. Bounded display slices may trim trailing CR/LF, without rewriting retained text or full-file hashes. Old snapshots keep retained revisions until revoked, and multiple supplied views can each cite their original positions.

Separate-process tests replaced a fixed 12s start window and finite capture lifespan with bounded 120s startup phases, early-exit diagnostics, capture-ready and actual endpoint-reached handshakes. Capture continues until the parent's stop signal. Cleanup releases the gate, drains, then kills/reaps still-owned children if necessary. [Repair1 report](../results/windows-repair-validation.json).

## Running and interpreting future checks

Use the complete [Chinese handoff](../handoff.md) for full setup, two-terminal live experiments, tests and cleanup. On Windows set `$env:PYTHONUTF8="1"` before capturing Unicode device names/JSON; this is a shell/consumer encoding setting, not an ASR defect. Use actual JSON IDs and the dedicated `.venv\Scripts\python.exe`; activation is unnecessary.

Final Linux stability evidence remains the completed [1,200.336s prerecorded real-ASR run](../results/audit-soak-final.json) on unchanged runtime. No extra ASR soak was run for test-only repairs. Historical wheel/README audits had 73 cases; the latest source suite has 85, without claiming a new 85-case wheel audit. macOS, real microphone/unplug/20-minute technical meeting, paid endpoints and human technical-term/multilingual assessment remain unvalidated. Collect/verify before coordinator server cleanup; append a receipt only after observed removal.
