"""Evidence and calculation records used throughout one query execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable


class EvidenceError(ValueError):
    """Raised when a tool refers to absent or incompatible evidence."""


@dataclass(frozen=True)
class NumericFact:
    fact_id: str
    label: str
    value: Decimal
    unit: str | None
    reporting_period: str | None
    evidence_id: str


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source_type: str
    title: str
    excerpt: str
    source_locator: str
    reporting_period: str | None = None
    unit: str | None = None
    url: str | None = None
    retrieved_at: str | None = None
    numeric_facts: tuple[NumericFact, ...] = ()


@dataclass(frozen=True)
class Calculation:
    calculation_id: str
    operation: str
    value: Decimal
    formula: str
    input_references: tuple[str, ...]
    reporting_periods: tuple[str, ...]
    units: tuple[str, ...]


@dataclass
class EvidenceRegistry:
    """Per-run source of truth. Model-provided values are never trusted directly."""

    _evidence: dict[str, Evidence] = field(default_factory=dict)
    _facts: dict[str, NumericFact] = field(default_factory=dict)
    _calculations: dict[str, Calculation] = field(default_factory=dict)

    def add(self, evidence: Evidence) -> Evidence:
        if evidence.evidence_id in self._evidence:
            raise EvidenceError(f"Duplicate evidence id: {evidence.evidence_id}")
        self._evidence[evidence.evidence_id] = evidence
        for fact in evidence.numeric_facts:
            if fact.evidence_id != evidence.evidence_id:
                raise EvidenceError("Numeric fact belongs to a different evidence record")
            self._facts[fact.fact_id] = fact
        return evidence

    def get(self, evidence_id: str) -> Evidence:
        try:
            return self._evidence[evidence_id]
        except KeyError as exc:
            raise EvidenceError(f"Unknown evidence id: {evidence_id}") from exc

    def get_fact(self, fact_id: str) -> NumericFact:
        try:
            return self._facts[fact_id]
        except KeyError as exc:
            raise EvidenceError(f"Unknown numeric fact id: {fact_id}") from exc

    def add_calculation(self, calculation: Calculation) -> Calculation:
        self._calculations[calculation.calculation_id] = calculation
        return calculation

    def get_calculation(self, calculation_id: str) -> Calculation:
        try:
            return self._calculations[calculation_id]
        except KeyError as exc:
            raise EvidenceError(f"Unknown calculation id: {calculation_id}") from exc

    def validate_evidence_ids(self, evidence_ids: Iterable[str]) -> None:
        for evidence_id in evidence_ids:
            self.get(evidence_id)

    def sources_for(self, evidence_ids: Iterable[str]) -> list[Evidence]:
        seen: set[str] = set()
        sources: list[Evidence] = []
        for evidence_id in evidence_ids:
            if evidence_id not in seen:
                sources.append(self.get(evidence_id))
                seen.add(evidence_id)
        return sources

