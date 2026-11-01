from decimal import Decimal
from pathlib import Path

from src.utils.chunker import SectionChunker
from src.utils.parser import SecHtmlParser


FIXTURE = Path(__file__).parent / "fixtures" / "apple_10k_sample.html"


def test_parser_preserves_sections_and_table_headers() -> None:
    filing = SecHtmlParser().parse_file(FIXTURE)

    titles = [section.title for section in filing.sections]
    assert "Item 1A. Risk Factors" in titles
    financials = next(section for section in filing.sections if section.title.startswith("Item 8"))
    table = financials.tables[0]
    assert table.headers[0] == "Line item"
    assert "2024" in table.headers[1]
    assert table.rows[0][0] == "Net sales"
    assert table.unit == "USD millions"


def test_normalized_table_facts_keep_year_unit_and_row_mapping() -> None:
    filing = SecHtmlParser().parse_file(FIXTURE)
    table = next(section for section in filing.sections if section.title.startswith("Item 8")).tables[0]

    facts = SecHtmlParser.table_numeric_facts(table, "doc_1")
    revenue = next(fact for fact in facts if fact.label.startswith("Net sales") and "2024" in fact.label)

    assert revenue.value == Decimal("391035")
    assert revenue.unit == "USD millions"
    assert revenue.reporting_period == "2024"
    assert revenue.evidence_id == "doc_1"


def test_parser_uses_inline_xbrl_fiscal_year_and_preserves_percentage_units() -> None:
    filing = SecHtmlParser().parse_file(Path("data/apple_10k_2025.htm"))

    assert filing.filing_year == "2025"
    margin_table = next(
        table
        for section in filing.sections
        if section.title.startswith("Item 7")
        for table in section.tables
        if "gross margin percentage" in table.render().lower()
    )
    margin = next(
        fact
        for fact in SecHtmlParser.table_numeric_facts(margin_table, "doc_1")
        if fact.label.startswith("Total gross margin percentage")
    )

    assert margin.value == Decimal("46.9")
    assert margin.unit == "percent"


def test_chunker_keeps_financial_tables_as_whole_chunks() -> None:
    filing = SecHtmlParser().parse_file(FIXTURE)
    chunks = SectionChunker(chunk_size=12, overlap=2, version="test-v1").build(filing)

    table_chunk = next(chunk for chunk in chunks if chunk.metadata["content_type"] == "table")
    assert "Line item" in table_chunk.text
    assert "Research and Development" in table_chunk.text
    assert table_chunk.metadata["table_unit"] == "USD millions"


def test_parser_extracts_html_from_browser_saved_mhtml(tmp_path: Path) -> None:
    source = tmp_path / "filing.htm"
    source.write_bytes(
        b'From: <Saved by Blink>\r\nMIME-Version: 1.0\r\nContent-Type: multipart/related; boundary="x"\r\n\r\n'
        b'--x\r\nContent-Type: text/html; charset=utf-8\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n'
        b'<html><body><div>Item 1A. Risk Factors</div><div>Supply chain risk.</div></body></html>\r\n--x--\r\n'
    )

    filing = SecHtmlParser().parse_file(source)

    risk_section = next(section for section in filing.sections if section.title.startswith("Item 1A"))
    assert "Supply chain risk." in risk_section.paragraphs

