import pytest

from src.tools.tavily_search import TavilySearchTool
from src.utils.evidence import EvidenceRegistry


class FakeTavily:
    def search(self, query: str, max_results: int, search_depth: str):
        assert query == "Microsoft gross margin"
        assert max_results == 5
        return {"results": [{"title": "Microsoft investor relations", "url": "https://example.test/ms", "content": "Gross margin context."}]}


class FailingTavily:
    def search(self, **kwargs):
        raise TimeoutError("network timeout")


def test_tavily_registers_structured_web_evidence() -> None:
    registry = EvidenceRegistry()
    results = TavilySearchTool("key", registry, FakeTavily()).search_web("Microsoft gross margin")

    assert results[0].source_type == "web"
    assert results[0].url == "https://example.test/ms"
    assert results[0].retrieved_at


def test_tavily_registers_explicit_gross_margin_percentages_as_calculator_facts() -> None:
    class GrossMarginTavily:
        def search(self, query: str, **kwargs):
            return {
                "results": [
                    {
                        "title": "Microsoft investor relations",
                        "url": "https://example.test/ms",
                        "content": "Microsoft reported gross margin was 68.40% for fiscal 2025.",
                        "published_date": "2026-01-28",
                    }
                ]
            }

    registry = EvidenceRegistry()
    result = TavilySearchTool("key", registry, GrossMarginTavily()).search_web("Microsoft gross margin")[0]

    fact = result.numeric_facts[0]
    assert fact.label == "Gross margin"
    assert str(fact.value) == "68.40"
    assert fact.unit == "percent"
    assert fact.reporting_period == "2025"
    assert registry.get_fact(fact.fact_id) == fact


def test_tavily_reports_missing_key_and_api_failures() -> None:
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        TavilySearchTool(None, EvidenceRegistry()).search_web("Microsoft gross margin")
    with pytest.raises(TimeoutError, match="network timeout"):
        TavilySearchTool("key", EvidenceRegistry(), FailingTavily()).search_web("Microsoft gross margin")
