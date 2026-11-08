# Windows final-validation repair

The coordinator's initial Windows 11 / CPython 3.12.14 run reported **68 passed, 4 failed, 1 skipped (73 cases)**. The rerun at repair `097fec3` reported **73 passed, 2 failed, 1 skipped (76 cases)**: the actual LF/CRLF cases passed, while capture identity and first-provider startup still failed. Init, demo, evaluation and microphone enumeration passed; no recording or paid API was used. **This second repair is validated on Linux and awaits a new coordinator Windows rerun.** [source: coordinator inputs/summary.json; inputs/windows-repaired-summary.json; inputs/windows-repaired-tests.xml; user repair request]

## Second observed repair: process identity and cold provider startup

The Windows venv launched PID 9048, while the capture interpreter reported PID 20584 after processing at least ten segments and completing more than five summaries. A venv redirector can launch the interpreter as its child, so equality with the launcher PID rejects this legitimate shape. The new readiness record includes the interpreter's own PID, PPID and a fresh parent-generated nonce. It must match either `(PID = launcher, PPID = test parent)` or `(PID != launcher, PPID = launcher)`, with the matching nonce. An unrelated parent, sibling, stale nonce or test-parent PID is rejected. The final shutdown record must carry the same identity. [source: coordinator inputs/windows-repaired-tests.xml; tests/test_live_processes.py:assert_capture_identity; user repair request]

Readiness still requires committed ingestion and successful shared summaries. The real CLI must reach fake HTTP, and both ingestion and summaries must advance while that provider response is gated and the ask process remains pending. Answer/snapshot membership, cutoff, newer evidence and summaries, zero gaps/drops/errors, and child exit assertions remain. Cleanup first releases the response gate and signals capture stop; a bounded Windows fallback kills the still-owned launcher tree before reaping it, including redirected interpreters. The fallback itself still needs native Windows exercise. [source: tests/test_live_processes.py]

The first OpenAI protocol test timed out with zero endpoint requests; later protocol tests passed. This supports cold LiteLLM/OpenAI/tokenizer initialization consuming the old 20-second budget before HTTP, rather than proving a network/provider defect. The exact distribution of native startup time was not profiled. A dependency warning also reported an unawaited OpenAI coroutine after that timeout; this repair does not claim to fix dependency internals. [source: coordinator inputs/windows-repaired-tests.stdout.log; general inference]

Successful fake-wire contracts and the cross-process ask now use an explicit **120-second** budget, with no retries or success bypass. Nonstream timeout remains **0.1 seconds**, stream timeout **0.15 seconds**, and explicit cancellation deadlines remain short. New regressions verify that both `chat()` and `complete()` cancel pending pre-HTTP asynchronous startup at 0.1 seconds. Runtime code remains unchanged: `chat()` and `complete()` already apply `asyncio.wait_for`, and the SDK receives the configured timeout with zero retries. Synchronous imports can temporarily block the event loop, so these cooperative deadlines should not be described as a hard wall-clock limit on Python import work. [source: tests/test_protocols.py; tests/test_live_processes.py; src/meeting_copilot/provider.py; general inference]

For an already authorized API configuration on Windows, use `"timeout_seconds": 120` for first use; **60 seconds** is a reasonable smaller budget after startup if local measurements support it. `Config.load()` permits up to 120 seconds; the shipped default remains 20. Edit that field in the existing local JSON configuration without changing provider, endpoint, credentials or upload consent. A larger budget allows cold startup but does not establish live provider reliability or paid-model quality. [source: src/meeting_copilot/config.py; src/meeting_copilot/provider.py; general inference]

The fake server catches `ConnectionError` only in response header/body/stream emission, covering broken pipe, reset and Windows `ConnectionAbortedError` during intentional cancellation. Request reads, JSON decoding and ordinary schema/assertion failures remain outside that exception class and are not suppressed. [source: tests/test_protocols.py:fake_http]

Linux results and reproducible controlled experiments are recorded in `results/windows-repair2-validation.json`: direct launch, a real launcher/interpreter child shape, the retained 13-second capture/21-second ask delay experiment, and a first provider request delayed 21 seconds before real loopback HTTP. These are contract tests with fake responses, not native Windows or paid/live provider results. [source: results/windows-repair2-validation.json]

## Root causes and retained-text contract

The three file failures used `Path.write_text()` with LF strings. Windows wrote CRLF bytes, while assertions sliced or compared the original LF variable. The connector deliberately decodes UTF-8 bytes without newline translation, retains that text, and hashes its UTF-8 representation; for these accepted UTF-8 files this is the SHA-256 of the original bytes. Normalizing retained content would change the provenance contract. [source: coordinator inputs/tests.stdout.log; src/meeting_copilot/project.py:scan; src/meeting_copilot/store.py:refresh]

