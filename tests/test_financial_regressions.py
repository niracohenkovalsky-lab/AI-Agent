import json
from decimal import Decimal

import pytest

from src.agent import GeminiResearchAgent, format_response
from src.tools.tavily_search import TavilySearchTool
from src.utils.calculator import FinancialCalculator
from src.utils.evidence import Evidence, EvidenceRegistry, NumericFact


def agent(registry):
    return GeminiResearchAgent('test', 'test', '', registry, {}, client=object())


def test_valid_id_does_not_support_invented_claim():
    registry = EvidenceRegistry()
    registry.add(Evidence('doc', 'document', 'Report', 'Margin fell due to costs.', 'section'))
    with pytest.raises(RuntimeError, match='verbatim'):
        agent(registry)._parse_final_response(json.dumps({
            'claims': [{'text': 'Margin rose due to demand.', 'evidence_ids': ['doc']}]}))


def test_calculation_claim_uses_registry_value_and_period_caveat():
    registry = EvidenceRegistry()
    for name, value, period in [('apple', '45', '2024'), ('ms', '69', '2025')]:
        registry.add(Evidence(name, 'web', name, 'Margin', name, numeric_facts=(
            NumericFact(name + '_fact', 'Gross margin', Decimal(value), 'percent', period, name),)))
    calc = FinancialCalculator(registry).percentage_point_difference('apple_fact', 'ms_fact')
    response = agent(registry)._parse_final_response(json.dumps({
        'claims': [{'text': 'Invented 99%', 'evidence_ids': ['apple', 'ms'],
                    'calculation_id': calc.calculation_id}], 'calculation_ids': [calc.calculation_id]}))
    rendered = format_response(response, registry)
    assert '99%' not in rendered
    assert '-24.00' in rendered
    assert '2024' in rendered and '2025' in rendered
    assert 'different reporting periods' in rendered


def test_web_period_is_not_publication_year_or_first_year():
    facts = TavilySearchTool._numeric_facts(
        'Published 2026. Gross margin was 69% for fiscal 2025.', '2026-01-01', 'web')
    assert facts[0].reporting_period == '2025'
    facts = TavilySearchTool._numeric_facts('Gross margin was 69%.', '2026-01-01', 'web')
    assert not facts


def test_tool_transient_failure_retries_but_permanent_failure_does_not(monkeypatch):
    monkeypatch.setattr('src.agent.time.sleep', lambda _: None)
    research = agent(EvidenceRegistry())
    attempts = []

    def search(query):
        attempts.append(query)
        if len(attempts) == 1:
            raise TimeoutError()
        return []

    research.tool_handlers['search_web'] = search
    assert research._execute_tool('search_web', {'query': 'margin'})['ok']
    assert len(attempts) == 2


@pytest.mark.parametrize('status, expected', [(503, 3), (401, 1), (400, 1)])
def test_tool_retry_exhaustion_and_permanent_errors(monkeypatch, status, expected):
    monkeypatch.setattr('src.agent.time.sleep', lambda _: None)
    research = agent(EvidenceRegistry())
    attempts = []

    def search(query):
        attempts.append(query)
        error = RuntimeError('private provider detail')
        error.status_code = status
        raise error

    research.tool_handlers['search_web'] = search
    result = research._execute_tool('search_web', {'query': 'margin'})
    assert not result['ok']
    assert 'private' not in str(result)
    assert len(attempts) == expected


@pytest.mark.parametrize('excerpt', [
    'Gross margin was 69% for fiscal 2025 versus fiscal 2024.',
    'Cloud gross margin was 69% for fiscal 2025.',
    'Gross margin was 69% in Q1 of fiscal 2025.',
    'Gross margin was 169% for fiscal 2025.',
])
def test_ambiguous_or_incompatible_web_metrics_are_not_calculator_facts(excerpt):
    assert not TavilySearchTool._numeric_facts(excerpt, None, 'web')


def test_calculation_claim_rejects_unrelated_valid_citation():
    registry = EvidenceRegistry()
    registry.add(Evidence('doc', 'document', 'Report', 'Data', 'section', numeric_facts=(
        NumericFact('n', 'Cost', Decimal('2'), 'USD', '2025', 'doc'),
        NumericFact('d', 'Sales', Decimal('4'), 'USD', '2025', 'doc'))))
    registry.add(Evidence('other', 'document', 'Unrelated', 'Data', 'other'))
    calc = FinancialCalculator(registry).percentage('n', 'd')
    with pytest.raises(RuntimeError, match='exact input evidence'):
        agent(registry)._parse_final_response(json.dumps({
            'claims': [{'text': 'Calculation', 'evidence_ids': ['other'],
                        'calculation_id': calc.calculation_id}], 'calculation_ids': [calc.calculation_id]}))


def test_web_metric_accepts_percent_word_and_explicit_fiscal_year():
    facts = TavilySearchTool._numeric_facts(
        'For FY2025, gross margin percentage was 68.4 percent.', None, 'web')
    assert facts[0].value == Decimal('68.4')
    assert facts[0].reporting_period == '2025'
