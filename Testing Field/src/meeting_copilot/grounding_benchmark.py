
"""Benchmark runner and quality metrics for semantic grounding."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from .config import Config
from .provider import Provider, ProviderError
from .grounding_eval import (
    VALID_LABELS,
    GroundingJudgeError,
    judge_claim_with_provider,
)


LABELS = ("supported", "contradicted", "insufficient")


def calculate_metrics(results):
    """Calculate metrics against reference labels.

    Judge errors count as incorrect in overall accuracy
    and as false negatives for the corresponding gold label.
    """

    total = len(results)
    correct = sum(
        row["predicted"] == row["gold"]
        for row in results
    )

    valid = sum(
        row["predicted"] in VALID_LABELS
        for row in results
    )

    errors = total - valid

    matrix = {
        gold: {pred: 0 for pred in LABELS}
        for gold in LABELS
    }

    for row in results:
        gold = row["gold"]
        predicted = row["predicted"]

        if predicted in VALID_LABELS:
            matrix[gold][predicted] += 1

    per_label = {}

    for label in LABELS:
        tp = matrix[label][label]

        gold_count = sum(
            row["gold"] == label
            for row in results
        )

        predicted_count = sum(
            row["predicted"] == label
            for row in results
        )

        precision = (
            tp / predicted_count
            if predicted_count else 0.0
        )

        recall = (
            tp / gold_count
            if gold_count else 0.0
        )

        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall else 0.0
        )

        per_label[label] = {
            "support": gold_count,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    return {
        "total_cases": total,
        "valid_predictions": valid,
        "judge_errors": errors,
        "correct_predictions": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "coverage": round(valid / total, 4) if total else 0.0,
        "macro_f1": round(
            sum(per_label[label]["f1"] for label in LABELS)
            / len(LABELS),
            4,
        ),
        "per_label": per_label,
        "confusion_matrix": matrix,
    }


async def run_grounding_benchmark(
    benchmark_path,
    *,
    judge,
    mode,
    provider_name=None,
    model_name=None,
):
    """Run the benchmark with an injected async judge."""

    benchmark = json.loads(
        Path(benchmark_path).read_text(encoding="utf-8")
    )

    results = []

    for case in benchmark["cases"]:
        try:
            prediction = await judge(
                evidence=case["evidence"],
                claim=case["claim"],
            )

            label = prediction["label"]

            if label not in VALID_LABELS:
                raise GroundingJudgeError(
                    "Invalid predicted label."
                )

            results.append({
                "id": case["id"],
                "gold": case["label"],
                "predicted": label,
                "correct": label == case["label"],
                "status": "ok",
            })

        except (GroundingJudgeError, ProviderError) as exc:
            results.append({
                "id": case["id"],
                "gold": case["label"],
                "predicted": None,
                "correct": False,
                "status": "error",
                "error_type": type(exc).__name__,
            })

    return {
        "benchmark_version": benchmark["benchmark_version"],
        "mode": mode,
        "provider": provider_name,
        "model": model_name,
        "metrics": calculate_metrics(results),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Run the semantic grounding benchmark."
    )

    parser.add_argument("--config", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--output", required=True)

    args = parser.parse_args()
    config = Config.load(args.config)

    if config.provider == "demo" or not config.send_evidence:
        parser.error(
            "Real grounding evaluation requires a non-demo "
            "provider and send_evidence=true."
        )

    if not os.environ.get(config.api_key_env):
        parser.error(
            "The configured API key environment variable is not set."
        )

    provider = Provider(config)

    async def judge(*, evidence, claim):
        return await judge_claim_with_provider(
            evidence=evidence,
            claim=claim,
            provider=provider,
        )

    report = asyncio.run(
        run_grounding_benchmark(
            args.benchmark,
            judge=judge,
            mode="real_llm",
            provider_name=config.provider,
            model_name=config.model,
        )
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(report["metrics"], indent=2))


if __name__ == "__main__":
    main()
