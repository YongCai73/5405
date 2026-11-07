# Draft module contracts

All fields and schemas are proposals for the kickoff meeting, not implemented APIs.

## TranscriptEvent

```json
{"meeting_id":"meeting-001","segment_id":"seg-001","start_ms":12000,"end_ms":18000,"text":"We rejected option A because of latency.","revision":1,"confirmed":true,"source":"local-asr"}
```

## PrivateQuestion

```json
{"run_id":"run-001","meeting_id":"meeting-001","user_id":"user-001","asked_at_ms":21000,"question":"Why did we reject option A?"}
```

## EvidenceRef

```json
{"meeting_id":"meeting-001","segment_id":"seg-001","available_at_ms":18000,"source_type":"transcript","citation":"00:12–00:18"}
```

## SecurityDecision

```json
{"call_id":"call-001","decision":"allow","reason":"read_only_in_scope","policy_version":"v0.1"}
```

`decision` is one of `allow`, `deny`, `approval_required`.

## RunEvent

```json
{"run_id":"run-001","case_id":"case-001","trace_id":"trace-001","meeting_id":"meeting-001","node_name":"retrieve_evidence","latency_ms":100,"error_type":null}
```

## Contract requirements

- Access control: the host authenticates the user and verifies meeting access before retrieval.
- Time: a question asked at T cannot cite transcript segments only available after T.
- Provenance: each summarized fact must link to source segment/document IDs.
- Privacy: private questions/answers remain keyed by both `meeting_id` and `user_id`.
- Safety: no tool action is authorized merely because a transcript or document says to execute it.
- Idempotency: duplicate transcript events with the same ID/revision must not create duplicate records.
