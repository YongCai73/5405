# Evidence index and time boundaries

Current handoff: [../handoff.md](../handoff.md). Current feature status: [requirements-current.json](requirements-current.json). Portable server audit/repair summary: [server-delivery.json](server-delivery.json). These survive collection without depending on build-root files.

| Record | Scope |
|---|---|
| [windows-validation.json](windows-validation.json), [full JUnit](tests-windows.xml), [stdout](tests-windows.stdout.log) | Actual Windows repair3 c763c14: 85 cases, 84 passed, one symlink-permission skip, no failures/errors. Pytest stdout 411.50s; JUnit 411.535s; launcher 434.89s. |
| [concurrency JUnit](tests-windows-concurrency.xml), [stdout](tests-windows-concurrency.stdout.log) | Separate actual Windows concurrency rerun: one passed. Pytest stdout 72.25s, JUnit 71.993s. |
| [windows-prepared.json](windows-prepared.json), [environment](windows-environment.txt), [source manifest](windows-source-manifest.json), [junction](windows-junction.json) | Actual Windows CLI, three enumerated sources, tiny.en public-recording ASR (two segments), 54 runtime + ten test hashes, native junction checks. No mic recording or paid endpoint. Environment is an observation, not a dependency constraint. |
| [repair3](windows-repair3-validation.json) | Latest Linux source: 85 full, 14 focused, one controlled cold-start case; representative bounded synthetic functional load. |
| [repair2](windows-repair2-validation.json), [repair1](windows-repair-validation.json) | Historical Linux test-contract repairs: ancestry/nonce/cold initialization/cancellation and LF/CRLF/startup. Their Windows-pending wording describes publication time; actual final Windows evidence is above. |
| [audit-validation.json](audit-validation.json), tests-audit*.xml | Historical v0.2 audit: 73 source, 73 independently installed wheel without audio, 73 README-environment cases. Not a latest 85-case wheel run. |
| [audit-soak-final.json](audit-soak-final.json) | Final completed Linux 1,200.336s stability run on frozen runtime: real ASR of repeated public audio, simulated five-second provider. Authoritative final stability evidence; no incomplete runs used. |
| [audit-soak-20m.json](audit-soak-20m.json) | Earlier completed soak during development; preserve as historical, prefer final report above. |
| [audit-report.json](audit-report.json), [audit-scale.json](audit-scale.json), [audit-install-smoke.json](audit-install-smoke.json), [audit-upstream.json](audit-upstream.json) | v0.2 synthetic evaluator, scale, clean install and upstream-byte checks; not real model/meeting quality. |
| [requirements-checklist.json](requirements-checklist.json) | Historical pre-Windows-repair v0.2 feature checklist; current mapping is requirements-current.json. |
| [test-summary.json](test-summary.json), tests.xml, tests-wheel.xml, tests-readme.xml | Historical v0.1: 32 cases per environment. Not current counts/platform status. |
| report.json, initial-eval.json, install-smoke.json, asr-smoke.json, local-report.json | Historical initial v0.1 evaluation/install/ASR records; synthetic expected-term scores and accelerated logical time are not live quality or 20-minute wall time. |

macOS, real microphone/unplug/20-minute technical discussion, paid-provider/native A/B quality and human ASR annotations remain unvalidated. Successful bounded functional cases do not guarantee zero drops at arbitrary rates; deliberate overload tests retain honest accounting. Original JSON/JUnit measurements are preserved. Release tags are historical; the coordinator will record the final docs head and append a cleanup receipt only after observed server cleanup.
