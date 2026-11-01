"""Opt-in live Gemini function-calling contract test; excluded from normal offline tests."""

from __future__ import annotations

import os

import pytest

from src.agent import GeminiResearchAgent
from src.utils.evidence import Evidence, EvidenceRegistry


@pytest.mark.gemini_integration
def test_real_gemini_manual_function_round_trip() -> None:
    if os.getenv("RUN_GEMINI_INTEGRATION") != "1" or not os.getenv("GEMINI_API_KEY"):
        pytest.skip("Set RUN_GEMINI_INTEGRATION=1 and GEMINI_API_KEY to run the live Gemini contract test")
    registry = EvidenceRegistry()
    registry.add(Evidence("doc_integration", "document", "Integration evidence", "Apple evidence for integration test.", "fixture"))
    calls: list[str] = []

    def search_documents(query: str):
        calls.append(query)
        return {"evidence_id": "doc_integration", "excerpt": "Apple evidence for integration test."}

    agent = GeminiResearchAgent(
        api_key=os.environ["GEMINI_API_KEY"],
        model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
        system_instruction=("Call search_documents exactly once. Then return JSON with one verbatim excerpt claim supported by "
                            "doc_integration, an empty calculation_ids list, and an empty limitations list."),
        registry=registry,
        tool_handlers={"search_documents": search_documents},
    )
    result = agent.run("Retrieve the integration evidence.")

    assert calls
    assert result.claims[0].evidence_ids == ("doc_integration",)
