
"""Combine workflow and grounding evaluation reports."""

import argparse
import json
from pathlib import Path


def combine_reports(workflow_report, grounding_report=None):
    """Keep independent evaluation results in one report."""

    if not isinstance(workflow_report, dict):
        raise ValueError("Workflow report must be a JSON object.")

    if (
        "qa_questions" not in workflow_report
        or not isinstance(workflow_report.get("configurations"), dict)
    ):
        raise ValueError("Invalid workflow evaluation report.")

    if grounding_report is None:
        grounding_section = {
            "status": "not_run",
            "note": "Semantic grounding evaluation was not supplied.",
        }
    else:
        if not isinstance(grounding_report, dict):
            raise ValueError("Grounding report must be a JSON object.")

        mode = grounding_report.get("mode")

        if mode not in ("test_stub", "real_llm"):
            raise ValueError("Unknown grounding evaluation mode.")

        if (
            not isinstance(grounding_report.get("metrics"), dict)
            or not isinstance(grounding_report.get("results"), list)
        ):
            raise ValueError("Invalid grounding evaluation report.")

        if mode == "real_llm" and (
            not grounding_report.get("provider")
            or not grounding_report.get("model")
        ):
            raise ValueError("Real LLM report requires provider and model.")

        grounding_section = {
            **grounding_report,
            "status": (
                "real_llm_result"
                if mode == "real_llm"
                else "test_only"
            ),
        }

    return {
        "schema_version": "1.0",
        "report_type": "combined_evaluation",
        "workflow_evaluation": workflow_report,
        "grounding_evaluation": grounding_section,
        "interpretation_notes": [
            "Workflow correctness uses deterministic benchmark checks.",
            "Grounding metrics are independent of workflow metrics.",
            "Test-stub results are not evidence of real model accuracy.",
            "Exact citation matching does not establish semantic support.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Combine independent evaluation reports."
    )

    parser.add_argument("--workflow", required=True)
    parser.add_argument("--grounding")
    parser.add_argument("--output", required=True)

    args = parser.parse_args()

    workflow = json.loads(
        Path(args.workflow).read_text(encoding="utf-8")
    )

    grounding = (
        json.loads(
            Path(args.grounding).read_text(encoding="utf-8")
        )
        if args.grounding
        else None
    )

    report = combine_reports(workflow, grounding)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    print(f"Combined evaluation report: {output}")


if __name__ == "__main__":
    main()
