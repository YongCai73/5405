import json
from meeting_copilot.evaluation import evaluate

def test_frozen_runner_controls_and_synthetic_ablations(tmp_path):
    path=tmp_path/'report.json';summary=evaluate(path,tmp_path/'work');r=json.loads(path.read_text())
    assert summary['skills_correct']==r['qa_questions']==22
    assert r['configurations']['skills']['future_leakage_cases']==0
    assert r['configurations']['no_snapshots']['future_leakage_cases']>0
    assert r['configurations']['no_folder']['correct']<22
    assert r['configurations']['direct']['same_retrieval_context_as_skills']
    assert r['configurations']['no_policy']['control_unauthorized_disclosures']==0
    assert r['configurations']['no_policy']['unauthorized_disclosures']==3
    assert r['security']['passed']==r['security']['total']==10
    assert r['native_meet2notes_baseline']['status']=='not measured'

    # Verify correlation in actual evaluation output.
    assert r["run_id"].startswith("run-")

    run_dir = tmp_path / "work" / r["run_id"]
    assert run_dir.is_dir()

    case_ids = {
        case["id"]
        for case in r["configurations"]["skills"]["cases"]
    }

    for filename in ("traces.jsonl", "direct-traces.jsonl"):
        trace_file = run_dir / filename
        assert trace_file.exists()

        rows = [
            json.loads(line)
            for line in trace_file.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]

        correlated = [
            row for row in rows if "case_id" in row
        ]

        # Real workflow must generate correlated events.
        assert correlated, f"No correlated events in {filename}"

        expected_variants = (
            {"direct"}
            if filename == "direct-traces.jsonl"
            else {"skills", "no_snapshots", "no_folder"}
        )

        for row in correlated:
            assert row["run_id"] == r["run_id"]
            assert row["case_id"] in case_ids
            assert row["variant"] in expected_variants


def test_evaluation_trace_correlation(tmp_path):
    import asyncio
    import json
    from meeting_copilot.trace import Trace, evaluation_trace_context

    trace_path = tmp_path / "trace.jsonl"
    trace = Trace(trace_path)

    async def simulated_workflow():
        trace.emit(
            "retrieval",
            snapshot_id="snapshot-1",
            question="PRIVATE-CANARY",
        )

        await asyncio.sleep(0)

        trace.emit(
            "answer",
            latency_ms=100,
            run_id="incorrect-run-id",
        )

    # Run an asynchronous workflow inside an evaluation context.
    with evaluation_trace_context(
        run_id="run-test",
        case_id="q07",
        variant="skills",
    ):
        asyncio.run(simulated_workflow())

    # An unrelated event should not inherit the previous context.
    trace.emit("unrelated_event")

    rows = [
        json.loads(line)
        for line in trace_path.read_text(
            encoding="utf-8"
        ).splitlines()
    ]

    assert len(rows) == 3

    # 1. Both workflow events share the correct identifiers.
    for row in rows[:2]:
        assert row["run_id"] == "run-test"
        assert row["case_id"] == "q07"
        assert row["variant"] == "skills"

    # 2. Context identifiers cannot be overridden.
    assert rows[1]["run_id"] == "run-test"

    # 3. Private question content must not appear in traces.
    assert all(
        "PRIVATE-CANARY" not in json.dumps(row)
        for row in rows
    )

    # 4. Context must not leak into unrelated events.
    for key in ("run_id", "case_id", "variant"):
        assert key not in rows[2]