The contract remains:

- Raw retained evidence contains the complete decoded file text, including its original LF or CRLF endings. Hashes identify the complete retained revision, even when a bounded excerpt is returned. [source: src/meeting_copilot/project.py:scan; src/meeting_copilot/store.py:refresh; src/meeting_copilot/retrieval.py:excerpt]
- Character offsets are zero-based, half-open Python Unicode string positions in that original decoded text; they are not byte offsets. Original line citations are one-based. Each CR and LF counts separately toward character offsets. [source: src/meeting_copilot/assistant.py:citation; src/meeting_copilot/service.py:read_project_file]
- `read_project_file()` preserves internal line endings in the selected retained slice and removes trailing CR/LF characters from that display slice. It applies a 16,000-character bound and returns the original offsets, revision and complete-file hash. This bounded display does not rewrite stored evidence. [source: src/meeting_copilot/service.py:read_project_file]
- A previous snapshot continues reading its retained revision after refresh or deletion, until authorization is revoked. Multiple supplied views can each support their own original line citations; a quotation from an unsent middle view remains rejected. [source: tests/test_project.py:test_refresh_retains_old_hash_lines_and_deleted_files; tests/test_audit_regressions.py:test_tool_reads_preserve_multiple_views_and_exact_original_lines]

All three tests now write exact UTF-8 bytes and run separate LF and CRLF cases on every OS. Assertions check the actual bytes, retained raw text, full-file hash and original character/line positions, including lines 241, 2002 and 2003. The added parameters increase the suite from 73 to 76 cases. [source: tests/test_audit_regressions.py; tests/test_project.py]

The process failure observed no provider request within its fixed 12-second startup window. That observation alone does not establish an application error: imports/startup had not reached the fake endpoint. The old synthetic capture also stopped after roughly 20 seconds even if the ask child was still starting. [source: coordinator inputs/tests.stdout.log; audit baseline tests/test_live_processes.py at 5831912; general inference]

The replacement waits up to 120 seconds per startup phase, fails early on child exit with diagnostics, and uses two meaningful handshakes: the capture child atomically publishes readiness after at least 10 committed segments and more than five completed summaries; the actual `python -m meeting_copilot ... ask` child signals readiness by reaching the loopback endpoint. Capture continues until the parent's stop file, including during imports. The endpoint holds its response with a bounded event gate while the parent requires both new ingestion and new shared summaries with the ask still pending; only then does the parent release the answer. The test still checks an actual answer, snapshot membership/cutoff, newer evidence and summary watermarks, no missing events, no drops/errors and child cleanup. Cleanup releases the gate, signals capture stop, drains children, and kills/reaps a child if it cannot exit within the cleanup bound. [source: tests/test_live_processes.py; tests/test_protocols.py:fake_http]

## Validation and scope

See `results/windows-repair-validation.json` and the matching sanitized JUnit/stdout artifacts for executed Linux tests and a controlled delayed-start experiment. No runtime, packaging, version or upstream files changed: both source and package version remain 0.2.0, and the original upstream attribution and release tags remain intact. The completed final-runtime 1,200.336-second real local ASR replay result remains applicable to the unchanged runtime; no additional soak was run for this test/documentation repair. [source: results/windows-repair-validation.json; results/audit-soak-final.json; pyproject.toml; src/meeting_copilot/__init__.py]

The original Windows symlink test **skipped because native symlink permission was unavailable**. The coordinator separately verified a native NTFS junction: scan escape blocked, root approval rejected, junction removed nonrecursively, external canary unchanged. That earlier junction result names source commit `b42ddcf8a22217ca5757fc5e577c77d42a8d2fb0`; it is separate evidence, not a symlink capability pass or a new repaired-build Windows result. Microphone recording and paid APIs were not used. [source: coordinator inputs/tests.stdout.log; inputs/windows-junction-result.json; user repair request]

## Exact coordinator rerun (PowerShell)

Collect the second repair commit/source archive identified in build-root `WINDOWS_REPAIR2.json`, and update the local `Testing Field` tracked source and tests from it while preserving the dedicated `.venv`. Start in that updated `Testing Field` directory. Keep the historical v0.2.0 tag separate from this branch repair commit. [source: WINDOWS_REPAIR2.json; user repair request]

