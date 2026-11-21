
import json
from collections import Counter
from pathlib import Path


BENCHMARK_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "grounding_benchmark.json"
)


def test_grounding_benchmark_structure():
    data = json.loads(
        BENCHMARK_PATH.read_text(encoding="utf-8")
    )

    assert data["benchmark_version"] == "1.0"

    cases = data["cases"]
    assert len(cases) == 9

    labels = {
        "supported",
        "contradicted",
        "insufficient",
    }

    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))

    for case in cases:
        assert set(case) == {
            "id",
            "evidence",
            "claim",
            "label",
        }

        assert case["evidence"].strip()
        assert case["claim"].strip()
        assert case["label"] in labels

    counts = Counter(case["label"] for case in cases)

    assert counts == {
        "supported": 3,
        "contradicted": 3,
        "insufficient": 3,
    }



import pytest

from meeting_copilot.grounding_eval import (
    judge_claim,
    build_grounding_prompt,
    GroundingJudgeError,
)


def test_grounding_judge_supported():
    def fake_generate(prompt):
        assert "SQLite" in prompt
        return json.dumps({
            "label": "supported",
            "reason": "The evidence confirms the decision.",
        })

    result = judge_claim(
        evidence="The team decided to use SQLite.",
        claim="SQLite was selected.",
        generate=fake_generate,
    )

    assert result["label"] == "supported"
    assert result["reason"]


def test_grounding_judge_insufficient():
    def fake_generate(prompt):
        return json.dumps({
            "label": "insufficient",
            "reason": "The decision was not established.",
        })

    result = judge_claim(
        evidence="The team discussed AWS.",
        claim="The team selected AWS.",
        generate=fake_generate,
    )

    assert result["label"] == "insufficient"


def test_grounding_judge_invalid_json():
    with pytest.raises(GroundingJudgeError):
        judge_claim(
            evidence="The team selected SQLite.",
            claim="SQLite was selected.",
            generate=lambda prompt: "invalid response",
        )


def test_grounding_judge_invalid_label():
    with pytest.raises(GroundingJudgeError):
        judge_claim(
            evidence="The team selected SQLite.",
            claim="SQLite was selected.",
            generate=lambda prompt: json.dumps({
                "label": "maybe",
                "reason": "Uncertain",
            }),
        )


def test_grounding_judge_missing_reason():
    with pytest.raises(GroundingJudgeError):
        judge_claim(
            evidence="The team selected SQLite.",
            claim="SQLite was selected.",
            generate=lambda prompt: json.dumps({
                "label": "supported",
                "reason": "",
            }),
        )


def test_grounding_prompt_rejects_empty_evidence():
    with pytest.raises(ValueError):
        build_grounding_prompt(
            evidence="",
            claim="SQLite was selected.",
        )


import asyncio
from types import SimpleNamespace

from meeting_copilot.grounding_eval import (
    judge_claim_with_provider,
)


def test_grounding_provider_adapter():
    class FakeProvider:
        def __init__(self):
            self.messages = None

        async def chat(self, messages, schemas=()):
            self.messages = messages
            return SimpleNamespace(
                content=json.dumps({
                    "label": "supported",
                    "reason": "Evidence confirms the decision.",
                })
            )

    provider = FakeProvider()

    result = asyncio.run(
        judge_claim_with_provider(
            evidence="The team decided to use SQLite.",
            claim="SQLite was selected.",
            provider=provider,
        )
    )

    assert result["label"] == "supported"
    assert result["reason"]

    assert provider.messages[0]["role"] == "system"
    assert provider.messages[1]["role"] == "user"
    assert "SQLite" in provider.messages[1]["content"]


def test_grounding_provider_invalid_response():
    class FakeProvider:
        async def chat(self, messages, schemas=()):
            return SimpleNamespace(
                content="This is not valid JSON."
            )

    with pytest.raises(GroundingJudgeError):
        asyncio.run(
            judge_claim_with_provider(
                evidence="We discussed SQLite.",
                claim="SQLite was selected.",
                provider=FakeProvider(),
            )
        )
