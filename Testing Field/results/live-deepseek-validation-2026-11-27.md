# Real DeepSeek endpoint: validation record, 2026-11-27

Branch `jiaxuan/discussion-skills`. These are **real API results**, unlike the
deterministic and mock records beside this file. The raw outputs
are not kept in the repository.

## Setup

| Item | Value |
|---|---|
| Endpoint | `https://api.deepseek.com/v1`, OpenAI-compatible, through LiteLLM |
| Model name sent | `deepseek-flash` |
| Settings | `streaming: true`, `max_tokens: 4096`, `timeout_seconds: 120` |
| Data | the bundled synthetic replay (`examples/replay.jsonl`, `examples/project`) and the 22-question evaluation fixture. No real meeting content was sent. |
| Key handling | The owner approved use of the workspace key file for testing. The key was read into the test process environment only. It was not printed, logged, written to a config or committed; the result files were scanned for it. |
| Estimated spend | about USD 0.09 in total, computed from reported tokens at USD 0.30 / 1.20 per million input / output (the listed peak rates). Not an invoice. |
| When | one evening session, single run of each item. Latency includes network variance. |

## 1. Tool rounds, thinking on and off

Eight real questions, two per configuration.

| `tool_rounds` | `thinking` | Requests succeeded | Runs where the model called tools | Tool calls allowed / denied |
|---|---|---|---|---|
| 1 | default (on) | 2/2 | 1 | 2 / 0 |
| 2 | default (on) | 2/2 | 1 | 2 / 0 |
| 1 | `disabled` | 2/2 | 2 | 4 / 0 |
| 2 | `disabled` | 2/2 | 1 | 2 / 0 |

Every run returned validated quotations; none abstained; no claim or inference was
rejected. When the model used tools it made two calls in one round
(`search_evidence` and `read_evidence`) and then answered. **No run used a second
tool round**, so two consecutive rounds against the live endpoint are still untested.

### Reasoning passback probe

A direct probe sent one tool-calling turn, then the follow-up request twice: once
without `reasoning_content` on the assistant message and once with it. Four runs
each for streaming and non-streaming.

| | Result |
|---|---|
| Runs where the endpoint returned reasoning text | 4 of 8 (both streaming and non-streaming), so the adapter reads it on both paths |
| Follow-up **with** passback accepted | 8 of 8 |
| Follow-up **without** passback accepted | 8 of 8, including the 4 runs that had reasoning text |

So the HTTP 400 that the documentation describes for a missing passback **was not
reproduced** on this endpoint on this date. The adapter's passback is accepted and
does no harm; these runs do not show that it is required. The earlier concern that
kept `tool_rounds` at 0 did not appear in any real request.

## 2. New skills with the real model

Twenty asks.
All succeeded. The stock-phrase check flagged nothing in any answer.

| Skill | What happened |
|---|---|
| `clarify` | First answer explained the bounded queue from three quotations and added two labelled background notes ("bounded queue", "chunk"). The follow-up in the same conversation did not define either term again and added no background. A term the meeting does not define (FTS5) got one background note next to cited explanation. A question with no evidence ("Define raft consensus") returned background only, under the not-from-the-meeting heading, with "Insufficient evidence" stated first. |
| `grill_me` | Two sessions. Each turn returned exactly one question. Follow-up turns critiqued the given answer and went deeper on the same topic; the answer text contained "because" and "compare" and stayed in the session. "that's enough, wrap up" produced a verdict and closed it. |
| `design_review` | Returned quotations as the proposal, then assumptions, failure scenarios and what to validate first, each citing quotations. |
| Saved preference | With "Answer in at most two sentences." saved, a `clarify` answer came back as one sentence. |

Three problems showed up only with the real model and were fixed in this branch, then
re-run against the real model:

1. `grill_me` round 1 returned an "assessment of your answer" before any answer
   existed. The host now discards inferences in round 1 and the prompt says so.
2. `design_review` listed its items in model order with no field names. They are now
   grouped under "Key assumptions", "Failure scenarios", "Validate first".
3. `previously_explained` missed "queue capacity" for the remembered "bounded queue".
   It now matches on shared content words.

Across the first run the host dropped 3 quotations and 7 inferences from model
output that failed validation (not an exact substring, wrong field, or the round 1
case above).

**My reading, not a measurement:** the answers were direct, grounded and free of
filler, and the challenge questions were specific to the recorded evidence. This is
one person's judgement of a handful of answers on a synthetic meeting. It is not the
owner's judgement on his own questions, which is the completion standard.

## 3. `suggest --use-model`

On a 14-turn history
the rules produced three suggestions (save "Keep answers brief.", use `grill_me` with
missing triggers "ask me" and "challenge", candidate skill `who_owns`). The model
added two, both of which cited real turn IDs and passed validation: adjust `grill_me`
routing for "challenge the …" phrasing, citing the thumbs-down note, and a more
specific brevity preference. Nothing was dropped and nothing was enabled. 1,797
input and 2,650 output tokens.

## 4. With and without skills, real model

`evaluate --api`: same model, same budget, `tool_rounds=0`; `skills` and `direct`
answer from the same frozen retrieval rows.
100 of 110 cases called the provider; 10 had no retrieved evidence and abstained
without a request. 252 seconds wall time. The strict gate passed.

| Variant | Correct (expected substrings) | Quote membership | Abstained | Validated inferences | Provider calls | Input / output tokens | Est. cost | Answer p50 / p95 |
|---|---|---|---|---|---|---|---|---|
| fixed skill | 22/22 | 22/22 | 2 | 34 | 20 | 10,501 / 7,596 | $0.0123 | 1.8 s / 8.2 s |
| `direct` (no skill) | 22/22 | 22/22 | 2 | 27 | 20 | 7,940 / 4,743 | $0.0081 | 1.4 s / 2.6 s |
| `auto` | 22/22 | 22/22 | 2 | 34 | 20 | 8,699 / 5,859 | $0.0096 | 1.5 s / 7.1 s |

What this shows: on these 22 synthetic questions the skill contract produced **no
measurable accuracy gain** over plain retrieval; all three scored 22/22. The fixed
skill used about 32% more input tokens, 60% more output tokens and about 52% more
estimated cost than `direct`, and wrote more inference lines. `auto` sat in between
because it sends 16 of the 22 questions to `direct`.

What it does not show: whether the extra inference lines are better or worse
answers. Scoring is expected substrings plus exact-quote membership, the questions
are simple factual lookups that saturate at 22/22, and it is one run. Telling
whether skills help needs harder questions and a person reading the answers.

The ablations behaved as designed with the real model: without snapshots, 3 cases
cited future evidence; without the project folder, 2 cases failed.

## Still not verified

- Two consecutive tool rounds on the live endpoint (the model never needed a second).
- `deepseek-v4-pro`, non-streaming `ask` runs, or any other provider.
- Real meeting content, live ASR transcripts, long conversations, or languages other
  than English.
- Answer quality as judged by the owner on his own questions.
- The desktop apps.
