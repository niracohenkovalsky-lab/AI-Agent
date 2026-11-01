import json
from types import SimpleNamespace

import pytest

from src.agent import GeminiResearchAgent, format_response
from src.utils.evidence import Evidence, EvidenceRegistry


def _call(name, arguments, call_id):
    return SimpleNamespace(type="function_call", name=name, arguments=arguments, id=call_id)


def _response(steps, text="", interaction_id="interaction-1"):
    return SimpleNamespace(steps=steps, output_text=text, id=interaction_id)


class FakeInteractions:
    def __init__(self, responses):
        self.responses, self.requests = iter(responses), []

    def create(self, **kwargs):
        self.requests.append({**kwargs, "input": list(kwargs["input"])})
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses):
        self.interactions = FakeInteractions(responses)


def _registry():
    registry = EvidenceRegistry()
    registry.add(Evidence("doc_1", "document", "Item 1A", "Risk factor evidence", "item-1a"))
    registry.add(Evidence("doc_2", "document", "Financial Statements", "R&D evidence", "item-8"))
    return registry


def _agent(responses, handlers=None):
    return GeminiResearchAgent("test", "test-model", "Return JSON.", _registry(), handlers or {}, client=FakeClient(responses))


def test_manual_loop_preserves_multiple_call_ids_and_returns_structured_response():
    final = json.dumps({"claims": [{"text": "Risk", "evidence_ids": ["doc_1"]}, {"text": "R&D", "evidence_ids": ["doc_2"]}], "calculation_ids": [], "limitations": []})
    agent = _agent([_response([_call("search_documents", {"query": "risks"}, "call-risk"), _call("search_documents", {"query": "R&D"}, "call-rd")]), _response([], final)], {"search_documents": lambda query: {"query": query}})
    result = agent.run("Test question")
    assert len(result.claims) == 2
    outputs = agent.client.interactions.requests[1]["input"]
    assert [item["call_id"] for item in outputs] == ["call-risk", "call-rd"]
    assert "[doc_1]" in format_response(result, agent.registry)


def test_final_response_rejects_unknown_citations():
    agent = _agent([_response([], json.dumps({"claims": [{"text": "Unsupported", "evidence_ids": ["not-real"]}]}))])
    with pytest.raises(Exception, match="Unknown evidence"):
        agent.run("Test question")


def test_repeated_tool_call_is_a_safe_tool_error():
    final = json.dumps({"claims": [{"text": "Risk", "evidence_ids": ["doc_1"]}], "calculation_ids": [], "limitations": ["Stopped."]})
    agent = _agent([_response([_call("search_documents", {"query": "risks"}, "one")]), _response([_call("search_documents", {"query": "risks"}, "two")]), _response([], final)], {"search_documents": lambda query: {"query": query}})
    agent.run("Test")
    result = agent.client.interactions.requests[2]["input"][0]["result"][0]["text"]
    assert json.loads(result)["error"] == "repeated_unproductive_tool_call"


def test_risk_query_uses_document_and_calculator_not_web():
    calls = []
    final = json.dumps({"claims": [{"text": "Risk factor evidence", "evidence_ids": ["doc_1"]}], "calculation_ids": [], "limitations": []})
    agent = _agent([_response([_call("search_documents", {"query": "risks"}, "risk"), _call("search_documents", {"query": "R&D"}, "rd")]), _response([_call("calculate_financial_metric", {"operation": "percentage"}, "calc")]), _response([], final)], {"search_documents": lambda query: calls.append("document") or {}, "search_web": lambda query: calls.append("web") or {}, "calculate_financial_metric": lambda operation: calls.append("calculate") or {}})
    agent.run("What are Apple's top 3 risk factors and R&D percentage?")
    assert calls == ["document", "document", "calculate"]


def test_margin_query_combines_document_and_web():
    calls = []
    final = json.dumps({"claims": [{"text": "Risk factor evidence", "evidence_ids": ["doc_1"]}], "calculation_ids": [], "limitations": []})
    agent = _agent([_response([_call("search_documents", {"query": "Apple margin"}, "apple"), _call("search_web", {"query": "Microsoft margin"}, "msft")]), _response([], final)], {"search_documents": lambda query: calls.append("document") or {}, "search_web": lambda query: calls.append("web") or {}})
    agent.run("Compare Apple and Microsoft gross margins")
    assert calls == ["document", "web"]


def test_agent_retries_a_transient_failure(monkeypatch):
    final = json.dumps({"claims": [{"text": "Risk", "evidence_ids": ["doc_1"]}], "calculation_ids": [], "limitations": []})
    agent = _agent([TimeoutError("temporary"), _response([], final)])
    monkeypatch.setattr("src.agent.time.sleep", lambda _: None)
    assert agent.run("Test").claims[0].evidence_ids == ("doc_1",)
    assert len(agent.client.interactions.requests) == 2


def test_gemini_request_uses_provider_supported_parameters():
    final = json.dumps({"claims": [{"text": "Risk", "evidence_ids": ["doc_1"]}], "calculation_ids": [], "limitations": []})
    agent = _agent([_response([], final)])

    agent.run("Test")

    assert "temperature" not in agent.client.interactions.requests[0]


def test_temporary_provider_error_remains_retryable():
    assert GeminiResearchAgent._is_transient_provider_error(TimeoutError("temporary"))


def test_last_tool_results_are_sent_for_a_final_answer_at_round_limit():
    final = json.dumps({"claims": [{"text": "Risk factor evidence", "evidence_ids": ["doc_1"]}],
                        "calculation_ids": [], "limitations": ["Microsoft metric unavailable."]})
    agent = _agent([_response([_call("search_documents", {"query": "Apple margin"}, "last-call")]),
                    _response([], final)], {"search_documents": lambda query: {"evidence_id": "doc_1"}})
    agent.max_rounds = 1

    result = agent.run("Compare Apple and Microsoft gross margins")

    request = agent.client.interactions.requests[-1]
    assert request["input"][0]["call_id"] == "last-call"
    assert request["generation_config"]["tool_choice"] == "none"
    assert request["previous_interaction_id"] == "interaction-1"
    assert result.claims[0].evidence_ids == ("doc_1",)
    assert result.limitations == ("Microsoft metric unavailable.",)


def test_final_answer_at_round_limit_still_rejects_unknown_citations():
    final = json.dumps({"claims": [{"text": "Unsupported margin", "evidence_ids": ["not-real"]}]})
    agent = _agent([_response([_call("search_web", {"query": "Microsoft margin"}, "last-call")]),
                    _response([], final)], {"search_web": lambda query: []})
    agent.max_rounds = 1

    with pytest.raises(Exception, match="Unknown evidence"):
        agent.run("Compare Apple and Microsoft gross margins")


def test_strict_calculator_schema_allows_unused_references_to_be_null():
    calculator = next(tool for tool in GeminiResearchAgent._function_declarations() if tool["name"] == "calculate_financial_metric")
    parameters = calculator["parameters"]
    assert parameters["required"] == ["operation"]
    assert parameters["properties"]["numerator_fact_id"]["type"] == "string"


def test_default_budget_allows_more_than_six_research_rounds():
    responses = [_response([_call('search_documents', {'query': str(i)}, str(i))]) for i in range(7)]
    responses.append(_response([], json.dumps({'claims': [
        {'text': 'Risk factor evidence', 'evidence_ids': ['doc_1']}]})))
    agent = _agent(responses, {'search_documents': lambda query: []})
    assert agent.run('Research question').claims
    assert len(agent.client.interactions.requests) == 8
