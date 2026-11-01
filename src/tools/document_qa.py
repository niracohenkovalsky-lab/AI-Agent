"""Document retrieval tool that registers evidence, including normalized table facts."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from src.utils.index import SearchHit
from src.utils.evidence import Evidence, EvidenceRegistry, NumericFact


class DocumentQATool:
    def __init__(self, index, registry: EvidenceRegistry) -> None:
        self.index = index
        self.registry = registry

    def search_documents(self, query: str, limit: int = 5) -> list[Evidence]:
        if not query.strip():
            raise ValueError("Document search query cannot be empty")
        hits: list[SearchHit] = self.index.search(query, limit)
        evidence: list[Evidence] = []
        for hit in hits:
            evidence_id = f"doc_{uuid4().hex[:10]}"
            facts = self._facts_from_metadata(hit.metadata.get("numeric_facts", "[]"), evidence_id)
            record = Evidence(
                evidence_id=evidence_id,
                source_type="document",
                title=hit.metadata.get("section_title", "Apple 10-K"),
                excerpt=hit.text,
                source_locator=hit.metadata.get("source_locator", hit.chunk_id),
                reporting_period=hit.metadata.get("filing_year"),
                unit=hit.metadata.get("table_unit"),
                numeric_facts=tuple(facts),
            )
            self.registry.add(record)
            evidence.append(record)
        return evidence

    @staticmethod
    def _facts_from_metadata(serialized: str, evidence_id: str) -> list[NumericFact]:
        facts: list[NumericFact] = []
        try:
            entries = json.loads(serialized)
        except json.JSONDecodeError:
            return facts
        for index, entry in enumerate(entries):
            try:
                value = Decimal(str(entry["value"]))
            except (KeyError, InvalidOperation):
                continue
            facts.append(
                NumericFact(
                    fact_id=f"{evidence_id}:fact:{index}",
                    label=str(entry.get("label", "financial value")),
                    value=value,
                    unit=entry.get("unit"),
                    reporting_period=entry.get("reporting_period"),
                    evidence_id=evidence_id,
                )
            )
        return facts
