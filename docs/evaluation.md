# Evaluation & Observability Workstream

## 1. Overview

This workstream develops the Evaluation & Observability framework for Meeting Copilot, focusing on traceability, failure diagnosis, and evidence-grounded answer quality.

The goal is not only to measure whether the system produces correct answers, but also to identify observable failures, link evaluation cases to workflow traces, and assess whether generated claims are supported by meeting evidence.

Development is currently maintained on the `feature/evaluation` branch.

## 2. Completed Features

### 2.1 Unified Evaluation & Trace Correlation

**Status: Implemented and tested**

- Introduced `run_id`, `case_id`, and `variant` identifiers to associate evaluation cases with workflow traces.
- Used Python `ContextVar` to propagate identifiers through asynchronous execution.
- Preserved metadata allowlisting to avoid recording raw questions and evidence text in traces.
- Added automated tests for asynchronous propagation, identifier integrity, context isolation, and trace correlation during actual synthetic evaluation runs.

**Main files:** `trace.py`, `evaluation.py`, `test_evaluation.py`.

### 2.2 Structured Failure Diagnosis

**Status: Implemented and tested**

Introduced a deterministic failure classifier with the following categories:

| Failure code | Description |
|---|---|
| `temporal_leakage` | Evidence originating after the question-time cutoff was used |
| `citation_quote_mismatch` | A cited quotation does not match its source |
| `missing_citation` | A non-abstained response has no citation claims |
| `abstention_mismatch` | Abstention behavior differs from the benchmark expectation |
| `expected_content_mismatch` | The response fails the benchmark's expected-content checks |

The evaluation report now includes case-level diagnoses and variant-level failure summaries.

These categories describe observable test failures, not verified root causes. In particular, expected-content matching is not equivalent to semantic correctness.

**Main files:** `diagnostics.py`, `evaluation.py`, `test_diagnostics.py`.

### 2.3 Semantic Grounding Evaluation

**Status: Framework implemented; real-model benchmarking pending**

Developed a Claim–Evidence evaluation framework to assess whether a generated claim is supported by its cited evidence.

The framework distinguishes three labels:

- **Supported:** Evidence sufficiently establishes the claim.
- **Contradicted:** Evidence explicitly conflicts with the claim.
- **Insufficient:** Evidence does not establish or explicitly contradict the claim.

Implemented components include:

- A nine-case synthetic benchmark with reference labels.
- An LLM-based grounding judge with structured JSON output validation.
- An asynchronous adapter using the existing `Provider.chat()` interface.
- A benchmark runner with explicit model-error handling.
- Accuracy, coverage, per-class precision, recall, F1, macro F1, and confusion matrix calculations.

Invalid model responses are recorded as errors rather than silently treated as valid predictions.

The current automated tests use fake or injected judge responses. They validate implementation behavior but do not demonstrate real LLM semantic-grounding performance.

**Main files:** `grounding_eval.py`, `grounding_benchmark.py`, `tests/fixtures/grounding_benchmark.json`, and associated tests.

### 2.4 Unified Evaluation Report

**Status: Implemented and tested**

Added a report-bundling component that combines the existing workflow evaluation with the independent semantic-grounding evaluation.

The two evaluations remain separate because they measure different properties:

- **Workflow Evaluation:** Synthetic QA behavior, citation matching, temporal consistency, security checks, and system latency.
- **Grounding Evaluation:** Claim–evidence support classification and model prediction quality.

Grounding results are explicitly identified as `not_run`, `test_only`, or `real_llm_result`, depending on the available report and execution mode.

No combined accuracy score is reported across the two evaluation types.

**Main files:** `report_bundle.py`, `test_report_bundle.py`.

## 3. Testing and Verification

The current development milestone has passed **20 relevant automated tests**, covering the evaluation, diagnostics, grounding, benchmark, and report-bundling components.

Tests are run from the `Testing Field` directory using:

`python -m pytest tests/test_report_bundle.py tests/test_grounding_benchmark.py tests/test_grounding_eval.py tests/test_diagnostics.py tests/test_evaluation.py -q`

The existing workflow baseline uses 22 synthetic QA cases. The semantic-grounding benchmark contains nine synthetic claim–evidence cases.

These test results demonstrate that the implemented components function as expected under the tested conditions. They should not be interpreted as real-world model accuracy, transcription quality, or complete production security validation.

## 4. Evaluation Report Usage

The unified report can be generated with:

`python -m meeting_copilot.report_bundle --workflow workflow.json --grounding grounding.json --output combined.json`

The `--grounding` argument is optional. When omitted, the combined report explicitly records that grounding evaluation has not been run.

A separate CLI is available for running the grounding benchmark with a configured real model:

`python -m meeting_copilot.grounding_benchmark --config local-config.json --benchmark tests/fixtures/grounding_benchmark.json --output grounding.json`

Real-model evaluation requires a supported provider, an API key supplied through an environment variable, and explicit permission to send evidence to that provider. API keys and private configuration files must not be committed.

## 5. Current Limitations

- The grounding benchmark contains only nine synthetic cases and is not representative of real meeting conversations.
- Real LLM grounding accuracy has not yet been measured.
- Citation matching verifies quotation membership, not semantic entailment.
- Failure diagnoses identify observed conditions rather than definitive root causes.
- The semantic grounding judge is an evaluation component and has not yet been integrated into the end-user answer review interface.
- End-to-end integration with the latest desktop and ASR branches remains pending.
- Langfuse-based distributed tracing and real-workflow observability dashboards have not yet been integrated.

## 6. Next Steps

1. Run the grounding benchmark with a real, configured LLM and analyze per-class errors.
2. Extend grounding evaluation to realistic meeting decisions, action items, uncertainty, and multi-evidence scenarios.
3. Prototype an evidence-quality review feature that flags potentially unsupported claims for human review.
4. Integrate evaluation and trace correlation with the team's latest desktop and orchestration features.
5. Add Langfuse tracing if it provides useful visibility beyond the existing JSONL logs.
6. Conduct final system-level evaluation and document limitations, ablations, and reproducible results.

## 7. Scope and Team Integration

This workstream is responsible for system-level evaluation methodology, observable failure classification, evidence-quality assessment, and evaluation reporting.

It complements rather than replaces the Security, Memory, Orchestration, MCP, and Desktop workstreams. Component-level security and performance tests remain owned by the relevant contributors, while Evaluation & Observability provides cross-component measurement and diagnostic capabilities.