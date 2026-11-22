# Crash recovery for private Q&A — contract and integration status (v0.2)

Owners: Zimo Li (orchestration, `assistant.py`, CLI) · Xiaolong Lin (memory, `store.py`)
Decision (meeting 2026-11-20): **Option B**. Extend the existing `checkpoints` table and `checkpoint_put` / `checkpoint_get`, which already enforce snapshot scope. LangGraph's built-in checkpointer is not used.
Integration status (2026-11-21): the replay-safe storage and orchestration path are merged into `experiment/eval-integration`. The deterministic evaluator executes C0–C4 through the real `Assistant` and `Store` state machine; see [EVALUATION_STATUS.md](EVALUATION_STATUS.md). A real process kill and a paid-provider retry remain separate target-environment validation work.

## Failure modes and current behavior

Without the checkpoint sequence below, `Assistant.ask()` would run `retrieve → answer` and save only at the end. The integrated path writes `retrieved`, `answered`, then `saved` checkpoints around the same guarded storage operations. Its boundaries are:

1. A crash during `retrieve` **before** the `retrieved` checkpoint (C0) has no durable progress and starts retrieval again. After that checkpoint, a provider failure (C2) resumes with the same validated evidence, though a provider invocation may still need to be repeated.
2. `private_put` is idempotent for `(snapshot, user, conversation, question)`, so C3 and a duplicate submission cannot add a second conversation turn. This does not guarantee exactly one external provider charge before the answer reaches durable storage.
3. The CLI writes the pinned snapshot ID before running `ask`, so a caller can reuse it for the same conversation after a failure.

## Crash points covered

| # | Crash happens | Expected after re-running the same `ask` |
|---|---|---|
| C1 | after the `retrieved` checkpoint has been **written**, before the provider call | retrieval is not repeated; the provider is called once |
| C0 | during `retrieve`, before the `retrieved` checkpoint is written | no usable progress: retrieval runs again from the start |
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

## Tests and current evidence

The integration evaluator deterministically injects the five boundaries below
through the real `Assistant` and `Store`, and requires exactly one final turn for
each case. The fresh current report records C0–C4 as 5/5 passing. It is not a
process-kill or paid-provider test.

The following original ownership checklist remains useful for target-platform
validation:

- Zimo: inject an exception during `retrieve` (C0), after the `retrieved` checkpoint (C1), inside the provider (C2) and between answer and save (C3), then re-run `ask` and assert:
  - the provider call count is as expected
  - the result is the same
  - exactly one turn exists
  - Also C4 (re-submit after success), and a revoked snapshot is denied on resume.
- Xiaolong: `private_put` twice → one row, same `turn_id`; the migration removes existing duplicates; invalid `step` is rejected.
- Shared: real CLI with `--snapshot`, kill the process during the provider call, re-run, check `history`.

## Decisions (meeting 2026-11-20, Zimo & Xiaolong)

1. **A resumed call reuses the stored evidence** and does not re-retrieve. The snapshot pins what may be used, so the evidence is the same. To include newer transcript, ask again, which creates a new snapshot.
2. **Live questions also expose the snapshot id up front.** The `Workers.submit` path returns `sid` before the answer runs, so the user can resume with `--snapshot`.
3. **Retention: checkpoints are deleted with their snapshot / meeting** (existing `ON DELETE CASCADE`). No separate checkpoint retention period; retention is set by Cai Yong's retention policy.
4. **Revocation**: after access to a snapshot, meeting or folder is revoked, its checkpoint can no longer be read or resumed. `snapshot_info` runs first, and `validate_evidence` re-checks evidence.
5. **C1 boundary**: resume skips retrieval only if the `retrieved` checkpoint was written successfully. A crash before that (C0) restarts retrieval.
