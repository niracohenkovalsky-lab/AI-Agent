import pytest

from src.utils.evidence import Evidence, EvidenceError, EvidenceRegistry


def test_registry_rejects_unknown_citation_ids() -> None:
    registry = EvidenceRegistry()
    registry.add(Evidence("doc_1", "document", "Item 1A", "Risk text", "item-1a"))
    registry.validate_evidence_ids(["doc_1"])

    with pytest.raises(EvidenceError, match="Unknown evidence"):
        registry.validate_evidence_ids(["missing"])
