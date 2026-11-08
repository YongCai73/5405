> **Historical v0.1 build record (32-case suites).** Earlier deferrals/counts below describe that initial release and are preserved as measured history. Current [handoff](../handoff.md), [proposal mapping](../results/requirements-current.json), [independent review](INDEPENDENT_REVIEW.md) and [portable delivery summary](../results/server-delivery.json) supersede its status statements.
>
> **Current measured platforms:** actual Windows repair3: 84 passed, one symlink-permission skip (85 cases), separate concurrency pass, CLI/three-device enumeration and public tiny.en ASR successful, native junction verified; no microphone recording or paid API. [Windows results](../results/windows-validation.json), [ASR](../results/windows-prepared.json), [junction](../results/windows-junction.json). Latest Linux source: 85/85; historical v0.2 wheel/README audit: 73 each. Final completed Linux prerecorded-ASR stability run: [1,200.336 seconds](../results/audit-soak-final.json). macOS and real microphone remain pending. Streaming, synthesis and private continuity are now implemented; the old owner list below is historical.
>
> Server cleanup is **pending collection and verification**, not already done. Coordinator collects and verifies first, removes the dedicated root, observes removal, then appends the receipt locally and in the final Git commit and creates the verified-handoff tag. Existing release tags remain unchanged.

# Server build and validation report

Build root: `/home/jy2283/meeting-copilot-dev-20261108-eHu73X5Y`. Linux aarch64, Python 3.12.3, glibc 2.39. All package installations, caches, model/audio downloads, test temp directories and wheel artifacts stayed inside this dedicated root. Inherited `TMPDIR=<build>/tmp`, `XDG_CACHE_HOME=<build>/cache/xdg`, `PIP_CACHE_DIR=<build>/cache/pip` were verified. No sudo/global install, dotfile/config/auth edits, paid API use or pre-existing service changes.

## Repository boundaries and reproducible build commands

Actual remote default inspection returned `main @ d1e1b078349a764c7924ba52ad3f4cc6ff04548c`. `git clone ... team` succeeded; the chained upstream clone found an existing coordinator-owned clone and did not overwrite it. Its clean checkout was verified at the proposal pin. A separate pre-existing `team-repo` directory was untouched.

The following are the effective commands used, relative to the build root; redirected logs remain local for coordinator inspection and are not committed wholesale:

```bash
git ls-remote --symref git@github.com:cyudaddy/5405.git HEAD
git clone git@github.com:cyudaddy/5405.git team
git -C upstream status --short
git -C upstream rev-parse HEAD
cat inputs/proposal-current.txt
git -C team switch -c meeting-copilot/prototype-20261108
git -C team config user.name 'Meeting Copilot Prototype'
git -C team config user.email 'meeting-copilot@users.noreply.github.com'
python3 -m venv .venv
.venv/bin/python -m pip install --cache-dir cache/pip hatchling pytest pytest-asyncio langgraph litellm mcp sounddevice faster-whisper
.venv/bin/python -m pip install --cache-dir cache/pip aiofiles
.venv/bin/python -m pip install --cache-dir cache/pip -e 'team/Testing Field[dev]'
.venv/bin/python -m pip install --cache-dir cache/pip 'av>=16,<17'
# platformdirs was installed only in build venv to inspect original native MCP gateway:
.venv/bin/python -m pip install --cache-dir cache/pip platformdirs
.venv/bin/python -m meeting_copilot --config tmp/smoke-config.json init
.venv/bin/python -m meeting_copilot --config tmp/smoke-config.json demo
.venv/bin/python -m meeting_copilot --config tmp/smoke-config.json evaluate --output 'team/Testing Field/results/report.json' --stress
TMPDIR="$PWD/tmp" XDG_CACHE_HOME="$PWD/cache" HF_HOME="$PWD/cache/huggingface" TIKTOKEN_CACHE_DIR="$PWD/cache/tiktoken" LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python -m pytest 'team/Testing Field/tests' --basetemp="$PWD/tmp/pytest-release-source" -q --junitxml='team/Testing Field/results/tests.xml'
.venv/bin/python -m pip wheel --cache-dir cache/pip --no-deps --wheel-dir tmp/wheels 'team/Testing Field'
python3 -m venv .venv-clean
.venv-clean/bin/python -m pip install --cache-dir cache/pip 'tmp/wheels/meeting_copilot_prototype-0.1.0-py3-none-any.whl[dev]'
# Later wheel refresh after final changes, no dependency edits:
.venv-clean/bin/python -m pip install --cache-dir cache/pip --force-reinstall --no-deps tmp/wheels/meeting_copilot_prototype-0.1.0-py3-none-any.whl
TMPDIR="$PWD/tmp" XDG_CACHE_HOME="$PWD/cache" HF_HOME="$PWD/cache/huggingface" TIKTOKEN_CACHE_DIR="$PWD/cache/tiktoken" LITELLM_LOCAL_MODEL_COST_MAP=True .venv-clean/bin/python -m pytest 'team/Testing Field/tests' --basetemp="$PWD/tmp/pytest-release-wheel" -q --junitxml='team/Testing Field/results/tests-wheel.xml'
.venv/bin/python -m pip check
.venv-clean/bin/python -m pip check
```

