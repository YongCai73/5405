
import asyncio
from pathlib import Path

from meeting_copilot.grounding_benchmark import (
    calculate_metrics,
    run_grounding_benchmark,
)
from meeting_copilot.grounding_eval import GroundingJudgeError


BENCHMARK_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "grounding_benchmark.json"
)


def test_grounding_metrics():
    results = [
        {
            "gold": "supported",
            "predicted": "supported",
        },
        {
            "gold": "contradicted",
            "predicted": "supported",
        },
        {
            "gold": "insufficient",
            "predicted": None,
        },
    ]

    metrics = calculate_metrics(results)

    assert metrics["total_cases"] == 3
    assert metrics["correct_predictions"] == 1
    assert metrics["judge_errors"] == 1
    assert metrics["accuracy"] == 0.3333
    assert metrics["coverage"] == 0.6667

    assert (
        metrics["confusion_matrix"]["contradicted"]["supported"]
        == 1
    )

    assert (
        metrics["per_label"]["insufficient"]["recall"]
        == 0.0
    )


def test_grounding_benchmark_runner():
    async def fake_judge(*, evidence, claim):
        if "SQLite" in claim:
            return {
                "label": "supported",
                "reason": "Test response.",
            }

        return {
            "label": "insufficient",
            "reason": "Test response.",
        }

    report = asyncio.run(
        run_grounding_benchmark(
            BENCHMARK_PATH,
            judge=fake_judge,
            mode="test_stub",
        )
    )

    assert report["benchmark_version"] == "1.0"
    assert report["mode"] == "test_stub"

    assert report["metrics"]["total_cases"] == 9
    assert report["metrics"]["judge_errors"] == 0
    assert len(report["results"]) == 9

    assert all(
        row["status"] == "ok"
        for row in report["results"]
    )


def test_grounding_benchmark_judge_errors():
    async def failing_judge(*, evidence, claim):
        raise GroundingJudgeError("Invalid JSON")

    report = asyncio.run(
        run_grounding_benchmark(
            BENCHMARK_PATH,
            judge=failing_judge,
            mode="test_stub",
        )
    )

    metrics = report["metrics"]

    assert metrics["total_cases"] == 9
    assert metrics["valid_predictions"] == 0
    assert metrics["judge_errors"] == 9
    assert metrics["accuracy"] == 0.0
    assert metrics["coverage"] == 0.0

    assert all(
        row["status"] == "error"
        for row in report["results"]
    )


def test_grounding_report_serialization_and_privacy():
    import json

    async def fake_judge(*, evidence, claim):
        return {
            "label": "supported",
            "reason": "Synthetic test response.",
        }

    report = asyncio.run(
        run_grounding_benchmark(
            BENCHMARK_PATH,
            judge=fake_judge,
            mode="test_stub",
        )
    )

    # Report must be valid JSON.
    serialized = json.dumps(report)
    restored = json.loads(serialized)

    assert restored["metrics"]["total_cases"] == 9
    assert len(restored["results"]) == 9
    assert restored["mode"] == "test_stub"

    # Only evaluation metadata should be stored.
    for row in restored["results"]:
        assert set(row) == {
            "id",
            "gold",
            "predicted",
            "correct",
            "status",
        }

    # The report should not store the original
    # evidence, claim, prompt, or model explanation.
    assert "evidence" not in serialized
    assert "claim" not in serialized
    assert "Synthetic test response." not in serialized

    # Every successful prediction is represented
    # in the confusion matrix.
    matrix = restored["metrics"]["confusion_matrix"]

    assert sum(
        count
        for predictions in matrix.values()
        for count in predictions.values()
    ) == 9