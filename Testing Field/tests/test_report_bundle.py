
import asyncio
import json
from pathlib import Path

import pytest

from meeting_copilot.evaluation import evaluate
from meeting_copilot.grounding_benchmark import (
    run_grounding_benchmark,
)
from meeting_copilot.report_bundle import combine_reports


BENCHMARK_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "grounding_benchmark.json"
)


def test_combined_report_with_both_evaluations(tmp_path):
    workflow_path = tmp_path / "workflow.json"

    evaluate(
        workflow_path,
        tmp_path / "work",
    )

    workflow = json.loads(
        workflow_path.read_text(encoding="utf-8")
    )

    async def fake_judge(*, evidence, claim):
        return {
            "label": "insufficient",
            "reason": "Synthetic test prediction.",
        }

    grounding = asyncio.run(
        run_grounding_benchmark(
            BENCHMARK_PATH,
            judge=fake_judge,
            mode="test_stub",
        )
    )

    combined = combine_reports(workflow, grounding)

    assert combined["schema_version"] == "1.0"

    assert (
        combined["workflow_evaluation"]["qa_questions"]
        == 22
    )

    assert (
        combined["grounding_evaluation"]["metrics"]["total_cases"]
        == 9
    )

    # Never present a fake judge as a real model result.
    assert (
        combined["grounding_evaluation"]["status"]
        == "test_only"
    )

    # The combined report must be JSON serializable.
    serialized = json.dumps(combined)
    assert json.loads(serialized)["report_type"] == (
        "combined_evaluation"
    )


def test_combined_report_without_grounding():
    workflow = {
        "qa_questions": 22,
        "configurations": {"skills": {}},
    }

    combined = combine_reports(workflow)

    assert (
        combined["grounding_evaluation"]["status"]
        == "not_run"
    )


def test_real_llm_report_requires_model_metadata():
    workflow = {
        "qa_questions": 22,
        "configurations": {"skills": {}},
    }

    grounding = {
        "mode": "real_llm",
        "metrics": {"accuracy": 0.8},
        "results": [],
    }

    with pytest.raises(ValueError):
        combine_reports(workflow, grounding)
