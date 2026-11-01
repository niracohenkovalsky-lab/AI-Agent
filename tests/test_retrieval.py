import json
from pathlib import Path

from src.utils.index import ChromaIndex, IndexManifest
from src.utils.index import SearchHit
from src.utils.chunker import Chunk
from src.tools.document_qa import DocumentQATool
from src.utils.evidence import EvidenceRegistry


class FakeEmbedder:
    model_id = "test-embedder"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]


class FakeIndex:
    def search(self, query: str, limit: int):
        return [
            SearchHit(
                "table-1",
                "Research and Development | 31,370 | Net sales | 391,035",
                {
                    "section_title": "Item 8. Financial Statements",
                    "source_locator": "item-8-table-1",
                    "filing_year": "2024",
                    "table_unit": "USD millions",
                    "numeric_facts": json.dumps(
                        [
                            {"label": "Research and Development (2024)", "value": "31370", "unit": "USD millions", "reporting_period": "2024"},
                            {"label": "Net sales (2024)", "value": "391035", "unit": "USD millions", "reporting_period": "2024"},
                        ]
                    ),
                },
            )
        ]


def test_manifest_requires_hash_model_and_chunk_configuration(tmp_path: Path) -> None:
    source = tmp_path / "apple_10k.html"
    source.write_text("filing v1", encoding="utf-8")
    index = ChromaIndex(tmp_path / "chroma", FakeEmbedder())
    expected = index.expected_manifest(source, 1400, 180, "sec-html-v1")
    index.manifest_path.write_text(json.dumps(expected.__dict__), encoding="utf-8")
    index.path.mkdir()

    assert index.is_compatible(expected)
    assert not index.is_compatible(IndexManifest(expected.source_hash, "other", 1400, 180, "sec-html-v1"))
    source.write_text("filing v2", encoding="utf-8")
    assert not index.is_compatible(index.expected_manifest(source, 1400, 180, "sec-html-v1"))


def test_document_tool_registers_facts_from_retrieved_table_metadata() -> None:
    registry = EvidenceRegistry()
    evidence = DocumentQATool(FakeIndex(), registry).search_documents("R&D revenue")

    assert len(evidence) == 1
    assert len(evidence[0].numeric_facts) == 2
    assert registry.get_fact(evidence[0].numeric_facts[0].fact_id).value == 31370


def test_persistent_chroma_index_builds_and_retrieves_with_local_embeddings(tmp_path: Path) -> None:
    source = tmp_path / "apple_10k.html"
    source.write_text("fixture filing", encoding="utf-8")
    index = ChromaIndex(tmp_path / "chroma", FakeEmbedder())
    manifest = index.expected_manifest(source, 1400, 180, "sec-html-v1")
    index.build(
        [
            Chunk(
                "risk-1",
                "Item 1A Risk Factors include supply chain disruption.",
                {"section_title": "Item 1A", "source_locator": "item-1a", "content_type": "narrative"},
            )
        ],
        manifest,
    )

    hits = index.search("supply chain risk", limit=1)

    assert index.is_compatible(manifest)
    assert hits[0].chunk_id == "risk-1"