`tmp/clean-check.py` independently invoked the wheel interpreter for init/demo/create/replay/approve/refresh/meeting-plus-project cited answer/export/checkpoint/evaluate/cleanup, asserted source hashes unchanged, and checked both environments. `results/install-smoke.json` contains sanitized operation results. The clean environment has no audio extra, proving the replay path does not require ASR/microphone dependencies. Source examples and paths contain the real `Testing Field` space.

## Dependency versions actually used

| Package | Server version |
|---|---|
| langgraph | 1.2.20 |
| litellm | 1.108.3 |
| mcp / mcp-types | 2.3.0 |
| faster-whisper | 1.2.1 |
| ctranslate2 | 4.8.5 |
| av | 16.1.0 (19.0.2 was incompatible) |
| numpy | 2.5.4 |
| sounddevice | 0.5.6 |
| aiofiles | 25.1.0 |
| pytest / pytest-asyncio | 9.2.0 / 1.4.1.post1 |

Full measured server freeze is `requirements-server.lock.txt`. It is an environment record, not a promise that Linux transitive wheels install on Windows/macOS; use pyproject extras there and perform the pending native checks. Runtime downloads/cache roots are set from the trusted single config. Automatic LangSmith export is disabled; Langfuse upload is not configured.

## Measured tests and evaluation

`results/test-summary.json` and both JUnit files are the final count/duration authority (source and independent installed wheel). The first integrated suite had 17 core passes, then 26 protocol passes after correcting a case-sensitive HTTP-header assertion; full CLI/checkpoint/ablation/regression coverage was added afterwards. An initial SQL placeholder-count error was fixed before the passing milestone. No failing initial run is presented as a pass.

Frozen replay has 20 events and 22 annotated questions (two unsupported questions). Initial skills score 21/22; targeted identifier tokenization fix produced 22/22. Exact per-case IDs, source IDs, derived logical question timestamps, correctness, citation support, future leakage, latency and usage are in `results/report.json`; fixture SHA256 matches `initial-eval.json`. Correctness is expected-substring matching over deterministic extracted quotations, not blinded human quality assessment.

- Skills: 22/22; supported quotation cases 22/22; zero future-evidence leakage on this fixture.
- Direct control: 21/22 using exactly the skills retrieval context; no paid model. The catch-up rule explains the difference; no general model improvement claim.
- No snapshot: 20/22 and 10 future-leakage cases using final synthetic evidence for early questions.
- No folder: 20/22.
- Policy retrieval ablation: one graph-only synthetic unauthorized canary disclosure vs zero with policy. It bypasses retrieval **only inside the evaluator-created synthetic database**; guarded host final checkpoints remain outside that ablation. Production exposes no policy/snapshot disabling switch.
- Defined attack suite: 10/10, zero observed unauthorized disclosures. Pytest additionally covers path/symlink escape, secrets/binary/oversize, refresh retention, prompt-injection inertness, raw/summary/cache/checkpoint revocation, and bounded queue overflow.

Reported p50/p95 are **answer-node** generation/validation in deterministic demo mode; `total_graph_latency_ms` separately includes retrieval and persistence. Direct control reuses frozen retrieval, so total graph time is not a like-for-like retrieval benchmark. These millisecond values are not API latency claims. API usage/cost in replay is exactly zero; fake API contract tests separately normalize 17 input/11 output fixture tokens. Real costs remain null unless users configure both rates.

Stress: 400 synthetic chunks × logical 3 seconds = 1200 logical seconds; approximately 0.25s wall time, four-slot audio queue, zero drops in the paced synthetic run. Peak Python allocation from tracemalloc is recorded; this is not full-process RSS, real-time scheduling, acoustic accuracy or 20 minutes of actual capture. A separate overflow test intentionally dropped 18 of 21 submitted chunks and verified bounded accounting; a blocking fake provider did not stall 30 synthetic capture items or the shared-summary worker.

