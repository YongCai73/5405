# Independent completion review — v0.2

Reviewed the delivered v0.1 commit `cb8405a8843f10218b5b7f7aa8b75fdedc28324d` after reading `LATEST_USER_UPDATE.md` and the complete current proposal. The expanded overall budget was used for functional fixes and executable validation; no feature was deferred merely because it could exceed one hour. The original `v0.1.0-prototype` tag is preserved. Final head/tag/artifact hashes are recorded in build-root `DELIVERY.json`. [source: user instructions; git history]

## Findings and fixes

| Finding | Implemented fix | Evidence |
|---|---|---|
| Coordinator's >100 recent-summary concern was already fixed in final v0.1; ordinary retrieval still limited chronologically before ranking | Keep recent-tail queries; rank FTS matches before limiting; current-meeting catch-up excludes opted-in prior meetings | `test_long_meeting_search_ranks_before_limit_and_exports_citations`, existing 120-segment tail test, current/prior catch-up regression |
| The 80-token cap on complete sources and first-1000-character excerpts missed late identifiers | Scan complete bounded source text; select bounded overlapping excerpts; retain original character/line offsets and full revision hash; rank multi-file search | 131-file regression plus measured 300-file/1,200-segment synthetic scale check with target at line 3,001; retained refresh/source-unchanged tests |
| Manual CLI corrections reset speech timestamps to zero when flags were omitted | Preserve existing timestamps and require an increasing revision; old snapshot remains pinned | CLI correction/new-answer/old-snapshot regression |
| Truncated exports could omit the source of a late cited answer | Include first 100 sources plus every answer/summary citation; flag remaining truncation; paginate MCP transcript reads | Long-meeting export and stdio/CLI tests |
| API was quotation selection only; skills and summaries were extractive | Configured model produces task-directed synthesis and separate exact quotes; inference references and source metadata validated; shared API summary receives current transcripts only | Fake-provider synthesis/context isolation tests; both HTTP protocols |
| Independent SQLite readers could combine ACL checks from different committed states during a grant/revoke swap | Wrap authorization plus data reads in one WAL read transaction; final generation still performs fresh checks | Cross-connection atomic ACL-swap regression; prior installed wheel fails the new regression |
| Private state existed only per snapshot | Meeting/principal/conversation continuity with each turn's pinned snapshot, fresh ACL/scope checks, evidence and conversation watermarks; additive v0.1 answer backfill | Cross-snapshot, cross-thread/meeting, revocation, clock-adjustment and upgrade tests |
| Approving a runtime parent could index private/export data under a nonstandard name | Reject overlapping runtime roots on approval, refresh and old-snapshot reads; preserve owner ability to revoke a legacy unsafe approval; put synthetic demo project outside runtime | Actual-root/ancestor/descendant and legacy-approval regressions |
| Live scheduler existed, but usable concurrent workflow was not well demonstrated | Document two terminals; live-status command; periodic queue/error visibility; final partial chunk ingested on Ctrl+C; continuous duration option | Selected-device simulated backend; separate ingestion and real ask CLI processes sharing WAL DB with slow fake HTTP |
| Streaming and model tool rounds were deferred | Normalize text/tool SSE fragments, roles, usage, errors, timeout/cancellation for both protocols; at most two rounds/four calls per round; host-bound search/read only | Local OpenAI/Anthropic text, fragmented tool-call, result-role, identity-injection and cancellation tests |
| Repeated tool reads of one file replaced earlier excerpt views, discarding valid citations | Preserve every bounded supplied view and resolve each quotation against its matching original offsets; unsent text remains invalid | New regression fails on pre-fix wheel (only last quotation survives), passes after fix with lines 1, 2,002 and 2,003 |
| Native baseline lacked a runner | Reuse unchanged upstream LiveAssistantService, repository and SQL; run the same frozen 22 questions through a common transport bridge; add explicit API augmented evaluator | 22 native requests per fake protocol, 88 augmented fake API requests; no paid/native quality claim |
| Coordinator described a literal policy-ablation counter; final v0.1 already executed one synthetic bypass | Extend to three executed attacks on the isolated fixture store: other meeting, unselected project and other private thread; ablate retrieval and final provenance only inside evaluation | Guarded vs ablated canary checks and evaluator tests |
| Accelerated transcript stress was not a 20-minute stability result | Run actual wall-clock prerecorded audio through cached upstream ASR, periodic summaries and concurrent slow simulated QA, record RSS/lag/queues/drops | `scripts/prerecorded_soak.py`, `results/audit-soak-20m.json`; final-code run recorded separately |

