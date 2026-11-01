from decimal import Decimal

import pytest

from src.utils.calculator import FinancialCalculator
from src.utils.evidence import Evidence, EvidenceError, EvidenceRegistry, NumericFact


def _registry() -> EvidenceRegistry:
    registry = EvidenceRegistry()
    registry.add(
        Evidence(
            evidence_id="doc_1",
            source_type="document",
            title="Statement of Operations",
            excerpt="R&D 31370; Net sales 391035; Cost of sales 220960.",
            source_locator="item-8-table-1",
            numeric_facts=(
                NumericFact("rd_2024", "Research and Development", Decimal("31370"), "USD millions", "2024", "doc_1"),
                NumericFact("revenue_2024", "Net sales", Decimal("391035"), "USD millions", "2024", "doc_1"),
                NumericFact("cost_2024", "Cost of sales", Decimal("220960"), "USD millions", "2024", "doc_1"),
            ),
        )
    )
    return registry


def test_percentage_is_calculated_only_from_registry_facts() -> None:
    registry = _registry()
    result = FinancialCalculator(registry).percentage("rd_2024", "revenue_2024")

    assert result.value == Decimal("8.02")
    assert result.input_references == ("rd_2024", "revenue_2024")


def test_gross_margin_and_percentage_point_difference_are_deterministic() -> None:
    registry = _registry()
    calculator = FinancialCalculator(registry)
    apple = calculator.gross_margin_percentage("revenue_2024", "cost_2024")
    registry.add(
        Evidence(
            evidence_id="web_1",
            source_type="web",
            title="Microsoft annual report",
            excerpt="Gross margin was 69.00 percent.",
            source_locator="https://example.test/microsoft",
            numeric_facts=(
                NumericFact("ms_margin_2024", "Gross margin", Decimal("69.00"), "percent", "2024", "web_1"),
            ),
        )
    )
    difference = calculator.percentage_point_difference(apple.calculation_id, "ms_margin_2024")
    assert difference.value == Decimal("-25.51")


def test_mixed_periods_and_zero_denominator_are_rejected() -> None:
    registry = _registry()
    registry.add(
        Evidence(
            evidence_id="doc_2",
            source_type="document",
            title="Prior statement",
            excerpt="Prior revenue.",
            source_locator="item-8-table-2",
            numeric_facts=(
                NumericFact("revenue_2023", "Net sales", Decimal("383285"), "USD millions", "2023", "doc_2"),
                NumericFact("zero_2024", "Zero", Decimal("0"), "USD millions", "2024", "doc_2"),
            ),
        )
    )
    calculator = FinancialCalculator(registry)

    with pytest.raises(EvidenceError, match="reporting periods"):
        calculator.percentage("rd_2024", "revenue_2023")
    with pytest.raises(EvidenceError, match="zero denominator"):
        calculator.percentage("rd_2024", "zero_2024")

