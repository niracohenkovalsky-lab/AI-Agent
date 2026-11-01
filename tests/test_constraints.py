"""Regression coverage for unavailable data, citations, and recoverable failures."""

import json
from types import SimpleNamespace

import pytest

from src.agent import Claim, GeminiResearchAgent, ResearchResponse, format_response
from src.utils.evidence import Evidence, EvidenceRegistry
from src.utils.planning import format_plan


def agent_with_responses(responses):
    responses = iter(responses)

    def create(**kwargs):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(steps=[], output_text=json.dumps(result))

    return GeminiResearchAgent("test", "test", "test", EvidenceRegistry(), {},
                               client=SimpleNamespace(interactions=SimpleNamespace(create=create)))


def test_missing_information_is_a_valid_answer_without_fabricated_sources():
    agent = agent_with_responses([{"claims": [], "limitations": ["No matching financial data was found."]}])
    rendered = format_response(agent.run("Unavailable metric"), agent.registry)
    assert "not available" in rendered
    assert "No matching financial data" in rendered
    assert "Sources:" not in rendered


@pytest.mark.parametrize("payload", [[], {"claims": "wrong"}, {"claims": [{}]},
    {"claims": [{"text": "unsupported", "evidence_ids": "doc_1"}]},
    {"claims": [], "limitations": []}, {"claims": [], "limitations": [42]}])
def test_malformed_model_responses_are_rejected(payload):
    with pytest.raises(RuntimeError):
        agent_with_responses([payload]).run("Question")


def test_citations_show_section_page_and_exact_anchor():
    registry = EvidenceRegistry()
    registry.add(Evidence("doc_1", "document", "Item 8. Financial Statements", "Data", "note-5",
                          url="https://example.test/10k#note-5", page_number="39"))
    answer = ResearchResponse((Claim("Supported finding", ("doc_1",)),), (), ())
    rendered = format_response(answer, registry)
    assert "Item 8. Financial Statements, note-5, p. 39" in rendered
    assert "https://example.test/10k#note-5" in rendered


@pytest.mark.parametrize("status, retryable", [(429, True), (503, True), (401, False), (400, False)])
def test_provider_status_retry_policy(status, retryable):
    error = RuntimeError("provider failure")
    error.status_code = status
    assert GeminiResearchAgent._is_transient_provider_error(error) is retryable


def test_retries_are_bounded_and_authentication_errors_fail_immediately(monkeypatch):
    monkeypatch.setattr("src.agent.time.sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="after retry"):
        agent_with_responses([TimeoutError()] * 3).run("Question")
    error = RuntimeError("authentication failed")
    error.code = 401
    with pytest.raises(RuntimeError, match="authentication failed"):
        agent_with_responses([error]).run("Question")


def test_tool_failure_can_be_reported_without_inventing_evidence():
    agent = agent_with_responses([])
    agent.tool_handlers["search_web"] = lambda **_: (_ for _ in ()).throw(TimeoutError("secret detail"))
    result = agent._execute_tool("search_web", {"query": "unavailable"})
    assert result == {"ok": False, "error": "tool_failure:search_web:TimeoutError"}


def test_plan_describes_calculations_and_external_sources_when_relevant():
    plan = format_plan("Compare Apple and Microsoft gross margin percentages")
    assert plan.startswith("To answer this, I will:\n1.")
    assert "external sources" in plan
    assert "Calculate" in plan
    assert "citations" in plan
