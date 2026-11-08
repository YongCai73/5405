# Windows final-validation repair

The coordinator's actual Windows 11 / CPython 3.12.14 run of the frozen multiview source reported **68 passed, 4 failed, 1 skipped (73 cases)** in a dedicated local venv with dev+audio extras. Init, demo, evaluation and microphone enumeration succeeded; enumeration did not record audio. Earlier public local ASR and native NTFS junction checks succeeded. This repair is validated on Linux; **the repaired Windows suite is pending the coordinator rerun**. [source: coordinator inputs/summary.json; inputs/tests.xml; inputs/windows-junction-result.json; user repair request]

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

Collect the repair commit/source archive identified in build-root `WINDOWS_REPAIR.json`, and update the local `Testing Field` tracked source and tests from it while preserving the dedicated `.venv`. Start in that updated `Testing Field` directory. Keep the historical v0.2.0 tag separate from this branch repair commit. [source: WINDOWS_REPAIR.json; user repair request]

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
& $py -m pytest tests/test_audit_regressions.py tests/test_project.py tests/test_live_processes.py -k 'late_file_identifier or tool_reads_preserve or refresh_retains or separate_cli' -q --basetemp=data/windows-repair-validation/targeted-temp --junitxml=data/windows-repair-validation/targeted.xml 2>&1 | Tee-Object -FilePath data/windows-repair-validation/targeted.stdout.log
if ($LASTEXITCODE -ne 0) { throw 'Targeted Windows rerun failed; collect artifacts' }
& $py -m pytest -q --basetemp=data/windows-repair-validation/full-temp --junitxml=data/windows-repair-validation/full.xml 2>&1 | Tee-Object -FilePath data/windows-repair-validation/full.stdout.log
if ($LASTEXITCODE -ne 0) { throw 'Full Windows rerun failed; collect artifacts' }
```

The targeted selection should collect seven cases. The full suite should collect 76; if symlink permission remains unavailable, the expected count is 75 passed and one skipped, subject to the actual coordinator result. Collect both JUnit/stdout pairs, Python/package version output and the exact repair commit or source archive SHA-256. Report unexpected failures or changed skips verbatim; do not turn the historical junction evidence into a symlink pass. Init/demo/evaluation, public ASR and mic enumeration already passed; no new microphone recording, paid API call or 20-minute soak is required for these unchanged runtime files. [source: tests/test_audit_regressions.py; tests/test_project.py; tests/test_live_processes.py; user repair request]

## 中文说明

三项文件测试把写盘前的 LF 字符串当成了 Windows 实际保留的 CRLF 内容，导致字符偏移与读取结果比较失败；现在统一用精确字节夹具，并在所有系统上分别覆盖 LF/CRLF，校验原始全文哈希、保留文本和原始行号。运行时代码没有改动。 [source: tests/test_audit_regressions.py; tests/test_project.py]

跨进程测试原来只等 12 秒，而且模拟采集约 20 秒就结束，可能在 Windows 冷启动导入期间提前失败。现在用“已提交采集且摘要成功”和“真实 ask 子进程到达本地端点”握手，各自最多等待 120 秒；采集持续到停止信号，回答释放前必须证明采集和共享摘要都在推进，仍严格检查快照、丢弃、错误及进程回收。 [source: tests/test_live_processes.py; tests/test_protocols.py]

Linux 结果见随附报告，Windows 修复后的结果仍待协调器复测；此前 NTFS junction 检查通过与 symlink 因权限不足跳过是两件事，不能混称。运行时未变，因此保留审计已完成的 20 分钟稳定性结果，不为测试和文档重跑长测。 [source: results/windows-repair-validation.json; coordinator inputs/windows-junction-result.json; results/audit-soak-final.json; user repair request]
