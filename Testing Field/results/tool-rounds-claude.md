# Tool rounds with Claude (Zimo Li, 2026-11-14)

Handoff P0: "DeepSeek endpoint and answer quality; fix `reasoning_content` tool continuation or make non-thinking configurable, then enable extra tool rounds."
This note checks the Anthropic path. The DeepSeek-specific `reasoning_content` issue is not covered here.

Setup: `provider: anthropic`, `model: claude-haiku-5-5`, sample meeting from `examples/replay.jsonl` plus the approved `examples/project` folder (2 files).
Each question was asked with `tool_rounds: 0` (current default) and `tool_rounds: 2` (local `config-tools.json`, not committed), in separate conversations.

| Question | tool_rounds | Tool calls | Input / output tokens | Latency | Answer |
|---|---|---|---|---|---|
| Why did we choose SQLite, and how does the project code use it? | 0 | 0 | 904 / 876 | 10.7 s | Correct; says code-level use is unverified |
| same | 2 | 2 | 5137 / 1533 | 14.4 s | Same content, slightly better structured |
| What timeout and retry settings does the worker use? | 0 | 0 | 958 / 1348 | 10.6 s | Correct, cites `worker.py` and the transcript |
| same | 2 | 0 | 1488 / 846 | 9.5 s | Same content |

Findings:
- **Tool rounds work with Claude**: two rounds ran without errors, and tool results were folded into a cited answer. The tool-continuation problem in the handoff is specific to DeepSeek thinking mode.
- Claude only called tools when the pre-selected evidence was incomplete (question 1). When the evidence was enough (question 2), it answered directly.
- Cost: offering tools adds ~55% input tokens even when none are called (tool definitions). When tools are called, input tokens grow ~5.7×, plus ~3–4 s latency.
- Quality did not improve on this sample. The project has only two short files, and the pre-selection already finds everything relevant. Both questions were answered correctly with 0 rounds. In question 1, the extra searches confirmed that no SQLite code exists.

Recommendation: keep `tool_rounds: 0` as the default for now. Re-evaluate with 1–2 rounds on a larger approved project folder, where pre-selection is more likely to miss evidence, as part of the evaluation suite (Qiyan) and together with the DeepSeek fix (Jiaxuan).
