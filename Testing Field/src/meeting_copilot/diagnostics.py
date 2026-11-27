"""Deterministic failure classification for evaluation cases."""


def diagnose_case(
    question,
    result,
    *,
    citation_supported,
    future_evidence_leakage,
):
    """Classify observable evaluation failures.

    These checks use synthetic benchmark expectations and
    exact citation matching. They do not establish semantic
    correctness or determine the root cause of a failure.
    """
    failures = []

    if future_evidence_leakage:
        failures.append("temporal_leakage")

    if not citation_supported:
        failures.append("citation_quote_mismatch")

    if result["abstained"] != question.get("abstain", False):
        failures.append("abstention_mismatch")

    if not result["abstained"] and not result.get("claims"):
        failures.append("missing_citation")

    expected_terms = question.get("expected", [])
    answer_text = result.get("text", "").lower()

    if any(
        term.lower() not in answer_text
        for term in expected_terms
    ):
        failures.append("expected_content_mismatch")

    return {
        "passed": len(failures) == 0,
        "failure_codes": failures,
        "failure_count": len(failures),
    }