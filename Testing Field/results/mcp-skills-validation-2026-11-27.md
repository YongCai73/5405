# MCP + Skills validation record, 2026-11-27

Branch `jiaxuan/mcp-skills`, based on `origin/main` at `197aaa7` (re-fetched at the
start; it was still the latest). Design and behavior: [../docs/MCP_SKILLS.md](../docs/MCP_SKILLS.md).

## Environment

| Item | Value |
|---|---|
| Machine | Windows 11 Home 10.0.26200, one developer laptop |
| Python | 3.12 virtual environment created with `uv`, `pip install -e ".[dev,audio]"` |
| Node | v26.13.0 (desktop UI contract tests only) |
| Provider for all runs below | local `demo` extractive provider, or a loopback fake HTTP server |
| Real API requests sent | **none** |

No API key was available in the session, and the workspace key file was not read.

## What was executed

| Check | Kind | Result |
|---|---|---|
| Full pytest suite, this branch | deterministic | 275 passed, 10 skipped, 0 failed, 325.87 s. [JUnit](tests-mcp-skills-windows.xml) |
| The 10 skips | platform | 9 in `test_security_files.py` (POSIX permissions/links/locks not applicable on Windows), 1 symlink permission in `test_project.py` |
| Same suite on unmodified `origin/main`, same machine | deterministic baseline | 228 tests: 226 passed + 2 timing-sensitive desktop tests that failed while another test run was using the machine and passed (3/3 in that file) when re-run alone; 10 skipped |
| Desktop UI contract tests (`node --test tests/test_desktop_audio_ui.mjs`) | deterministic | 10 passed |
| `evaluate --stress --strict` | deterministic, simulated provider | release gate passed, 12/12 checks. [Report](mcp-skills-evaluation-2026-11-27.json) |
| `scripts/skills_demo.py` | real CLI + real stdio MCP session, simulated provider | ran to completion. [Transcript](mcp-skills-demo-2026-11-27.txt) |

New tests in this branch: `tests/test_skill_runtime.py` (36), `tests/test_deepseek_protocol.py` (10),
one added stdio test in `tests/test_mcp.py`. Updated: `tests/test_evaluation.py`
(three new gate checks) and one assertion in `tests/test_protocols.py` (see below).
The test count is a record of what ran, not a measure of quality.

### Strict gate detail (simulated provider)

| Check | Result |
|---|---|
| Fixture questions, fixed skill / `direct` / `auto` | 22/22, 21/22, 22/22 by expected-substring scoring |
| Citation membership (exact quote in the cited source) | 22/22 in all three |
| Future-evidence leakage, guarded runs | 0 |
| Routing regression set | 24/24 |
| Skill-control suite | 13/13 |
| Access-policy suite / recovery C0–C4 | 10/10, 5/5 (unchanged from `main`) |

Auto routing on the 22 fixture questions: 6 matched a skill (4 `trace_decision`,
1 `catch_up`, 1 `compare_options`), 16 had no trigger phrase and ran as `direct`.
The fixture's own skill labels predate the router and mark plain factual questions
as `trace_decision`, so agreement with those labels (6/22) is not an error rate.

### What each topic is covered by

| Topic | Where |
|---|---|
| Auto and manual routing, ambiguity, not applicable | 24 routing cases; `test_routing_is_explainable_and_manual_choice_is_kept`; CLI test |
| Misrouted question, no evidence | `misrouted_question_without_evidence_abstains`; `test_no_evidence_abstains_without_a_provider_call` |
| Illegal tool, illegal arguments, forged identity | skill-control cases on both surfaces; `test_shared_argument_validation`; stdio MCP test; over-the-wire injection test in `test_protocols.py` |
| Tool allowlist and budget enforced in code | `test_model_tool_outside_skill_is_denied_in_code_and_traced` (runs the real provider tool loop with a scripted model) |
| Revocation | `revoked_scope_denied_on_both_surfaces`; existing stdio and recovery tests |
| Timeout | `test_timeout_is_traced_and_resume_keeps_skill_and_task`; existing wire timeout tests |
| Recovery and version change | `resume_keeps_pinned_skill_and_task`, `contract_change_restarts_retrieval_same_skill`, `test_stored_answer_keeps_its_skill_version_when_the_contract_changes`, `test_legacy_checkpoint_without_routing_still_resumes`; existing C0–C4 tests pass unchanged |
| Same question, other skill | `same_question_other_skill_reports_conflict`; the existing store-rule test passes unchanged |
| Trace correlation, no argument text in trace | `auto_route_events_share_task_id`, `denials_traced_without_argument_text`, CLI `trace-show` test |

## With and without skills

Same evidence, same provider, same budget (`skills` and `direct` share frozen
retrieval rows; `tool_rounds=0`). Simulated provider, from the report above:

| Variant | Correct | Quote membership | Abstained | Validated inferences | Provider calls | Tokens / cost |
|---|---|---|---|---|---|---|
| fixed skill | 22/22 | 22/22 | 2 | 0 | 0 | 0 / $0 |
| `direct` (no skill) | 21/22 | 22/22 | 2 | 0 | 0 | 0 / $0 |
| `auto` | 22/22 | 22/22 | 2 | 0 | 0 | 0 / $0 |

**This table is not evidence that skills help.** The simulated provider extracts
one sentence per source and ignores the skill contract. The single case that differs
is `catch_up`, where `direct` reuses the same rows but applies a keyword filter the
`catch_up` skill does not. Latencies in the report are local milliseconds of host
code only and are not meaningful as a comparison. A real comparison needs
`evaluate --api` with a configured provider, and human review of the answers;
neither was done.

## Not verified

- **Real DeepSeek endpoint.** The `reasoning_content` passback, the `thinking`
  switch and tool rounds were tested only against a loopback mock built from the
  official documentation as read on 2026-11-27. Whether the live endpoint accepts
  the request shape through LiteLLM is unknown.
- **Real-model quality, tokens, cost, latency** with or without skills.
- **Semantic correctness.** Scoring is expected substrings and exact-quote
  membership.
- **Routing accuracy on real questions.** The 24 cases were labelled by the router's
  author while writing the trigger phrases.
- **Desktop apps.** Only the Python bridge tests and the JavaScript contract tests
  ran. The Windows and macOS applications were not rebuilt or opened, so the
  auto-routed typed question was not seen in a running window.
- **macOS, live microphone, ASR.** Not touched by this work and not re-run.

## Behavior changes other owners should know

1. CLI `ask` without `--skill` now routes automatically instead of using
   `trace_decision`. `Assistant.ask()` and `Workers.submit()` keep their defaults.
2. API evaluation: up to 110 requests instead of 88. A case with no retrieved
   evidence abstains without calling the provider, so the old exact-88 assertion in
   `test_protocols.py` now checks the count against the report.
3. Answers, checkpoints and trace rows have new keys (`skill_version`,
   `contract_sha256`, `routing`, `task_id`, `abstain_reason`, `validation_report`).
   The checkpoint format version is still 1 and `store.py` was not modified.
4. The model prompt now receives the task-relevant part of the skill contract, not
   the whole JSON file.
5. All ten MCP tools remain registered. A session started with `mcp SNAPSHOT --skill NAME`
   refuses tools outside that skill; without `--skill` behavior is as before, with
   a limit of 1000 calls per session.
