# Private Q&A recovery: storage interface

This records Xiaolong's storage portion of option B, following
[Zimo's draft agreement](CRASH_RECOVERY.md). The intermediate save points, resume
routing and early snapshot ID output are now integrated with the storage contract
on `experiment/eval-integration`. Deterministic C0–C4 fault injection runs
through the real `Assistant` and `Store`; see [EVALUATION_STATUS.md](EVALUATION_STATUS.md).
The original ownership split remains useful context, but the statement that
orchestration is outside this branch is historical.

## Identity and idempotency

- A private answer is identified by `(snapshot, user, conversation, question)`.
  The comparison uses the exact question string. Retrying that identity returns
  the original turn ID and keeps the first committed answer.
- A new snapshot or conversation permits another answer to the same question.
  `skill` is not part of the private answer key; to intentionally generate a
  different answer with another skill, use a new snapshot or conversation.
- Checkpoint identity stays `(snapshot, user, conversation)`, as agreed. It has
  room for one active question. A different question or skill may replace it.
  The orchestration layer must serialize work on the same checkpoint key, or use
  separate conversations/snapshots. This is not a multi-worker task lease.
- A future `request_id` can be added by a separate interface agreement; the
  current integration does not require changing Zimo's `--snapshot` workflow.

## `private_put(user, snapshot, conversation, question, result) -> int`

Both the private answer and its conversation turn are written in one SQLite
transaction. A database unique index prevents duplicate turns even when two
connections save concurrently. A retry returns the original `turn_id` without
changing the original result or creation time.

On opening an older database, the migration keeps the earliest turn in each
duplicate group and makes the private answer agree with that turn. Existing
turn IDs, creation times, and snapshot conversation watermarks are preserved;
IDs are not renumbered. The migration runs transactionally and is repeatable.

## `checkpoint_put(user, snapshot, conversation, state) -> dict`

The accepted state is the draft v1 JSON structure:

```python
state = {
    "version": 1,
    "snapshot": snapshot,
    "question": question,
    "skill": skill,
    "step": "retrieved",
    "source_ids": [row["id"] for row in evidence],
    "evidence": evidence,
}
stored = store.checkpoint_put(user, snapshot, conversation, state)
```

- `step` is `retrieved`, `answered`, or `saved`. For the same question and skill,
  explicit step writes cannot go backwards. A new question or skill may start
  again at `retrieved`.
- `answered` requires the result dictionary returned by the answer node.
- `saved` requires that result and a real `turn_id` belonging to the same user,
  snapshot, conversation, and question. Its result must match the committed
  private answer and turn.
- `updated` is assigned by storage. `version` defaults to 1; other versions are
  rejected.
- The old caller's no-step payload is accepted only when it has a result, and is
  normalized to `answered`. This keeps the existing desktop/CLI working during
  integration; it does not add intermediate recovery to the old orchestrator.

The existing snapshot permission checks run before reading or writing state.
Source IDs must remain in the original snapshot. Stored evidence must carry the
original metadata and exact retained text or a valid excerpt with matching
`text_offset` and `line_offset`. Evidence may grow after authorized model tool
calls, but must stay within the same snapshot. If prior private turns are stored
as `history`, they must match currently authorized, cutoff-pinned history.

## `checkpoint_get(user, snapshot, conversation) -> dict | None`

Reading repeats permission, source, evidence-integrity, and saved-turn checks.
A revoked meeting/folder or a forged checkpoint cannot authorize recovery.
After the model returns, the orchestration layer must still recheck permissions
before committing its answer; `private_put` and `checkpoint_put` do so themselves.

## Completion sequence for Zimo

```python
# The answer node has returned and its result/evidence have been validated.
state.update(step="answered", result=result, evidence=answer_evidence,
             source_ids=[row["id"] for row in answer_evidence])
store.checkpoint_put(user, snapshot, conversation, state)

turn_id = store.private_put(user, snapshot, conversation, question, result)
# A retry must return the first committed answer, not overwrite it.
canonical = store.private_get(user, snapshot, conversation, question)[0]["answer"]
state.update(step="saved", turn_id=turn_id, result=canonical)
store.checkpoint_put(user, snapshot, conversation, state)
return canonical
```

If the process exits after `private_put` commits but before `saved` is recorded,
the checkpoint remains `answered`. Repeating this completion sequence returns
the same turn ID and does not add a conversation record.

## Recovery boundaries and meeting decisions

1. Reuse the original snapshot and stored evidence on resume. Later transcript
   arrivals do not expand the question's evidence scope.
2. Live questions should expose the pinned snapshot ID before work begins. That
   belongs to Zimo's worker/UI/CLI integration.
3. Checkpoints use the existing snapshot deletion cascade. Retention policy is
   coordinated with Cai Yong; this branch adds no independent expiration job.

C1 can skip retrieval only after the `retrieved` checkpoint has committed. A
failure inside retrieval before that commit requires retrieval again. C3 can
skip another model call only after `answered` has committed. If a remote model
has responded but its result has not reached durable storage, the client may
need to call it again. Database idempotency guarantees one saved answer/turn,
not exactly one external model invocation or charge.

These are persistence and provenance checks, not a semantic correctness test of
the model's conclusions. The existing answer/citation validation remains in the
orchestration layer.
