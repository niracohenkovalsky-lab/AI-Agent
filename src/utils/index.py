"""Persistent Chroma index and compatibility manifest."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from src.utils.chunker import Chunk


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    text: str
    metadata: dict[str, str]


@dataclass(frozen=True)
class IndexManifest:
    source_hash: str
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    chunking_version: str


class Embedder(Protocol):
    model_id: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalSentenceTransformerEmbedder:
    """Lazy local embedder so tests never download a model."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self._model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            # The first successful index run caches this public model. Subsequent runs must
            # not block on optional Hugging Face metadata requests.
            self._model = SentenceTransformer(self.model_id, local_files_only=True)
        return self._model.encode(texts, normalize_embeddings=True).tolist()


class ChromaIndex:
    collection_name = "apple_10k"

    def __init__(self, path: Path, embedder: Embedder) -> None:
        self.path = path
        self.embedder = embedder
        self.manifest_path = path.parent / "index_manifest.json"
        self._collection = None

    @staticmethod
    def source_hash(source_file: Path) -> str:
        return hashlib.sha256(source_file.read_bytes()).hexdigest()

    def expected_manifest(
        self, source_file: Path, chunk_size: int, chunk_overlap: int, chunking_version: str
    ) -> IndexManifest:
        return IndexManifest(
            source_hash=self.source_hash(source_file),
            embedding_model=self.embedder.model_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunking_version=chunking_version,
        )

    def is_compatible(self, expected: IndexManifest) -> bool:
        if not self.manifest_path.exists() or not self.path.exists():
            return False
        try:
            persisted = IndexManifest(**json.loads(self.manifest_path.read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False
        return persisted == expected

    def build(self, chunks: list[Chunk], manifest: IndexManifest) -> None:
        if not chunks:
            raise ValueError("Cannot build an index with no filing chunks")
        collection = self._open_collection(recreate=True)
        embeddings = self.embedder.embed([chunk.text for chunk in chunks])
        collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            metadatas=[chunk.metadata for chunk in chunks],
            embeddings=embeddings,
        )
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(asdict(manifest), indent=2), encoding="utf-8")

    def search(self, query: str, limit: int = 6) -> list[SearchHit]:
        collection = self._open_collection()
        semantic = collection.query(
            query_embeddings=self.embedder.embed([query]),
            n_results=limit,
            include=["documents", "metadatas"],
        )
        hits = self._results_to_hits(semantic)
        # Exact matching supplements embeddings for labels such as "Research and Development".
        all_records = collection.get(include=["documents", "metadatas"])
        terms = {term.lower() for term in query.split() if len(term) > 3}
        exact = []
        for chunk_id, text, metadata in zip(
            all_records.get("ids", []), all_records.get("documents", []), all_records.get("metadatas", [])
        ):
            lowered = (text or "").lower()
            if sum(term in lowered for term in terms) >= 2:
                exact.append(SearchHit(chunk_id, text or "", metadata or {}))
        merged: dict[str, SearchHit] = {hit.chunk_id: hit for hit in exact}
        merged.update({hit.chunk_id: hit for hit in hits})
        # SEC filings repeat many terms in introductory text. Prefer a direct section-title
        # match and financial-table chunks over generic narrative when the query is specific.
        def score(hit: SearchHit) -> tuple[int, int]:
            text = hit.text.lower()
            title = hit.metadata.get("section_title", "").lower()
            term_score = sum(text.count(term) for term in terms)
            title_score = 4 * sum(term in title for term in terms)
            item_score = 16 if "item 1a" in query.lower() and "item 1a" in title else 0
            risk_section_score = 100 if {"risk", "factors"}.issubset(terms) and "risk factors" in title else 0
            table_score = 2 if hit.metadata.get("content_type") == "table" and any(
                term in {"research", "development", "sales", "revenue", "cost", "margin"} for term in terms
            ) else 0
            return risk_section_score + item_score + title_score + table_score + term_score, int(
                hit.chunk_id in {item.chunk_id for item in hits}
            )

        return sorted(merged.values(), key=score, reverse=True)[:limit]

    def _open_collection(self, recreate: bool = False):
        try:
            import chromadb
        except ImportError as exc:
            raise RuntimeError("ChromaDB is not installed; install requirements.txt") from exc
        client = chromadb.PersistentClient(path=str(self.path))
        if recreate:
            try:
                client.delete_collection(self.collection_name)
            except Exception:  # collection does not yet exist
                pass
        self._collection = client.get_or_create_collection(self.collection_name)
        return self._collection

    def follow_references(self, hits: list[SearchHit], max_depth: int = 2, max_additional: int = 8) -> list[SearchHit]:
        """Expand local note/anchor links with bounded traversal and cycle detection."""
        if not any(json.loads(hit.metadata.get("references", "[]")) for hit in hits):
            return hits
        records = self._open_collection().get(include=["documents", "metadatas"])
        by_locator: dict[str, list[SearchHit]] = {}
        for chunk_id, document, metadata in zip(records["ids"], records["documents"], records["metadatas"]):
            metadata = metadata or {}
            by_locator.setdefault(metadata.get("section_locator", ""), []).append(
                SearchHit(chunk_id, document or "", metadata)
            )
        expanded = {hit.chunk_id: hit for hit in hits}
        frontier = hits
        for _ in range(max_depth):
            following = []
            for hit in frontier:
                for locator in json.loads(hit.metadata.get("references", "[]")):
                    for target in by_locator.get(locator, []):
                        if target.chunk_id in expanded:
                            continue
                        if len(expanded) >= len(hits) + max_additional:
                            return list(expanded.values())
                        expanded[target.chunk_id] = target
                        following.append(target)
            frontier = following
            if not frontier:
                break
        return list(expanded.values())

    @staticmethod
    def _results_to_hits(results: dict) -> list[SearchHit]:
        ids = (results.get("ids") or [[]])[0]
        documents = (results.get("documents") or [[]])[0]
        metadata = (results.get("metadatas") or [[]])[0]
        return [SearchHit(chunk_id, text or "", data or {}) for chunk_id, text, data in zip(ids, documents, metadata)]
