"""Deterministic financial calculations resolved from the evidence registry."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from src.utils.evidence import Calculation, EvidenceError, EvidenceRegistry


class FinancialCalculator:
    """The one calculation tool; it never accepts model-invented numeric values."""

    def __init__(self, registry: EvidenceRegistry) -> None:
        self.registry = registry

    def percentage(self, numerator_fact_id: str, denominator_fact_id: str) -> Calculation:
        numerator = self.registry.get_fact(numerator_fact_id)
        denominator = self.registry.get_fact(denominator_fact_id)
        self._require_matching_periods(numerator.reporting_period, denominator.reporting_period)
        self._require_compatible_units(numerator.unit, denominator.unit)
        if denominator.value == 0:
            raise EvidenceError("Cannot calculate a percentage with a zero denominator")
        value = (numerator.value / denominator.value * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return self._record(
            "percentage",
            value,
            f"({numerator.value} / {denominator.value}) * 100",
            (numerator.fact_id, denominator.fact_id),
            (numerator.reporting_period or "unknown",),
            tuple(unit for unit in (numerator.unit, denominator.unit) if unit),
        )

    def execute(self, operation: str, **arguments: str) -> Calculation:
        """Dispatch the one calculator tool without permitting arbitrary arithmetic."""
        if operation == "percentage":
            return self.percentage(arguments["numerator_fact_id"], arguments["denominator_fact_id"])
        if operation == "gross_margin_percentage":
            return self.gross_margin_percentage(arguments["revenue_fact_id"], arguments["cost_fact_id"])
        if operation == "percentage_point_difference":
            return self.percentage_point_difference(arguments["left_reference"], arguments["right_reference"])
        raise EvidenceError(f"Unsupported calculation operation: {operation}")

    def gross_margin_percentage(self, revenue_fact_id: str, cost_fact_id: str) -> Calculation:
        revenue = self.registry.get_fact(revenue_fact_id)
        cost = self.registry.get_fact(cost_fact_id)
        self._require_matching_periods(revenue.reporting_period, cost.reporting_period)
        self._require_compatible_units(revenue.unit, cost.unit)
        if revenue.value == 0:
            raise EvidenceError("Cannot calculate gross margin with zero revenue")
        value = ((revenue.value - cost.value) / revenue.value * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return self._record(
            "gross_margin_percentage",
            value,
            f"({revenue.value} - {cost.value}) / {revenue.value} * 100",
            (revenue.fact_id, cost.fact_id),
            (revenue.reporting_period or "unknown",),
            tuple(unit for unit in (revenue.unit, cost.unit) if unit),
        )

    def percentage_point_difference(self, left_reference: str, right_reference: str) -> Calculation:
        left_value, left_periods, left_reference_id = self._resolve_percentage_value(left_reference)
        right_value, right_periods, right_reference_id = self._resolve_percentage_value(right_reference)
        value = (left_value - right_value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return self._record(
            "percentage_point_difference",
            value,
            f"{left_value} - {right_value}",
            (left_reference_id, right_reference_id),
            tuple(sorted(set(left_periods + right_periods))),
            ("percentage_points",),
        )

    def _resolve_percentage_value(self, reference: str) -> tuple[Decimal, tuple[str, ...], str]:
        """Resolve a verified percentage calculation or a percent-valued retrieved fact."""
        try:
            calculation = self.registry.get_calculation(reference)
        except EvidenceError:
            fact = self.registry.get_fact(reference)
            if fact.unit != "percent":
                raise EvidenceError(f"Fact {reference} is not a verified percentage")
            return fact.value, (fact.reporting_period or "unknown",), fact.fact_id
        if calculation.operation not in {"percentage", "gross_margin_percentage"}:
            raise EvidenceError(f"Calculation {reference} is not a percentage")
        return calculation.value, calculation.reporting_periods, calculation.calculation_id

    def _record(
        self,
        operation: str,
        value: Decimal,
        formula: str,
        references: tuple[str, ...],
        periods: tuple[str, ...],
        units: tuple[str, ...],
    ) -> Calculation:
        calculation = Calculation(
            calculation_id=f"calc_{uuid4().hex[:10]}",
            operation=operation,
            value=value,
            formula=formula,
            input_references=references,
            reporting_periods=periods,
            units=units,
        )
        return self.registry.add_calculation(calculation)

    @staticmethod
    def _require_matching_periods(left: str | None, right: str | None) -> None:
        if left and right and left != right:
            raise EvidenceError(f"Incompatible reporting periods: {left} and {right}")

    @staticmethod
    def _require_compatible_units(left: str | None, right: str | None) -> None:
        if left and right and left != right:
            raise EvidenceError(f"Incompatible units: {left} and {right}")
