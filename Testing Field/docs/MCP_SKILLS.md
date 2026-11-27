# MCP + Skills: routing, contracts, enforcement and trace

Owner: Jiaxuan Yan. Branch `jiaxuan/mcp-skills`, based on `main` at `197aaa7`.
Recorded 2026-11-27. Validation results for this branch are in
[results/mcp-skills-validation-2026-11-27.md](../results/mcp-skills-validation-2026-11-27.md).

The flow is **choose a skill → host-controlled evidence → cited answer**, still on
the existing LangGraph `retrieve → answer` graph. No second agent framework was
added.

## What actually goes through MCP

This was checked in the code, not assumed.

| Surface | Code | Who calls it | Is it MCP? |
|---|---|---|---|
| stdio MCP server, 10 read-only tools | `mcp_server.py` → `EvidenceService.call` | an external MCP client (`meeting-copilot mcp SNAPSHOT`) | Yes. Real MCP protocol over stdio. |
| Model tool loop, 2 read-only tools | `provider.Provider.complete` → `model_tools.EvidenceTools.call` | the configured model, when `tool_rounds > 0` | **No.** An in-process Python tool loop using OpenAI/Anthropic function calling. |
| Host retrieval | `Assistant.retrieve` | the graph, before any model call | No. Host code reading the pinned snapshot. |

So a private answer produced by `ask` does **not** travel through MCP end to end,
and nothing here should be described that way. What the two tool surfaces now share
is one gate, `skill_runtime.guarded_call`: skill allowlist → call budget → argument
validation → host-scoped read → one trace row. Identity, snapshot and folder scope
are bound when the surface object is built; they are not arguments on either
surface.

One transport detail found by test: the MCP SDK drops undeclared arguments and
rejects out-of-range ones **before** the tool body runs. Over stdio a forged
`principal`/`snapshot` argument is therefore ignored (it has no effect, and no
trace row is written for an SDK-level rejection), while the same argument on the
model surface or the in-process gateway is refused as `invalid_arguments`.

## Skill contracts

`src/meeting_copilot/skills/*.json`, validated on load by
`skill_runtime.check_contract`. `direct` (no skill) is defined in code with the same
shape.

| Field | Meaning | Enforced where |
|---|---|---|
| `version` | semantic version, now `1.1.0` | recorded in every answer, checkpoint and trace row |
| `routing.phrases` | trigger phrase → weight (1–5) | `route()` |
| `retrieval_plan` | `keyword` (≤16 rows) or `recent_segments` (latest 8) | `Assistant.retrieve` |
| `allowed_tools.model` / `.mcp` | tool allowlist per surface | `ToolPolicy.admit` |
| `termination` | max tool rounds, tool calls, claims, inferences, MCP calls per session | `ToolPolicy`, `Provider.complete`, `check_output` |
| `output_fields`, `abstention`, `citation`, `task_steps` | output shape and task wording | `validated_inferences`, `check_output`, prompt |

| Skill | Model tools | MCP tools | Tool rounds / calls |
|---|---|---|---|
| `catch_up` | `read_evidence` | status, meeting, transcript, summary, read_evidence | 1 / 2 |
| `trace_decision` | `search_evidence`, `read_evidence` | all except `list_project_files` | 2 / 4 |
| `compare_options` | both | all 10 | 2 / 6 |
| `direct` | both | all 10 | 2 / 4 |

A skill can only lower the host's `tool_rounds`, never raise it. A model sees only
the tools its skill allows, and a call to any other name is refused in code. When a
budget is exhausted the model receives a `denied` tool result and must answer from
what it already has, or abstain. `contract_sha256` in each answer detects a contract
edit that forgot to bump `version`.

List the live values with `python -m meeting_copilot skills`. The branch
`jiaxuan/discussion-skills` adds `clarify`, `grill_me`, `design_review` and the
internal `meta_suggest` contract; see [DISCUSSION_SKILLS.md](DISCUSSION_SKILLS.md).

## Routing

`--skill auto` is now the CLI default; typed questions in the desktop UI also use
`auto`. `--skill catch_up|trace_decision|compare_options|direct` and the desktop
prompt buttons still choose manually. `Assistant.ask(...)` keeps its Python default
of `trace_decision`, so existing callers are unchanged.

Routing is a weighted whole-word phrase match, with no model call and no evidence
read. `python -m meeting_copilot route "question"` prints the scores and matched
phrases.

| Status | When | What runs |
|---|---|---|
| `manual` | the user named a skill | that skill |
| `matched` | one skill has the highest positive score | that skill |
| `ambiguous` | two or more skills tie for the highest score | `direct`; candidates are listed so the user can choose |
| `not_applicable` | no trigger phrase matched | `direct` (plain keyword retrieval, one generic field) |

No evidence: if retrieval returns nothing, the answer abstains with
`abstain_reason: no_evidence_retrieved`. With an API provider and no tool rounds,
the provider is not called at all for that case, so it costs nothing. A wrongly
routed question does not get re-routed to another skill behind the user's back; it
abstains, and the routing decision is in the answer.

Known limits: English trigger phrases only; "why" always scores for
`trace_decision` even in an unrelated question; a tie falls back to `direct`
rather than asking the user interactively.