## ASR and native integration evidence

The JFK fixture was downloaded to `<build>/tmp/asr/jfk.flac` from the URL and SHA256 recorded in `results/asr-smoke.json`. A tiny.en model was downloaded explicitly inside `<build>/tmp/asr/models`; Hugging Face cache stayed under the build root. Actual vendored upstream engine CPU int8/2 threads transcribed the 11s recording into two segments in 2.054s including model load in the selected probe. No GPU packages or local LLM were installed. No WER, technical-term recognition, continuous 20-minute ASR or microphone quality was measured. Three real decoder solutions and exact outcomes are in COMPATIBILITY.md.

Original upstream MCP server import and original gateway controlled completed-transcript rewrite probe ran with `PYTHONPATH=<build>/upstream/src`; no desktop service started. First mock omitted required native response fields and was corrected against the actual schemas. The selected adapted registration passed actual MCP stdio initialization, 10 tool schemas, calls, cross-snapshot denial and post-revocation denial. Native assistant/hybrid RAG baseline remains **not measured**; the full desktop/native model stack was not run.

## Architecture, limits and ownership

SQLite exact revision IDs and observed ingestion watermark are authoritative; snapshots are not permission grants. Reads of metadata, evidence, project manifests, summaries, answers and final graph checkpoints revalidate the current ACL and approved folders. Project reads return retained text, not current disk. Host context contains principal/snapshot; MCP cannot choose either. Private data does not enter shared facts; promotion is explicit and labelled.

Captured PCM has upstream bounded buffering; new chunk/segment queues are bounded independently from QA and summary workers. CPU ASR uses two threads and one active model worker; API work is I/O. The simple CLI can capture in one terminal while another asks through the same WAL database. Long-meeting catch-up/shared summaries explicitly select recent rows rather than the first 100; ordinary retrieval/export remain bounded (export reports truncation past 100). Final graph checkpoints support authorized inspection/reuse, not mid-node durable resume.

Remaining owners: Zimo—Windows/macOS/mic/ASR feasibility; Xiaolong—durable mid-graph checkpoints, migration/long retention; Jiaxuan—richer synthesis/tool-rounds/streaming and project retrieval; Cai—native junction and host session checks; Qiyan—real provider/native baseline and human quality/cost/lag measurement. Optional UI/installers/diarization/system audio/embeddings/Langfuse hosting remain deferred. Estimated effort/priorities are in handoff.md.

## Processes and cleanup boundaries

Only loopback HTTP fake endpoints were started (ephemeral ports, ThreadingHTTPServer finally shutdown/server_close/join). Actual MCP stdio child processes were closed by client contexts. CLI smoke subprocesses exited; ASR engine executors and pipeline/QA/summary threads were shut down. No persistent app server, daemon or system service was installed. Final process inventory is in build-root DELIVERY.json.

No credentials/runtime DB/models/audio/venvs were committed. Source hashes and upstream license are retained. App cleanup removes a selected meeting and dependent snapshots/private state/media; exports/traces/backups remain separate and there is no secure-erasure guarantee. The coordinator must collect DELIVERY.json, app repo and useful logs, independently verify, sync to Windows, then remove the dedicated server root. This implementation agent does **not** remove that root, touch coordinator launch processes, change existing user services or clean unrelated files.

## Final recorded totals

- source_venv: 32 passed, failures=0, errors=0, JUnit elapsed 24.348 seconds.
- independent_wheel_venv_without_audio: 32 passed, failures=0, errors=0, JUnit elapsed 21.085 seconds.

| Configuration | Correct / 22 | Answer p50 ms | Answer p95 ms | Future leakage cases |
|---|---:|---:|---:|---:|
| skills | 22 | 0.482 | 0.663 | 0 |
| direct | 21 | 0.126 | 0.291 | 0 |
| no_snapshots | 20 | 0.452 | 0.732 | 10 |
| no_folder | 20 | 0.469 | 0.746 | 0 |

All timings are this synthetic local run; they are not live-provider, Windows/macOS or sustained microphone measurements.

The README Linux command chain was also executed verbatim from `Testing Field/`: `python3.12 -m venv .venv`, `pip install -e ".[dev]"`, `init`, `demo`, `evaluate --output results/local-report.json --stress`, and `pytest --basetemp data/test-temp -q`. Results are recorded in `results/test-summary.json`. It uses a third app-local environment; all files remain inside the dedicated build root.

README verbatim suite: 32 passed in 21.17s; no failures or errors.