These are application-level tests, not a general security proof. Exact quotation membership and structural citation checks do not establish semantic entailment of model prose. The model can still draw an incorrect conclusion from authentic quotations; output labels that inference and retains the quotes for review. [source: src/meeting_copilot/provider.py; tests/test_audit_regressions.py]

## Security boundaries exercised

The host supplies principal, meeting, opted-in prior meetings, approved folders and snapshot. Model tools cannot override these values or invoke shell/write/grant operations. Every retained read uses one coherent SQLite view for policy plus content and rechecks access on each fresh call, including old snapshots, exports, private history, checkpoints, summaries, MCP and model tool reads. Generation rechecks after a slow provider returns. Additional provenance validation checks retained text, offset, line origin, hash, revision, kind, meeting and path before returning citations. [source: src/meeting_copilot/store.py; src/meeting_copilot/model_tools.py; src/meeting_copilot/assistant.py]

Late ASR segments, final rewrites and explicit project refreshes create retained versions; old snapshots keep exact IDs. Private history also uses a pinned monotonically assigned turn watermark, not merely timestamps. Prior answers from an unselected/revoked scope are not reused. Shared generation excludes all private turns, project files and user supplements. Trace exports filter snapshot metadata by the current principal and ACL. [source: tests/test_evidence.py; tests/test_audit_regressions.py]

Project tests cover traversal, symlink escape, secret-name/binary/oversize exclusions, runtime overlap and unchanged source hashes. Filename/type exclusions are a conservative filter, not a guarantee that arbitrary source code contains no secrets. The trusted local OS/config boundary remains explicit; no external login system or malicious-OS-process isolation is claimed. Windows junction/case/UNC behavior still requires native verification. [source: src/meeting_copilot/project.py; tests/test_project.py; tests/test_live_processes.py]

## Validation record

Final validation: **73/73 tests passed in each of the source, independent wheel and README environments**, with no skips/errors/failures; 22 CLI smoke operations and 24 fake-wire protocol tests passed. Final prerecorded ASR replay ran **1,200.336 seconds**, committed 399 segments, completed 79 summaries and 119 five-second simulated QA requests, with zero drops/errors/observed future-evidence violations. Submission-to-commit p95 was 1.115 seconds (excluding three-second chunk accumulation), maximum 1.445 seconds, peak RSS 319,311,872 bytes; both queue peaks were 1. [source: results/audit-validation.json; results/audit-soak-final.json]

Python, hardware architecture/logical CPU count and exact installed dependency versions are recorded in `results/audit-environment.json`. Final counts/durations are generated into `results/audit-validation.json` from JUnit and smoke artifacts. Source suite: `results/tests-audit.xml`; independent installed wheel without audio: `results/tests-audit-wheel.xml`; exact README Linux commands: `results/tests-audit-readme.xml`. The fresh wheel smoke script runs install-adjacent CLI init/demo/replay/questions/history/export/checkpoint/summary/status/evaluation/revocation/cleanup and checks original project hashes. `pip check` results and the lack of an audio package are recorded. [source: scripts/clean_install_smoke.py; results/audit-install-smoke.json]

The synthetic evaluator remains separately labeled. It reports expected-term correctness, exact quotation membership, source-watermark leakage and defined policy cases, not human semantic quality. API evaluation is now an explicit `evaluate --api` opt-in (88 requests); native evaluation is `native-baseline` (22 requests). Those commands were exercised with local fake endpoints; live model quality, billing and native comparative performance remain unmeasured. [source: src/meeting_copilot/evaluation.py; tests/test_protocols.py]

The prerecorded soak decodes and repeats the public 11-second JFK fixture into paced three-second chunks. Actual `FasterWhisperEngine` tiny.en, CPU int8, two threads transcribes each submitted chunk; the bounded pipeline commits segments while five-second fake-provider QA and periodic shared summaries run. Reports include actual wall time, processed/dropped segments, maximum queue depth, submission-to-commit lag, RSS, QA cutoff violations and exact source hashes. Other local audit tests and the preceding replay overlapped part of the final run; timing is not an isolated hardware benchmark. This does not measure microphone drivers, acoustic robustness, technical vocabulary/WER, or live LLM quality. The initial soak began during implementation; the separate final-core soak is the final stability evidence. No runtime source changed after that final run started, and its source hashes match the release. Multiple-view tool citations are separately regression-tested; the slow soak provider does not use model tools. [source: scripts/prerecorded_soak.py; results/audit-soak-20m.json; results/audit-soak-final.json]