## Recovery compatibility

The checkpoint format stays at version 1. Four keys were added and are preserved
by the store: `routing`, `task_id`, `skill_version`, `contract_sha256`.

- **A resume never re-routes.** A checkpoint applies when the question matches and
  the skill request is the same value as before (a name or `auto`), or resolves to
  the pinned skill. The pinned routing and task ID are reused even if the trigger
  phrases changed since.
- **Contract changed, only evidence stored (`retrieved`)**: retrieval is redone
  under the current contract of the same skill and a `skill_contract_changed` trace
  event is written.
- **Contract changed, answer already stored (`answered`/`saved`)**: the stored
  answer is kept with the version that produced it. It is not regenerated.
- **Checkpoints written before this change** have no routing or version; they
  resume exactly as before.
- **Same question, different skill.** The private-answer key is still
  `(snapshot, user, conversation, question)`; the store was not changed. The first
  committed answer is still returned, now with an explicit `skill_conflict` object
  (requested skill, skill that answered, how to get a new answer), a stderr note in
  the CLI and a `skill_conflict` trace event. Use a new `--conversation` or a new
  snapshot to answer the same question with another skill.
- Revocation still wins: `snapshot_info` runs before any checkpoint or route is used.

## Trace

Still the local `traces.jsonl` with an allowlist; no new log system. One `ask` gets
one `task_id`, kept across a crash and resume. Every event written during that ask
carries `task_id`, `skill` and `skill_version` through a context variable.

| Event | Added fields |
|---|---|
| `skill_route` | `route_mode`, `route_status`, `candidates`, `step` (new/resumed), `latency_ms` |
| `retrieval` | `latency_ms` |
| `tool_call` | `tool`, `tool_surface` (model/mcp), `policy` (allow/deny/error), `reason`, `source_ids`, `latency_ms` |
| `answer` | `tool_calls`, `tool_denied`, `reason` (abstain reason) |
| `ask_failed` | `error`: a fixed provider code or exception class name |
| `skill_contract_changed`, `skill_conflict` | `reason` |

Not recorded: questions, queries, tool arguments, evidence text, model reasoning,
provider error messages, keys. An unknown tool name is written as `unknown`, not
as the model-supplied string. An MCP session gets one `mcp-…` task ID.

```powershell
python -m meeting_copilot trace-show --task TASK_ID
python -m meeting_copilot trace-show --snapshot SNAPSHOT_ID
```

## DeepSeek tool rounds

Facts below were read from the official documentation on 2026-11-27
([thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/),
[tool calls](https://api-docs.deepseek.com/guides/tool_calls),
[chat completion](https://api-docs.deepseek.com/api/create-chat-completion),
[pricing](https://api-docs.deepseek.com/quick_start/pricing/)). They may change.

- Model names `deepseek-flash` and `deepseek-v4-pro`; thinking is on by default.
- `{"thinking": {"type": "enabled" | "disabled"}}` in the request body switches it.
- With `tools` in thinking mode, `reasoning_content` of earlier assistant turns must
  be sent back, otherwise the API returns HTTP 400.
- In streaming, reasoning arrives in `delta.reasoning_content` and usage is on the
  last chunk.

What changed in `provider.py`:

1. `reasoning_content` is read from non-streaming and streaming responses and sent
   back on each assistant tool-call turn of the same loop (OpenAI-compatible
   provider only). It is bounded, kept only in memory for that loop, and never
   written to an answer, checkpoint or trace.
2. New optional config key `thinking`: `null` (provider default), `"enabled"` or
   `"disabled"`. It is sent as `extra_body`.
3. When tool rounds are active, prior conversation context is folded into the user
   message instead of a synthetic assistant turn, because a fabricated assistant
   message has no `reasoning_content` to send back.

**Verification status: mock only.** `tests/test_deepseek_protocol.py` runs the real
adapter and LiteLLM against a loopback server that imitates the rules above,
including a negative control showing the mock returns 400 when the passback is
missing. No request was sent to the real DeepSeek endpoint in this work: no API key
was available in the session and the key file was deliberately not read. Whether the
live endpoint accepts this exact request shape through LiteLLM is **unverified**.
Keep `tool_rounds: 0` for real meetings until someone runs the check in
[handoff.md](../handoff.md).

## Evaluation

`evaluate` gained an `auto` variant, a routing regression set, a skill-control
suite and a `skill_comparison` table (correctness, citation membership, abstentions,
validated inferences, provider calls, latency, tokens, cost) for `skills`, `direct`
and `auto`. `skills` and `direct` use the same frozen retrieval rows, provider,
model and budget. Three checks were added to the strict release gate.

With the default simulated provider the answers are deterministic sentence
extraction, which ignores the skill contract apart from `catch_up` ordering. That
run can show the controls execute; it cannot show that skills improve answers. An
API run (`evaluate --api`) gives a real comparison but still scores by expected
substrings and exact-quote membership, on 22 synthetic questions. Semantic quality
needs human review.

## Demonstrations

```powershell
python scripts/skills_demo.py
```

Uses a temporary data directory and the local extractive provider; no key, no
network, and it does not touch `config.json` or `data/`. It shows auto routing, a
cited answer, the correlated trace, and refused calls on both tool surfaces.
