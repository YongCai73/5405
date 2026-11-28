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

No evidence, strict contract (`"general_answers": false`, the local extractive
mode, and always the API evaluation): if retrieval returns nothing, the answer
abstains with `abstain_reason: no_evidence_retrieved`. With an API provider and no
tool rounds, the provider is not called at all for that case, so it costs nothing.
A wrongly routed question does not get re-routed to another skill behind the
user's back; it abstains, and the routing decision is in the answer.

### Open answers (default with a configured model, added 2026-11-27)

In a meeting the owner asks things the record cannot answer, and a bare
"Insufficient evidence" stops the conversation. With `"general_answers": true`
(the default) and an API provider, every skill may also return `general_answer`:
the model's own answer for what the sources do not cover. The meeting record is
used the way retrieval-augmented generation uses its sources, as the basis where
it applies, and the answer says which part is which.

| | From the meeting or approved files | From the model |
|---|---|---|
| Where it appears | exact quotations with `[E…]` citations, and inferences that cite them | under the heading "Beyond the meeting record (model knowledge and reasoning; NOT from the meeting or project files, not verified)" |
| Host checks | unchanged: the quotation must be an exact substring of a retained source; an inference must cite validated quotations | citation markers are removed so it cannot pass as a quotation; at most 2000 characters |
| Answer fields | `claims`, `citations`, `inferences` | `general_answer.text`, `general_answer.label` |

`grounding` is `meeting`, `meeting_and_model`, `model_only` or `none`. When nothing
was quoted the text starts with "No direct evidence for this was found in the
meeting record or approved files." `abstained` and `abstain_reason` keep their
meaning, *nothing in this answer is grounded in the record*, so an answer can be
`abstained: true` and still carry a labelled model answer. The model is told never
to state in `general_answer` what the team decided, owns, measured or said.

What this changes and what it does not:

- The provider is now called even when nothing was retrieved, so such a question
  costs one request instead of none.
- `evaluate --api` forces `general_answers` off, so its abstention, citation and
  request-count numbers still measure the strict contract and stay comparable
  with earlier records. The deterministic release gate runs in local mode, which
  cannot write an open answer, and is unchanged.
- The host cannot verify model knowledge. The label says so; whether the model
  keeps team facts out of that section is prompt-enforced, not host-enforced.
- A model reply that holds nothing usable (no valid quotation and no answer of its
  own, which was seen once in real use) is followed by one more request for a plain
  answer with no meeting content, so an open question does not end in a bare
  refusal. If that request fails, the abstention text remains.

### Questions in Chinese (added 2026-11-27)

The transcript, project files, skill contracts and every matching rule stay
English. A question may be asked in Chinese; `language.py` is the only place that
knows about it, and the router, retrieval, session rules and suggestion rules run
unchanged on what it gives them.

| Step | English question | Chinese question |
|---|---|---|
| Routing, session exit phrases, suggestion cues | match the English phrases | match the question plus its English *gloss*: the English cue of each glossary phrase found ("为什么" → `why`, "对比" → `compare`, "够了" → `that's enough`) |
| Retrieval | keyword overlap with the English record | the gloss, plus, with a configured model, English search keywords the model proposes in one short extra request that carries the question only |
| Answer | model wording in English | model wording in Chinese; host headings in Chinese; quotations exactly as in the English sources |

The glossary is a fixed list of about 160 phrases: routing cues aligned with the
contracts, plus common meeting vocabulary. It is all the local extractive mode
has, so there a Chinese question finds evidence only when it uses glossary words
or English terms. With a model, the keyword request covers a meeting's own
vocabulary; measured with `deepseek-flash` it added 1.1 to 1.8 s, and five Chinese
questions on the sample meeting took 2.7 to 4.6 s each and all cited the right
English passages. Only ASCII words from the keyword reply are used, as search
terms; if the request fails, retrieval falls back to the glossary. Its tokens are
not included in the answer's `usage`.

Limits: the glossary is small and hand-written; a word with two meanings is
mapped to one ("比较" alone is left out because it usually means "rather");
`suggest` still groups candidate new skills by the first two English words, which
does nothing for Chinese questions; only Simplified Chinese phrases are listed.

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

**Verification status (updated 2026-11-27): checked against the real endpoint.**
With the owner's approval, tool rounds were run against `deepseek-flash` with thinking
on and off: 8 of 8 questions succeeded and the model's tool calls were served. A
direct probe found the endpoint accepted follow-up requests both with and without
the passback, so the documented HTTP 400 was not reproduced; the passback is
harmless but was not shown to be required. Two consecutive tool rounds were not
exercised. Details: [live record](../results/live-deepseek-validation-2026-11-27.md).
The paragraph below describes the mock tests, which remain the regression check.

`tests/test_deepseek_protocol.py` runs the real
adapter and LiteLLM against a loopback server that imitates the rules above,
including a negative control showing the mock returns 400 when the passback is
missing. `tool_rounds: 0` remains the default because it costs fewer tokens; 1 is
known to work.

## Evaluation

`evaluate` gained an `auto` variant, a routing regression set, a skill-control
suite and a `skill_comparison` table (correctness, citation membership, abstentions,
validated inferences, provider calls, latency, tokens, cost) for `skills`, `direct`
and `auto`. `skills` and `direct` use the same frozen retrieval rows, provider,
model and budget. Three checks were added to the strict release gate.

With the default simulated provider the answers are deterministic sentence
extraction, which ignores the skill contract apart from `catch_up` ordering. That
run can show the controls execute; it cannot show that skills improve answers.

The real-model run (`evaluate --api`, `deepseek-flash`, 2026-11-27) scored 22/22
for fixed skill, `direct` and `auto` alike. The fixed skill cost about 52% more
than `direct` and wrote more inference lines. So no accuracy benefit from skills was
measured, on questions too easy to separate the variants and with substring scoring.
Semantic quality needs harder questions and human review.

## Demonstrations

```powershell
python scripts/skills_demo.py
```

Uses a temporary data directory and the local extractive provider; no key, no
network, and it does not touch `config.json` or `data/`. It shows auto routing, a
cited answer, the correlated trace, and refused calls on both tool surfaces.
