# Meeting Copilot — Secure, Memory-Augmented Multi-Agent Meeting Assistant

**ENMGT 5405 Final Project | Team planning repository**

A privacy-conscious meeting assistant that combines continuous transcription with private, evidence-grounded Q&A, meeting memory, controlled tools, and reproducible evaluation.

> **Prototype entry (this branch):** [Meeting Copilot 0.2.0 application](Testing%20Field/README.md) · [Chinese B/G/C/T/D/R handoff](Testing%20Field/handoff.md) · [Current proposal/status mapping](Testing%20Field/results/requirements-current.json). Windows source/CLI/public-recording ASR and Linux stability have measured results; real microphones, macOS and paid endpoints still need validation. Use [meeting-copilot/prototype-20261108](https://github.com/cyudaddy/5405/tree/meeting-copilot/prototype-20261108); existing tags are historical milestones.
>
> **Historical planning status:** Proposal / research stage. The planning content below is retained; “no working pipeline” described the repository before the prototype was added.

## Product scenario

During a small in-person technical meeting, a user can privately ask questions such as *“Why did we reject Option A?”* or *“How does this compare with last week's decision?”* while transcription continues uninterrupted. Answers should cite timestamped meeting segments or approved project documents, and should not incorporate meeting content that occurred after the question was asked.

## MVP scope

1. Capture microphone audio on one computer and generate timestamped transcript segments.
2. Keep recording/transcribing while a private question is being answered.
3. Retrieve relevant meeting segments and approved project documents with source citations.
4. Generate a concise meeting summary and export it locally.
5. Enforce meeting/user access boundaries and treat transcript content as untrusted data.
6. Record reproducible traces and test task quality, latency, and privacy/security cases.

**Out of scope for the first version:** Zoom/Teams integration, accurate multi-speaker diarization in noisy rooms, automatic outbound emails, and uncontrolled third-party actions.

## Workstreams

| Workstream | Initial responsibility | Possible technologies (under evaluation) |
|---|---|---|
| Orchestration & Integration | Transcription ingestion, concurrent summary/private Q&A, routing, bounded retries | LangGraph |
| Memory & Context | Timestamped transcript store, meeting-scoped RAG, document retrieval, provenance | SQLite, Chroma |
| MCP & Skills | Read-only project-document tools, skill routing, schema/contracts | MCP, Agent Skills |
| Security & Guardrails | Authorization, private/shared separation, prompt-injection defense, approval policy | Deterministic execution checks; research Invariant/AgentDojo |
| Evaluation & Observability | Replayable tests, trace IDs, baseline comparison, metrics | Langfuse, custom scorers, optional DeepEval |

This original **five-workstream plan** is preserved. The current proposal assignments and developer TODO owners are recorded in the prototype handoff.

## Architecture (proposed)

```text
Microphone -> ASR adapter -> Timestamped transcript events -> Meeting store
                                                    |         |
                                                    v         v
                                           Background summary  Private Q&A request
                                                              |
                                                     Orchestrator (LangGraph)
                                                       /          \
                                          Meeting RAG / Memory   Read-only MCP tools
                                                       \          /
                                                 Evidence + citations
                                                        |
                                               Security and access checks
                                                        |
                                                Private answer to user

All modules -> structured traces -> evaluation runner
```

**Critical security rule:** Meeting speech and retrieved documents are evidence, **not instructions** that authorize tool actions. Authorization must be validated server-side before any tool execution. Private Q&A does not enter shared notes automatically.

## Getting started (planning stage)

- Read [project scope](docs/PROJECT_SCOPE.md).
- Agree on [interfaces](docs/INTERFACES.md) before independent implementation.
- Use [milestones](docs/MILESTONES.md) as a draft timeline; adapt to the professor's deadline.
- Record research and benchmark references in [research notes](docs/RESEARCH.md).

The `src/` directory is intentionally a lightweight placeholder. Add a prototype only after the audio feasibility test and team architecture review.

## Open-source provenance

We are evaluating [Meet2Notes](https://github.com/estebanstifli/Meet2Notes) as a reference or potential base. The original planning repository did not include copied Meet2Notes code. This branch now includes attributed prototype reuse under `Testing Field/`; see its [upstream provenance](Testing%20Field/docs/UPSTREAM_SOURCE.json) and [MIT notice](Testing%20Field/docs/UPSTREAM_LICENSE.txt). Preserve license, notices and attribution, document changes, and distinguish upstream features from original contributions.

## Research/evaluation principles

- Compare a fixed baseline against each enhancement on the same test cases.
- Report legitimate answer accuracy, citation support, private-data leakage, injection success, P50/P95 latency, and audio queue lag.
- Use synthetic or consented recordings; never upload private meeting audio to a third party without explicit consent and disclosure.

## License

No license has been chosen for the team's original code yet. Decide this before making the project public or accepting external contributions. Third-party components retain their respective licenses.
