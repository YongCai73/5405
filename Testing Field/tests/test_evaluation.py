import json
from meeting_copilot.evaluation import evaluate


def _api_report(provider, model, fixture='frozen-fixture', multiplier=1):
    variants = {}
    for name in ('skills', 'direct', 'auto'):
        variants[name] = {
            'total': 22, 'correct': 21 + (name == 'auto'), 'citation_supported': 22,
            'abstained': 2, 'provider_calls': 20 * multiplier, 'p50_ms': 100 * multiplier,
            'p95_ms': 200 * multiplier, 'input_tokens': 1000 * multiplier,
            'output_tokens': 500 * multiplier, 'estimated_cost_usd': 0.0123 * multiplier,
        }
    return {
        'mode': 'API evaluation / synthetic transcript fixtures; expected-term and exact-quote checks are not semantic quality assessment',
        'provider': provider, 'model': model, 'tool_rounds': 0, 'fixture_sha256': fixture,
        'created_at_utc': '2026-11-28T00:00:00Z', 'skill_comparison': {'variants': variants},
    }

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
    assert r['recovery']['passed']==r['recovery']['total']==5
    assert summary['release_gate_passed'] is True
    assert r['release_gate']['passed'] is True
    assert {check['id'] for check in r['release_gate']['checks']} == {
        'skills_fixture_correctness', 'skills_citation_membership', 'skills_no_future_evidence',
        'case_isolation', 'policy_guard_and_ablation_sensitivity', 'security_suite', 'recovery_suite',
        'temporal_ablation_sensitivity', 'folder_ablation_sensitivity',
        'auto_routing_fixture', 'routing_suite', 'skill_controls',
    }
    assert summary['auto_correct'] == 22 and r['routing']['passed'] == r['routing']['total'] == 36
    assert r['skill_controls']['passed'] == r['skill_controls']['total'] == 17
    auto = r['configurations']['auto']
    assert auto['routing']['status_counts'] == {'matched': 6, 'not_applicable': 16}
    assert {c['skill'] for c in auto['cases']} == {'catch_up', 'compare_options', 'direct', 'trace_decision'}
    comparison = r['skill_comparison']
    assert set(comparison['variants']) == {'skills', 'direct', 'auto'} and 'SIMULATED' in comparison['interpretation']
    assert all(v['provider_calls'] == 0 and v['estimated_cost_usd'] == 0.0 for v in comparison['variants'].values())
    assert 'not a live-meeting' in r['release_gate']['scope']
    assert {case['id'] for case in r['recovery']['cases']} == {'C0','C1','C2','C3','C4'}
    assert all(case['turns'] == 1 for case in r['recovery']['cases'])
    assert r['native_meet2notes_baseline']['status']=='not measured'
    manifest = r['reproducibility']
    assert manifest['configured_provider'] == 'demo'
    assert manifest['evaluation_overrides'] == {
        'tool_rounds': 0, 'research_retries': 0, 'general_answers': False,
        'transcript_context': False, 'temperature': 0,
    }
    assert 'api_key' not in json.dumps(manifest).lower()

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
            else {"skills", "auto", "no_snapshots", "no_folder"}
        )

        for row in correlated:
            assert row["run_id"] == r["run_id"]
            assert row["case_id"] in case_ids
            assert row["variant"] in expected_variants

    # Verify diagnosis is included in actual evaluation reports.
    for variant in ("skills", "direct", "auto", "no_snapshots", "no_folder"):
        run = r["configurations"][variant]

        assert "diagnosis_summary" in run

        summary = run["diagnosis_summary"]
        cases = run["cases"]

        assert summary["cases_with_failures"] == sum(
            not case["diagnosis"]["passed"]
            for case in cases
        )

        for case in cases:
            assert case['case_isolated'] is True
            diagnosis = case["diagnosis"]

            assert diagnosis["failure_count"] == len(
                diagnosis["failure_codes"]
            )
            assert diagnosis["passed"] == (
                    diagnosis["failure_count"] == 0
            )

    # Temporal leakage findings must be reflected in diagnosis.
    no_snapshots = r["configurations"]["no_snapshots"]

    diagnosed_leakage = sum(
        "temporal_leakage" in case["diagnosis"]["failure_codes"]
        for case in no_snapshots["cases"]
    )

    assert diagnosed_leakage == no_snapshots["future_leakage_cases"]
    assert diagnosed_leakage > 0


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


def test_model_comparison_requires_compatible_api_reports(tmp_path):
    from meeting_copilot.cli import parser
    from meeting_copilot.model_report import compare_evaluations

    deepseek = tmp_path / 'deepseek.json'
    anthropic = tmp_path / 'anthropic.json'
    deepseek.write_text(json.dumps(_api_report('openai', 'deepseek-flash')), encoding='utf-8')
    anthropic.write_text(json.dumps(_api_report('anthropic', 'claude-test', multiplier=2)), encoding='utf-8')
    output = tmp_path / 'comparison.md'

    result = compare_evaluations([deepseek, anthropic], output)

    text = output.read_text(encoding='utf-8')
    assert result['reports'] == ['deepseek.json', 'anthropic.json']
    assert 'openai / deepseek-flash' in text and 'anthropic / claude-test' in text
    assert 'Fixture SHA-256: `frozen-fixture`' in text
    assert '$0.0123' in text and '200 / 400 ms' in text
    args = parser().parse_args(['compare-evaluations', str(deepseek), str(anthropic), '--output', str(output)])
    assert args.command == 'compare-evaluations' and args.reports == [str(deepseek), str(anthropic)]

    anthropic.write_text(json.dumps(_api_report('anthropic', 'claude-test', fixture='other')), encoding='utf-8')
    try:
        compare_evaluations([deepseek, anthropic], output)
    except ValueError as exc:
        assert 'different fixtures' in str(exc)
    else:
        raise AssertionError('expected incompatible reports to be rejected')