```powershell
$py = Join-Path $PWD '.venv\Scripts\python.exe'
& $py -c "import sys; print(sys.version); assert sys.version_info[:3] == (3, 12, 14)"
if ($LASTEXITCODE -ne 0) { throw 'Use the dedicated CPython 3.12.14 venv' }
# Rebind this venv to the collected source, without changing resolved dependencies.
& $py -m pip install --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw 'Editable source installation failed' }
& $py -c "import importlib.metadata as m, meeting_copilot as c; print(c.__file__); print(c.__version__); assert c.__version__ == m.version('meeting-copilot-prototype') == '0.2.0'"
if ($LASTEXITCODE -ne 0) { throw 'Source/package version mismatch' }
New-Item -ItemType Directory -Force 'data\windows-repair-validation' | Out-Null
& $py -m pytest tests/test_audit_regressions.py tests/test_project.py tests/test_live_processes.py tests/test_protocols.py -k 'capture_identity or separate_cli or late_file_identifier or tool_reads_preserve or refresh_retains or wire_protocol or timeout or cancel' -q --basetemp=data/windows-repair-validation/targeted-temp --junitxml=data/windows-repair-validation/targeted.xml 2>&1 | Tee-Object -FilePath data/windows-repair-validation/targeted.stdout.log
if ($LASTEXITCODE -ne 0) { throw 'Targeted Windows rerun failed; collect artifacts' }
& $py -m pytest -q --basetemp=data/windows-repair-validation/full-temp --junitxml=data/windows-repair-validation/full.xml 2>&1 | Tee-Object -FilePath data/windows-repair-validation/full.stdout.log
if ($LASTEXITCODE -ne 0) { throw 'Full Windows rerun failed; collect artifacts' }
```

The targeted selection should collect 22 cases. The full suite should collect 85; if symlink permission remains unavailable, the expected count is 84 passed and one skipped, subject to the actual coordinator result. Collect both JUnit/stdout pairs, Python/package version output and the exact repair commit or source archive SHA-256. Report unexpected failures or changed skips verbatim; do not turn the historical junction evidence into a symlink pass. Init/demo/evaluation, public ASR and mic enumeration already passed; no new microphone recording, paid API call or 20-minute soak is required for these unchanged runtime files. [source: tests/test_audit_regressions.py; tests/test_project.py; tests/test_live_processes.py; tests/test_protocols.py; user repair request]

## 中文说明

`097fec3` 的 Windows 复测为 73 通过、2 失败、1 跳过，LF/CRLF 实测已通过。此次只修测试和文档：采集握手用随机 nonce、真实 PID/PPID 校验直接启动或 venv 启动器父子关系，保留采集/摘要推进、快照截止、零丢失/错误和清理断言。成功协议测试设 120 秒冷启动预算，负向超时测试仍为 0.1/0.15 秒；Windows 首次 API 使用可在原有已授权配置中把 `timeout_seconds` 设为 120，启动后按测量考虑 60。假 HTTP 只忽略写响应时的连接中断类异常。运行时、0.2.0 版本和归属声明不变，不重跑长测，不声称付费 API 或新 Windows 通过；最终结果仍待协调器复测。 [source: coordinator inputs/windows-repaired-summary.json; tests/test_live_processes.py; tests/test_protocols.py; src/meeting_copilot/config.py; user repair request; general inference]

三项文件测试把写盘前的 LF 字符串当成了 Windows 实际保留的 CRLF 内容，导致字符偏移与读取结果比较失败；现在统一用精确字节夹具，并在所有系统上分别覆盖 LF/CRLF，校验原始全文哈希、保留文本和原始行号。运行时代码没有改动。 [source: tests/test_audit_regressions.py; tests/test_project.py]

跨进程测试原来只等 12 秒，而且模拟采集约 20 秒就结束，可能在 Windows 冷启动导入期间提前失败。现在用“已提交采集且摘要成功”和“真实 ask 子进程到达本地端点”握手，各自最多等待 120 秒；采集持续到停止信号，回答释放前必须证明采集和共享摘要都在推进，仍严格检查快照、丢弃、错误及进程回收。 [source: tests/test_live_processes.py; tests/test_protocols.py]

Linux 结果见随附报告，Windows 修复后的结果仍待协调器复测；此前 NTFS junction 检查通过与 symlink 因权限不足跳过是两件事，不能混称。运行时未变，因此保留审计已完成的 20 分钟稳定性结果，不为测试和文档重跑长测。 [source: results/windows-repair-validation.json; coordinator inputs/windows-junction-result.json; results/audit-soak-final.json; user repair request]
