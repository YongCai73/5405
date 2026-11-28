# Discussion skills and meta-suggestion: validation record, 2026-11-27

Branch `jiaxuan/discussion-skills`, stacked on `jiaxuan/mcp-skills` (`eea17b0`), which is
based on `origin/main` at `197aaa7`. Design: [../docs/DISCUSSION_SKILLS.md](../docs/DISCUSSION_SKILLS.md).

## Environment

Same Windows 11 laptop and Python 3.12 environment as the
[MCP + Skills record](mcp-skills-validation-2026-11-27.md). Providers used: the local
`demo` extractive provider, a scripted provider (fixed replies passed through the real
prompt assembly, validation and storage), and loopback fake HTTP servers.
The automated runs in this record called no real model. A separate real-endpoint
run, done afterwards with the owner's approval, is in
[live-deepseek-validation-2026-11-27.md](live-deepseek-validation-2026-11-27.md).

## What was executed

| Check | Kind | Result |
|---|---|---|
| Full pytest suite on mains power | deterministic | 308 passed, 10 platform skips, 0 failed, 174 s (re-run after the fixes made from real-model output). |
| Same suite earlier on battery power | deterministic | 307 passed, 1 failed, 50 minutes. The laptop was throttled (module import took 26 s instead of 2 s) and the first mock-DeepSeek test hit its 120 s provider timeout during cold start. It passed in the rerun above with no code change. |
| Desktop UI contract tests | deterministic | 10 passed |
| `evaluate --stress --strict` | deterministic, simulated provider | gate passed, 12/12. Routing set 36/36, skill controls 17/17, fixture questions 22/22 fixed skill, 22/22 auto. |
| `scripts/skills_demo.py` | real CLI and stdio MCP, simulated provider | ran to completion. |

New tests: `tests/test_discussion_skills.py` (15) and `tests/test_meta_suggest.py` (6);
12 routing cases and 4 skill-control checks were added to the existing sets.

| Behaviour | Shown by | With |
|---|---|---|
| New skills are auto-routed; "Explain why…" still goes to `trace_decision` | routing cases r25–r36 | router |
| `clarify` carries explained terms across snapshots in one conversation, not into another | `test_clarify_remembers_explained_terms…`, control check | scripted + simulated |
| Background is labelled and kept apart; other skills cannot use it and make no provider call without evidence | `test_clarify_may_answer_from_labelled_background…` | scripted |
| Saved preferences reach the prompt as style-only rules | same clarify test | scripted |
| `grill_me`: one question per turn, round counter, topic kept, stays in session despite trigger words, exit phrase, round limit, sixth question refused | three `test_grill_me_…` tests, control check | scripted + simulated |
| Session is private to user and conversation; a manual skill leaves it | `test_grill_me_session_is_private…` | scripted |
| `design_review`: quotations vs model suggestions, invented quote and wrong field dropped, stock phrase flagged | `test_design_review_separates…` | scripted |
| Suggestions: preference vs existing skill vs new-skill candidate; nothing enabled | `test_rules_separate_preferences…` | simulated history |
| Feedback drives `adjust_skill`; one complaint changes nothing | `test_feedback_drives_adjust_skill…` | simulated history |
| History isolated per user, dropped after revocation; private tools refused outside `meta_suggest` | `test_history_is_isolated…`, control check, stdio CLI test | real store, stdio MCP |
| Model suggestions must cite real turns and registered tools | `test_model_suggestions_must_cite…` | scripted |

## Not verified

- **Any real-model behaviour.** Whether a model follows the style rules, avoids
  redefining remembered terms, asks useful challenge questions, writes sound
  assumptions and failure scenarios, or proposes useful skills. The scripted replies
  were written by the test author.
- **Usefulness on Tony's real questions**, which was the stated completion standard.
  That needs an API run on real meetings and his judgement.
- **Suggestion quality.** The rules were tested on a history built to contain the
  patterns. Precision on a real history is unknown.
- **Desktop.** The bridge accepts the new skill names and typed questions route
  automatically, but no prompt buttons were added and the apps were not rebuilt or
  opened. A grill session in the desktop conversation was not tried.
- **Languages other than English** for triggers, exit phrases and suggestion rules.

## Behaviour changes other owners should know

1. Three more auto-routable skills. Questions containing "explain", "clarify",
   "mean", "risks", "assumptions", "critique" or "review our/my" now route to them
   instead of `direct`.
2. `abstained` is now false when a `clarify` answer has labelled background but no
   quotation. For every other skill it is unchanged.
3. The MCP server registers 12 tools. The two new ones return only the caller's own
   question history and are refused unless the session was started with
   `--skill meta_suggest`.
4. One new table, `answer_feedback`, created on first use, and one `app_meta` key per
   user for preferences. `store.py` was not modified.
5. Private conversation history read for an answer went from 8 to 20 turns so term
   memory and sessions survive longer conversations; the model still receives only
   the last four as context.
