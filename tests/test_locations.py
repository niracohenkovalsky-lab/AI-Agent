"""Exercise page citations, span expansion, and bounded internal reference traversal."""

import json
from pathlib import Path

from bs4 import BeautifulSoup

from src.tools.document_qa import DocumentQATool
from src.utils.chunker import SectionChunker
from src.utils.evidence import EvidenceRegistry
from src.utils.index import ChromaIndex, SearchHit
from src.utils.parser import SecHtmlParser
from src.utils.locations import internal_fragment


def test_multirow_multicolumn_headers_preserve_financial_relationships():
    html = '''<table><tr><th rowspan="2">Metric</th><th colspan="2">USD millions</th></tr>
    <tr><th>2025</th><th>2024</th></tr><tr><td>Net sales</td><td>400</td><td>350</td></tr>
    <tr><td>R&amp;D</td><td>40</td><td>30</td></tr></table>'''
    table = SecHtmlParser().normalize_table(BeautifulSoup(html, "lxml").table, "t", "Financials")
    assert table.headers == ("Metric", "USD millions / 2025", "USD millions / 2024")
    facts = SecHtmlParser.table_numeric_facts(table, "doc_1")
    assert [(fact.label, str(fact.value)) for fact in facts] == [
        ("Net sales (USD millions / 2025)", "400"), ("Net sales (USD millions / 2024)", "350"),
        ("R&D (USD millions / 2025)", "40"), ("R&D (USD millions / 2024)", "30")]


def test_pages_notes_and_links_survive_parsing_and_chunking():
    html = '''<html><head><link rel="canonical" href="https://example.test/10k"/></head><body>
    <h2 id="item-7">Item 7. Management Discussion</h2>
    <p>See Note 5 to Consolidated Financial Statements and <a href="#note-5">details</a>.</p>
    <div>Apple Inc. | 2025 Form 10-K | 20</div>
    <h2 id="item-8">Item 8. Financial Statements</h2>
    <h3 id="note-5">Note 5. Property and Equipment</h3><p>Equipment information.</p>
    <div>Apple Inc. | 2025 Form 10-K | 39</div></body></html>'''
    filing = SecHtmlParser().parse_soup(BeautifulSoup(html, "lxml"), Path("filing.html"))
    discussion = next(section for section in filing.sections if section.locator == "item-7")
    assert discussion.page_number == "20"
    assert discussion.references == ("note-5",)
    note = next(section for section in filing.sections if section.locator == "note-5")
    assert note.page_number == "39"
    chunks = SectionChunker(100, 10, "test").build(filing)
    chunk = next(chunk for chunk in chunks if chunk.metadata["section_locator"] == "note-5")
    assert chunk.metadata["source_url"] == "https://example.test/10k#note-5"
    assert chunk.metadata["page_number"] == "39"


def test_missing_page_markers_do_not_invent_page_numbers():
    filing = SecHtmlParser().parse_soup(BeautifulSoup(
        '<body><h2>Item 1A. Risk Factors</h2><p>Risk.</p></body>', "lxml"), Path("filing.html"))
    assert all(section.page_number is None for section in filing.sections)


def test_cover_is_not_assigned_the_first_body_page_number():
    html = '''<body><p>Annual report cover.</p><h2>Item 1. Business</h2>
    <p>Business details.</p><div>Apple Inc. | 2025 Form 10-K | 1</div></body>'''
    filing = SecHtmlParser().parse_soup(BeautifulSoup(html, "lxml"), Path("filing.html"))
    assert filing.sections[0].page_number is None
    assert filing.sections[1].page_number == "1"


def test_explicit_pages_do_not_mix_narrative_across_page_boundaries():
    html = '''<body><div data-page-number="10"><h2 id="risk">Item 1A. Risk Factors</h2>
    <p>First page risk.</p></div><div data-page-number="11"><p>Second page risk.</p></div></body>'''
    filing = SecHtmlParser().parse_soup(BeautifulSoup(html, "lxml"), Path("filing.html"))
    chunks = SectionChunker(100, 10, "test").build(filing)
    assert [(chunk.text, chunk.metadata["page_number"]) for chunk in chunks] == [
        ("First page risk.", "10"), ("Second page risk.", "11")]


def test_empty_anchor_before_a_note_resolves_to_the_note():
    html = '''<body><h2>Item 7. Discussion</h2><p><a href="#equipment">Details</a></p>
    <div id="equipment"></div><h3>Note 5. Equipment</h3><p>Equipment facts.</p></body>'''
    filing = SecHtmlParser().parse_soup(BeautifulSoup(html, "lxml"), Path("filing.html"))
    discussion = next(section for section in filing.sections if section.title.startswith("Item 7"))
    note = next(section for section in filing.sections if section.title.startswith("Note 5"))
    assert discussion.references == (note.locator,)


def test_internal_links_cannot_redirect_to_a_different_document():
    assert internal_fragment("#note-5", None) == "note-5"
    assert internal_fragment("https://example.test/10k#note-5", "https://example.test/10k") == "note-5"
    assert internal_fragment("https://elsewhere.test/10k#note-5", "https://example.test/10k") is None
    assert internal_fragment("https://example.test/other#note-5", "https://example.test/10k") is None


def test_internal_link_traversal_registers_target_and_stops_cycles():
    hits = [SearchHit("a", "See Note 5", {"section_locator": "item-7", "references": '["note-5"]'})]
    target = SearchHit("b", "Equipment information", {
        "section_locator": "note-5", "section_title": "Note 5", "source_locator": "note-5",
        "page_number": "39", "references": '["item-7"]'})
    class Collection:
        def get(self, **kwargs):
            return {"ids": [hit.chunk_id for hit in [*hits, target]],
                    "documents": [hit.text for hit in [*hits, target]],
                    "metadatas": [hit.metadata for hit in [*hits, target]]}

    class Index(ChromaIndex):
        def __init__(self):
            pass

        def _open_collection(self):
            return Collection()

        def search(self, query, limit):
            return hits

    index = Index()
    assert [hit.chunk_id for hit in index.follow_references(hits, max_depth=20)] == ["a", "b"]
    assert index.follow_references(hits, max_additional=0) == hits
    evidence = DocumentQATool(index, EvidenceRegistry()).search_documents("equipment")
    assert len(evidence) == 2
    assert evidence[1].page_number == "39"


def test_corrupt_or_nonfinite_numeric_metadata_is_not_registered():
    for data in ("null", "{}", "not JSON", json.dumps([None, {"value": "NaN"}, {"value": "Infinity"}])):
        assert DocumentQATool._facts_from_metadata(data, "doc_1") == []
