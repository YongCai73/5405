# Changelog

## 0.3.1 (2026-11-28)

Documentation and repository layout only. The program is unchanged from 0.3.0.

- Root README rewritten as the project front page, with the download link and
  SHA-256 of the 0.3.0 Windows portable zip.
- Application README, launch guide and Chinese handoff brought up to date:
  current defaults, clone instructions for `main`, no machine-specific paths.
- Design notes no longer point at deleted task branches.
- Removed the unused placeholder package and placeholder test note at the
  repository root.
- Added this changelog.

## 0.3.0 (2026-11-28)

[Release page and Windows portable zip](https://github.com/YongCai73/5405/releases/tag/v0.3.0)

- Skills on top of MCP: versioned contracts, automatic routing, tool limits
  enforced in code, traces linked to tasks, recovery pinning.
- Discussion skills `clarify`, `grill_me` and `design_review`, with ratings,
  wording preferences and a user-triggered suggestion report.
- Desktop app: one-click launchers, model and API key set once in Settings,
  keys saved per provider in the system credential store, skills menu,
  portable Windows package.
- Answers beyond the meeting record, labelled apart from quotations.
- Questions in Chinese over an English record.
- Retrieval on overlapping time windows with citations mapped back to the
  original segments; whole-meeting questions receive the whole transcript.
- Agentic loops: the model's tool loop on by default, and a bounded
  answer, rewrite, retrieve cycle in the LangGraph graph.
- A direct client for OpenAI-compatible addresses.
- DeepSeek fix: `reasoning_content` is passed back across tool rounds.

## 0.2.0 and earlier

The CLI prototype, crash recovery, security hardening, the evaluation gate and
the first macOS and Windows desktop hosts. See
[BUILD_REPORT.md](Testing%20Field/docs/BUILD_REPORT.md) and the
[evidence index](Testing%20Field/results/README.md).
