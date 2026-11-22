# Current evaluation and integration status

**Recorded 2026-11-21 (America/New_York)** on
`experiment/eval-integration` at `b1577d0`.

This page is the current integration record. It does not replace the historical
Windows/Linux audit artifacts linked from the [results index](../results/README.md):
those artifacts remain evidence for their original commit, platform, and time.

## Fresh deterministic gate

The new [integration evaluation report](../results/integration-evaluation-2026-11-21.json)
was generated from the current branch using the local deterministic provider. Its
release gate passed all nine checks:

| Check | Result |
|---|---|
| Frozen skills fixture correctness and citation membership | 22/22 |
| Future-evidence leakage in the guarded skills run | 0 |
| Independent benchmark case conversations | 22/22 |
| Guarded policy cases / intentionally unsafe ablation | 0 / 3 disclosures |
| Defined access-policy suite | 10/10 |
| Deterministic crash-recovery suite C0–C4 | 5/5 |
| Temporal and folder ablation sensitivity | detected |

Run the same gate before an integration merge:

```powershell
python -m meeting_copilot evaluate --output results/local-report.json --stress --strict
```

`--strict` exits nonzero if any listed invariant fails. This is a regression gate
for the frozen synthetic workflow, access-policy and recovery contracts. It is
not evidence of live-meeting behavior, microphone/ASR quality, a paid provider,
or semantic answer quality.

## Integration work after the historical audit

| Commit | Current change | Evidence |
|---|---|---|
| `febd1eb` / `e7d172b` | Merged desktop audio status/low-latency work and replay-safe crash recovery into this isolated integration branch. | Conflict-free merges; prior focused desktop and recovery tests. |
| `7df19d1` | Namespaced evaluator conversations by case, preventing recovery replay cache from joining repeated benchmark questions. | Every report case records `case_isolated: true`. |
| `9e4bb6f` | Added deterministic fault injection for C0–C4 to the workflow report. | Fresh report: 5/5 recovery cases. |
| `e70f762` | Added local-only desktop Session Health persistence/export. | 25 focused desktop Python tests and 7 desktop UI contract tests passed. |
| `b1577d0` | Added the machine-readable release gate and `evaluate --strict`. | Fresh report above; `tests/test_evaluation.py`: 2 passed. |

Session Health stores only bounded, allowlisted numeric capture metrics (for
example dropped segments, speech-to-ready latency, missing audio and input
overflows), plus the effective local preset. It deliberately excludes audio,
transcript text, questions, citations and raw errors. It is returned with a
finished meeting and included in its JSON export.

## Observability decision

There is no Langfuse or external LangSmith tracing integration. Local trace
files, redacted trace export, evaluation reports and Session Health are the
current observability path. External hosting remains an optional future feature,
not a prerequisite for this evaluation gate. Add it only after the team has a
shared-dashboard need and has agreed explicit opt-in, redaction, retention,
access control and credential handling for meeting-derived data.

## Still required before a delivery claim

- Run the full suite from the exact integration commit in the target delivery
  environment and retain its result; the historical 84/85 and 85/85 counts must
  not be presented as results for this newer branch.
- Build and test the macOS desktop bundle, including one consented microphone
  session, input interruption/resume, and the exported Session Health record.
- If an API provider is in scope, run the separately opt-in API and native
  baseline evaluations with the same disclosed provider/model and budget.
- Obtain human review of real meeting usefulness and ASR accuracy; deterministic
  expected-term and exact-quote checks do not establish either property.
