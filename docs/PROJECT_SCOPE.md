# Project scope and elevator pitch

## Elevator pitch

We propose a secure, memory-augmented meeting copilot that continuously transcribes a small in-person meeting while allowing participants to privately ask evidence-grounded questions about the discussion and approved project documents. The final product will be a locally runnable prototype, a replayable test set, and an evaluation report comparing reliability, retrieval quality, privacy, latency, and cost.

## What makes this different from a generic note taker?

- Truly concurrent ingestion and private Q&A (no blocking audio collection).
- Time-correct evidence: at query time T, use only segments available by T.
- Meeting-scoped and user-scoped access control; private questions never become shared evidence by default.
- Tool-use safety against instruction injection in spoken/transcribed content.
- Reproducible baseline and ablation experiments.

## First demo acceptance flow

1. Start a local meeting recording with participant consent.
2. Show timestamped transcript segments arriving.
3. Ask a private question, receive an answer citing segments already recorded.
4. Verify new transcript segments arrive while the question is processed.
5. Show a malicious spoken instruction is treated as transcript data, not a command.
6. Export meeting summary and show the event trace.

## Stop/go feasibility gate

Test 10–15 minutes of continuous local ASR on the target laptop. Check important technical terminology, negations, queue lag and a concurrent Q&A request. If lag grows or recognition is unusable, narrow to quiet conditions or recorded-audio replay, without changing the rest of the architecture.
