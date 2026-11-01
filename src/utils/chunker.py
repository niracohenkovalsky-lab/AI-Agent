"""Create retrieval chunks while retaining filing section and table context."""

from __future__ import annotations

import json
from dataclasses import dataclass

from src.utils.parser import ParsedFiling, SecHtmlParser


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    metadata: dict[str, str]


class SectionChunker:
    def __init__(self, chunk_size: int, overlap: int, version: str) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.version = version

    def build(self, filing: ParsedFiling) -> list[Chunk]:
        chunks: list[Chunk] = []
        for section_index, section in enumerate(filing.sections):
            base_metadata = {
                "source_file": str(filing.source_file),
                "filing_year": filing.filing_year or "unknown",
                "filing_date": filing.filing_date or "unknown",
                "section_title": section.title,
                "source_locator": section.locator,
                "page_number": section.page_number or "",
                "source_url": ((filing.document_url + "#" + section.locator)
                               if filing.document_url and section.locator in filing.anchor_ids
                               else filing.document_url or ""),
                "section_locator": section.locator,
                "references": json.dumps(section.references),
            }
            text = "\n\n".join(section.paragraphs)
            chunks.extend(self._split_text(text, f"section-{section_index}", base_metadata, "narrative"))
            for table_index, table in enumerate(section.tables):
                metadata = {
                    **base_metadata,
                    "source_locator": table.locator,
                    "content_type": "table",
                    "table_unit": table.unit or "unknown",
                    "table_years": ",".join(table.years),
                    "numeric_facts": json.dumps(
                        [
                            {
                                "label": fact.label,
                                "value": str(fact.value),
                                "unit": fact.unit,
                                "reporting_period": fact.reporting_period,
                            }
                            for fact in SecHtmlParser.table_numeric_facts(table, "template")
                        ]
                    ),
                }
                # Tables remain intact so their header/value relationships survive retrieval.
                chunks.append(
                    Chunk(
                        chunk_id=f"section-{section_index}-table-{table_index}",
                        text=table.render(),
                        metadata=metadata,
                    )
                )
        return chunks

    def _split_text(self, text: str, prefix: str, metadata: dict[str, str], content_type: str) -> list[Chunk]:
        if not text:
            return []
        words = text.split()
        chunks: list[Chunk] = []
        start = 0
        step = max(1, self.chunk_size - self.overlap)
        while start < len(words):
            end = min(len(words), start + self.chunk_size)
            chunks.append(
                Chunk(
                    chunk_id=f"{prefix}-{len(chunks)}",
                    text=" ".join(words[start:end]),
                    metadata={**metadata, "content_type": content_type},
                )
            )
            if end == len(words):
                break
            start += step
        return chunks
