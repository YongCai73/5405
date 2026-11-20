# Crash recovery for private Q&A — interface agreement (draft v0.1)

Owners: Zimo Li (orchestration, `assistant.py`, CLI) · Xiaolong Lin (memory, `store.py`)
Decision (meeting 2026-11-20): **Option B**. Extend the existing `checkpoints` table and `checkpoint_put` / `checkpoint_get`, which already enforce snapshot scope. LangGraph's built-in checkpointer is not used.
Timeline: interface agreed **11/24**, done and tested **12/1**.

## Problem today

`Assistant.ask()` runs `retrieve → answer`, then calls `checkpoint_put` and `private_put` once at the end.

1. A crash during `retrieve` or the provider call loses all progress; the paid model call is wasted.
2. `private_put` always inserts a new row in `conversation_turns`. A retry after a crash between `checkpoint_put` and `private_put`, or a double submit, creates a **duplicate turn** that later appears in conversation history.
3. The snapshot id is printed only in the final result. After a crash the user does not know which snapshot to resume.

## Crash points covered

| # | Crash happens | Expected after re-running the same `ask` |
|---|---|---|
| C1 | during / after `retrieve`, before the provider call | retrieval is not repeated; the provider is called once |
| C2 | during the provider call (network drop, timeout, Ctrl+C) | provider is called again with the **same** stored evidence |
| C3 | after the answer, before the turn is saved | **no** new provider call; the stored answer is saved once |
| C4 | after everything is saved (user re-submits) | stored answer returned; **no** new turn, **no** provider call |

## Checkpoint state (stored as JSON in `checkpoints.state`)

Key stays `(snapshot, user, conversation)`.

```json
{
  "version": 1,
  "snapshot": "<snapshot id>",
  "question": "<question text>",
  "skill": "trace_decision",
  "step": "retrieved | answered | saved",
  "source_ids": [12, 15],
  "evidence": [ { "...": "excerpt rows exactly as returned by retrieve" } ],
  "result": { "...": "answer result, present from step 'answered' on" },
  "turn_id": 42,
  "updated": 1763600000.0
}
```

| Field | Written at | Notes |
|---|---|---|
| `step` | every step | progresses `retrieved → answered → saved`, never backwards |
| `source_ids` | `retrieved`, updated at `answered` | must stay a subset of the snapshot's refs (already checked by `checkpoint_put`) |
| `evidence` | `retrieved` | ≤ 16 rows / 24 000 chars (existing retrieve limits). On resume, `validate_evidence` re-checks every row against retained content, so a checkpoint cannot inject text |
| `result` | `answered` | the same dict `ask` returns today |
| `turn_id` | `saved` | id of the `conversation_turns` row |

`next: []` from the current code is replaced by `step`.

## Resume rules (Zimo, `assistant.py`)

On `ask(snapshot, question, conversation, skill)`:

1. Call `snapshot_info` first. A revoked snapshot fails here, so **a checkpoint never grants access**.
2. Read `checkpoint_get(user, snapshot, conversation)`.
3. If there is no checkpoint, or its `question`/`skill` differ, start fresh (a new question overwrites the checkpoint).
4. Otherwise, by `step`:
   - `saved` → return `result` (C4)
   - `answered` → save the turn (step 6), return `result` (C3)
   - `retrieved` → skip `retrieve`, run `answer` with the stored `evidence` (C1, C2)
5. After `retrieve`: `checkpoint_put(step='retrieved')`. After `answer`: `checkpoint_put(step='answered')`.
6. Save: `turn_id = private_put(...)`, then `checkpoint_put(step='saved', turn_id=...)`.
7. CLI: print the snapshot id to stderr **before** running, e.g. `{"event":"ask_started","snapshot_id":...}`, so the user can resume with `ask MEETING "same question" --snapshot SID --conversation C`.

## Store changes (Xiaolong, `store.py`)

1. **Idempotent `private_put`**: at most one turn per `(snapshot, user, conversation, question)`.
   - Unique index on those columns, plus `INSERT OR IGNORE`.
   - Return the existing or new `turn_id`.
   - Migration for existing databases: before creating the index, keep the earliest row of any duplicates.
2. `checkpoint_put`: accept the fields above and validate `step` ∈ {retrieved, answered, saved}. Keep the existing scope check.
3. Optional: reject a `step` that goes backwards for the same question.

## Tests (each side writes its own; one shared end-to-end test)

- Zimo: inject an exception after `retrieve` (C1), inside the provider (C2) and between answer and save (C3), then re-run `ask` and assert:
  - the provider call count is as expected
  - the result is the same
  - exactly one turn exists
  - Also C4 (re-submit after success), and a revoked snapshot is denied on resume.
- Xiaolong: `private_put` twice → one row, same `turn_id`; the migration removes existing duplicates; invalid `step` is rejected.
- Shared: real CLI with `--snapshot`, kill the process during the provider call, re-run, check `history`.

## Open questions

1. Should a resumed C2 call reuse the stored evidence, or re-retrieve if new transcript arrived? Proposal: **reuse**. The snapshot is fixed, so the evidence is too.
2. Should the `ask_started` line be added to the `Workers.submit` path (live use) too? Proposal: yes, via the returned `sid`.
3. How long are checkpoints kept? Proposal: deleted with the snapshot (existing `ON DELETE CASCADE`); covered by Cai Yong's retention task.
