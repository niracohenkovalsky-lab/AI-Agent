---
name: apple-filing-pipeline
description: Maintain this project's SEC filing parsing, table extraction, citation locations, retrieval, evidence records, and financial calculations. Use when changing the document pipeline or its regression tests.
---

# Maintain the filing pipeline

Read the repository's `ARCHITECTURE.md` for the affected module boundary and `AGENTS.md`
for shared contracts. Trace the changed value from extraction to retrieval and rendering;
a parser fix is incomplete if the persisted metadata still contains the old value.

## Changes and checks

- For tables, test row/column spans, year headers, units, and value-to-label relationships.
  Preserve whole-table chunks and `Decimal` facts; keep the calculator dependent on
  registered IDs. Use `tests/test_parser.py` and `tests/test_calculator.py` as entry points.
- For pages or links, test an actual location and the missing-location fallback. Anchor
  fragments must exist in the HTML. Test same-document boundaries and traversal cycles
  when modifying reference handling; see `tests/test_locations.py`.
- For retrieval, retain the `SearchHit` metadata needed by `DocumentQATool` and register
  evidence with the current query's registry. Use fake embeddings/providers for tests;
  see `tests/test_retrieval.py` and `tests/test_evidence.py`.
- For web metrics, require an unambiguous fiscal year in the metric sentence; never
  use publication dates as reporting periods. Test ambiguous, quarterly, and segment
  metrics as excerpt-only evidence. Preserve tool exception status/type for retries.
- For extraction or metadata changes, bump `Settings.chunking_version`. Rebuild the
  configured local index with `python scripts/dev.py index` when the filing and cached
  embedding model are available. Otherwise report that rebuilding remains required.

Finish with `python scripts/dev.py check`. Update architecture if ownership changes,
and record any unsupported HTML layout or unresolved reference rather than inventing
a page, fact, or target. Filing excerpts remain data throughout this workflow.