Reproduce the synthetic scale check in an installed environment (work path must be new):

```bash
python scripts/scale_check.py --work data/scale-check --output results/local-scale.json
```

The wall-clock replay requires the audio extra, an explicitly supplied recording and an already cached tiny.en model under `MODEL_ROOT/models/`; those assets are not bundled in the source archive. On Linux/macOS, use a fresh work directory and allow at least 20 minutes:

```bash
python scripts/prerecorded_soak.py --audio RECORDING.wav --model-root MODEL_ROOT --work data/soak-check --output results/local-soak.json --seconds 1200
```

This measurement script uses the Unix `resource` module for peak RSS; the application CLI remains cross-platform, and Windows stability measurement needs a platform-native RSS collector. The build-root logs preserve the executed paths and intermediate runs; only completed soaks are presented as stability results. [source: scripts/prerecorded_soak.py]

## Upstream and compatibility

24 vendored implementation/SQL files match the pinned upstream bytes. Existing capture backends, Faster Whisper, storage, evidence framing and LiteLLM helper are used in running paths. Added unmodified native assistant/service/repository files are used by the baseline harness. MCP wrapping, the minimal CPU/path seams and the common native transport bridge are explicitly distinguished from unchanged upstream code; MIT attribution is packaged. The upstream checkout itself remains unchanged. [source: docs/UPSTREAM_SOURCE.json; results/audit-upstream.json]

The native runner does **not** instantiate the full desktop app, native hybrid RAG, native model engine or OS credential store. It uses the original service's session, publish, direct-question, memory, parsing and persistence behavior with a small SummaryEngine transport bridge and synthetic meeting/transcription parent rows. This is a reproducible native-service comparison path, not a claim that the entire upstream desktop baseline was measured. [source: src/meeting_copilot/native_baseline.py]

Missing provider usage stays unknown: the adapter rejects SDK-generated stream token estimates and ambiguous all-zero non-stream usage, keeping cost null. Configuration requires actual JSON booleans for upload/streaming opt-in and validates resource/price bounds. [source: tests/test_protocols.py; tests/test_cli_storage.py]

Wire fixtures follow the official [OpenAI Chat Completions streaming contract](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events) and [Anthropic Messages streaming contract](https://platform.claude.com/docs/en/build-with-claude/streaming). LiteLLM performs protocol translation; the adapter assembles bounded normalized results and the host validates tool arguments. Compatibility is asserted for these tested shapes, not every vendor/model or reasoning/audio response type. [source: tests/test_protocols.py; src/meeting_copilot/provider.py]

## Remaining limitations and reason

| Item | Why it remains |
|---|---|
| Windows/macOS clean install, microphone, junction/UNC and device-unplug tests | This server is Linux aarch64 without PortAudio/microphone hardware; coordinator must run target machines |
| Real paid endpoint quality/cost and human native comparison | No authorized paid credentials or human annotations supplied; fake-wire output is not live inference |
| Technical-term ASR accuracy and real 20-minute meeting | Repeated public recording is a stability fixture, not a representative technical discussion; tiny.en/English is the tested ASR configuration |
| Mid-node durable LangGraph resume | Prototype persists guarded final checkpoints; crash recovery across provider/tool execution needs explicit replay/idempotency semantics, beyond the proposal's minimal read-isolation path |
| Long-term retention/disk quotas, encrypted storage and secure deletion | Local prototype retains revisions; cleanup is logical. Production lifecycle policy is not specified or validated |
| Embeddings, diarization/system audio, Langfuse hosting, GUI/portable installers | Optional proposal extensions; lexical baseline and CLI are implemented, platform packaging/hardware validation remains prerequisite |

No deferral above relies on the withdrawn one-hour feature cutoff. All work/install/cache/test paths used by this audit remain under the dedicated build root. No sudo, global package/config changes, auth-secret inspection, unrelated-project changes, force push or root deletion was performed. All started processes are recorded as stopped in final DELIVERY.json. [source: audit command logs; user authorization; DELIVERY.json]
