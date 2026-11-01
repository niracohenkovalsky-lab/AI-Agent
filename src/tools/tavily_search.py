"""Tavily search tool with normalized, registered web evidence."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from src.utils.evidence import Evidence, EvidenceRegistry, NumericFact


GROSS_MARGIN_PATTERN = re.compile(
    r"\bgross\s+margin(?:\s+percentage)?(?:\s+(?:was|is|of|at|reached|reported(?:\s+at)?))?\s*[:=]?\s*(?:approximately\s+)?(\d+(?:\.\d+)?)\s*(?:%|percent\b)",
    re.IGNORECASE,
)
FISCAL_PERIOD_PATTERN = re.compile(r"\b(?:fiscal\s+(?:year\s+)?|FY\s*)((?:19|20)\d{2})\b", re.IGNORECASE)


class TavilySearchTool:
    def __init__(self, api_key: str | None, registry: EvidenceRegistry, client=None) -> None:
        self.api_key = api_key
        self.registry = registry
        self._client = client

    def search_web(self, query: str, limit: int = 5) -> list[Evidence]:
        if not self.api_key and self._client is None:
            raise RuntimeError("TAVILY_API_KEY is not configured")
        if not query.strip():
            raise ValueError("Web search query cannot be empty")
        client = self._client or self._create_client()
        # Preserve exception type/status so orchestration can retry transient failures.
        response = client.search(query=query, max_results=limit, search_depth="advanced")
        evidence: list[Evidence] = []
        for result in response.get("results", []):
            evidence_id = f"web_{uuid4().hex[:10]}"
            excerpt = result.get("content") or ""
            record = Evidence(
                evidence_id=evidence_id,
                source_type="web",
                title=result.get("title") or "Untitled web result",
                excerpt=excerpt,
                source_locator=result.get("url") or "",
                url=result.get("url"),
                retrieved_at=datetime.now(UTC).isoformat(),
                numeric_facts=tuple(self._numeric_facts(excerpt, result.get("published_date"), evidence_id)),
            )
            self.registry.add(record)
            evidence.append(record)
        return evidence

    @staticmethod
    def _numeric_facts(excerpt: str, published_date: str | None, evidence_id: str) -> list[NumericFact]:
        """Register only explicitly labelled web metrics the calculator can safely reuse."""
        facts: list[NumericFact] = []
        for index, match in enumerate(GROSS_MARGIN_PATTERN.finditer(excerpt)):
            # shortcut: only unambiguous annual prose metrics; tables need structured extraction.
            start = max(excerpt.rfind('. ', 0, match.start()), excerpt.rfind('\n', 0, match.start())) + 1
            ends = [position for position in (excerpt.find('. ', match.end()), excerpt.find('\n', match.end())) if position >= 0]
            sentence = excerpt[start:min(ends) if ends else len(excerpt)]
            periods = set(FISCAL_PERIOD_PATTERN.findall(sentence))
            if len(periods) != 1 or len(list(GROSS_MARGIN_PATTERN.finditer(sentence))) != 1:
                continue
            if re.search(r'\b(?:quarter|Q[1-4]|three months|six months|nine months|cloud)\b', sentence, re.IGNORECASE):
                continue
            reporting_period = periods.pop()
            try:
                value = Decimal(match.group(1))
            except InvalidOperation:
                continue
            if not 0 <= value <= 100:
                continue
            facts.append(
                NumericFact(
                    fact_id=f"{evidence_id}:fact:{index}",
                    label="Gross margin",
                    value=value,
                    unit="percent",
                    reporting_period=reporting_period,
                    evidence_id=evidence_id,
                )
            )
        return facts

    def _create_client(self):
        try:
            from tavily import TavilyClient
        except ImportError as exc:
            raise RuntimeError("tavily-python is not installed") from exc
        return TavilyClient(api_key=self.api_key)
