# Discussion skills and meta-suggestion

Owner: Jiaxuan Yan. Branch `jiaxuan/discussion-skills`, stacked on
`jiaxuan/mcp-skills` (see [MCP_SKILLS.md](MCP_SKILLS.md) for routing, contracts, the
tool gate and trace, which this builds on). Recorded 2026-11-27. Validation:
[results/discussion-skills-validation-2026-11-27.md](../results/discussion-skills-validation-2026-11-27.md).

Two requests from use: more skills for technical discussion, and a way for a user
to find out how the copilot should be changed to fit how they ask.

## What a skill prompt alone could not fix

Two limits sat below the skill layer, so they were changed in the runtime, not in
prompt wording.

| Wanted | Underlying limit | What was added |
|---|---|---|
| "Do not explain a term again" | The model had no record of what it had explained | **Term memory.** A skill with `"memory": "explained_terms"` stores the terms each answer explained in the private turn. Later turns of the same user and conversation read them back and pass them to the model as already explained. |
| "Answer wider questions" | Every sentence had to be a quotation or cite one, so a term the meeting never defined could not be explained | **Labelled background.** A skill with `"general_knowledge": "labelled"` may return up to three short background notes. The host shows them under a heading that says they are model knowledge, not from the meeting, and never mixes them with quotations. Only `clarify` has this permission. |
| "Ask me one question, then follow up" | One `ask` produced one answer; nothing carried a round number | **Session state.** A skill with a `"session"` block gets a round counter, the topic and the previous challenge from the last stored turn. Auto routing stays in an open session. |

Retrieval scope is unchanged: the pinned snapshot, opted-in earlier meetings and
approved folders. A wider question still cannot reach evidence the user did not
authorize.

## The three skills

| | `clarify` | `grill_me` | `design_review` |
|---|---|---|---|
| For | A point you did not follow | Testing your own reasoning | Finding what to check in a proposal |
| Example | "Explain FTS5 in plain language" | "Grill me on the SQLite decision" | "What could go wrong with the snapshot design?" |
| Turns | one | up to 5 rounds | one |
| Quotations go to | `related_evidence` | `challenged_evidence` | `proposal_evidence` |
| Model wording goes to | `explanation` | `assessment`, plus one `question` | `key_assumptions`, `failure_scenarios`, `validate_first` |
| General background | yes, labelled | no | no |
| Heading over model wording | "Explanation (model wording…)" | "Assessment of your answer (model critique, not a meeting fact)" | "Model suggestions (not meeting facts…)" |

**"No AI feel" as rules.** Each contract has a `style` list that is sent to the
model: answer in the first sentence; no greeting, praise or restating the question;
no closing offer or summary line; plain words; one small example over an abstract
description; prose unless a list is asked for; a term already explained gets at most
one short clause. The host also reports stock phrases it finds in the model's
wording (`validation_report.style_flags`, for example "great question", "I hope this
helps"). It reports them; it does not rewrite the answer.

**`grill_me` rounds.**

1. First message: the model picks the least justified point in the evidence and
   returns one question. The host keeps only the text up to the first question
   mark, so a list of questions becomes one.
   Nothing is assessed in round 1; inferences the model volunteers there are dropped.
2. Each later message is treated as the answer. The model critiques it in
   `assessment` and asks one follow-up. Retrieval keeps using the session topic.
3. The session closes after round 5, when the message contains an exit phrase
   ("stop", "wrap up", "that's enough"…), or when the model returns no valid
   question. A sixth question from the model is discarded.
4. While a session is open, `--skill auto` stays in it even if the answer contains
   words such as "because" or "compare". Naming another `--skill` leaves it.
5. State is the user's own stored private turns for that conversation. Another
   conversation, another user, or a process restart changes nothing.
6. With no evidence about the topic the skill abstains and no session starts.

## Feedback and wording preferences

```powershell
python -m meeting_copilot feedback SNAPSHOT_ID "the question" --rating down --note "too long" --conversation mine
python -m meeting_copilot prefs add "Skip basic definitions unless I ask."
python -m meeting_copilot prefs list
```

Feedback is stored per user and answer, and is read only by `suggest`. Preferences
(at most five single lines) are sent with API questions as style rules. The prompt
states they change wording only, never scope or permissions; they are not used for
routing or tool access. The local extractive mode ignores them.

## Meta-suggestion

```powershell
python -m meeting_copilot suggest MEETING_ID
python -m meeting_copilot suggest MEETING_ID --use-model --output data/suggestions.json
```

Run only when the user asks. It reads that user's own private questions and ratings
for the meeting (plus `--previous` meetings they opt in) and returns:

observed repeated need → what it rests on (turn IDs) → one of *save a preference*,
*use or adjust an existing skill*, *candidate new skill* → for a new skill, a draft
contract and test cases a developer or coding agent can implement.

| Rule | Becomes | Example |
|---|---|---|
| The same wording request in ≥2 questions | `preference` | "briefly" twice → `prefs add "Keep answers brief."` |
| ≥2 unmatched questions that read like an existing skill | `use_existing_skill`, with the trigger words that skill lacks | "ask me hard questions…" → `grill_me`, missing trigger "ask me" |
| ≥2 thumbs-down, or half of ≥3 answers abstaining, for one skill | `adjust_skill`, with abstain reasons and notes | |
| ≥3 unmatched questions with the same opening words | `new_skill` candidate with a draft contract | "who owns …" ×3 → `who_owns` |
| nothing repeats | `no_change` | one bad answer is not a missing skill |

With `--use-model` the same data (question texts and metadata, not answers or
evidence) is also sent to the configured provider. A model suggestion is kept only
if it cites at least `--min-support` real turn IDs, names an existing skill where it
needs one, and, for a new skill, uses only registered read-only tools and yields a
contract that passes validation. The report counts what was dropped and why.

Boundaries:

- **Nothing is enabled.** The report is text. A proposed skill is a draft; it is not
  written into `skills/` and not loaded. Preferences are saved only by `prefs add`.
- **Per user.** History is read with the host-bound user and re-checked against
  current access, so a revoked meeting drops out and another user's questions are
  never visible.
- **Through the gate.** History is read with two tools, `list_private_turns` and
  `list_feedback`, through the same gateway as other tool calls. Only the
  `meta_suggest` contract allows them. Every other session is refused, including an
  MCP session started without `--skill`. `meta_suggest` in turn cannot read
  evidence. An external MCP client can use them only with
  `mcp SNAPSHOT --skill meta_suggest`.
- **Minimal data.** The tools return question text, routing metadata and ratings.
  They do not return answer text or quotations.
- The `suggest` flow calls the gateway in process. It is not an MCP client session;
  the stdio path for the two tools is covered by a separate test.

Limits: the rules match English phrases in question text, and "same opening words"
is a crude grouping. A suggestion shows that a pattern repeats. It does not show the
change would help; the user decides.

## Not verified

The automated tests use a scripted provider (fixed replies fed through the real
prompt assembly, validation and storage) or the local extractive mode.

On 2026-11-27 the skills and `suggest --use-model` were also run against the real
`deepseek-flash` model on the synthetic sample meeting; see the
[live record](../results/live-deepseek-validation-2026-11-27.md). All calls
succeeded, the model kept to one question per `grill_me` turn, did not redefine
remembered terms, and tripped no stock-phrase flag. Three defects seen only in real
output were fixed (an "assessment" in `grill_me` round 1, ungrouped `design_review`
output, too-strict term matching).

Still unknown: whether these skills are better on the owner's real meetings and
questions, which is the completion standard and needs his judgement; behaviour on
long conversations and noisy ASR transcripts; and anything in a language other than
English.
