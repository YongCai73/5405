from meeting_copilot.diagnostics import diagnose_case


def test_successful_case():
    question = {
        "expected": ["SQLite"],
        "abstain": False,
    }

    result = {
        "text": "The team selected SQLite.",
        "abstained": False,
        "claims": [{"source_id": 1, "quote": "SQLite"}],
    }

    diagnosis = diagnose_case(
        question,
        result,
        citation_supported=True,
        future_evidence_leakage=False,
    )

    assert diagnosis["passed"] is True
    assert diagnosis["failure_codes"] == []


def test_multiple_failures():
    question = {
        "expected": ["SQLite"],
        "abstain": False,
    }

    result = {
        "text": "The team selected PostgreSQL.",
        "abstained": False,
        "claims": [],
    }

    diagnosis = diagnose_case(
        question,
        result,
        citation_supported=False,
        future_evidence_leakage=True,
    )

    assert diagnosis["passed"] is False
    assert set(diagnosis["failure_codes"]) == {
        "temporal_leakage",
        "citation_quote_mismatch",
        "missing_citation",
        "expected_content_mismatch",
    }
    assert diagnosis["failure_count"] == 4