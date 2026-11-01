"""CLI entry point for indexing an Apple 10-K and asking research questions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow the assignment command `python src/main.py` as well as package imports in tests.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent import GeminiResearchAgent, format_response
from src.utils.config import Settings
from src.utils.chunker import SectionChunker
from src.utils.index import ChromaIndex, LocalSentenceTransformerEmbedder
from src.utils.parser import SecHtmlParser
from src.utils.calculator import FinancialCalculator
from src.tools.document_qa import DocumentQATool
from src.tools.tavily_search import TavilySearchTool
from src.utils.evidence import EvidenceRegistry
from src.utils.logging import configure_logging


def _read_prompt(name: str) -> str:
    return (ROOT / "prompts" / name).read_text(encoding="utf-8")


def _index(settings: Settings) -> int:
    if not settings.filing_path.exists():
        print(f"Filing not found: {settings.filing_path}. Download the non-amended Apple 10-K HTML first.")
        return 2
    parser = SecHtmlParser()
    filing = parser.parse_file(settings.filing_path)
    chunker = SectionChunker(settings.chunk_size, settings.chunk_overlap, settings.chunking_version)
    index = ChromaIndex(settings.chroma_path, LocalSentenceTransformerEmbedder(settings.embedding_model))
    manifest = index.expected_manifest(
        settings.filing_path,
        settings.chunk_size,
        settings.chunk_overlap,
        settings.chunking_version,
    )
    if index.is_compatible(manifest):
        print("Index is already compatible with the current filing and configuration.")
        return 0
    chunks = chunker.build(filing)
    index.build(chunks, manifest)
    print(f"Indexed {len(chunks)} chunks from {settings.filing_path.name}.")
    return 0


def _interactive(settings: Settings) -> int:
    if not settings.filing_path.exists():
        print(f"Filing not found: {settings.filing_path}. Add the Apple 10-K HTML, then run `python src/main.py index`.")
        return 2
    index = ChromaIndex(settings.chroma_path, LocalSentenceTransformerEmbedder(settings.embedding_model))
    expected = index.expected_manifest(
        settings.filing_path,
        settings.chunk_size,
        settings.chunk_overlap,
        settings.chunking_version,
    )
    if not index.is_compatible(expected):
        print("No compatible index. Run `python src/main.py index`; query mode never rebuilds an index.")
        return 2
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is not configured. Copy .env.example to .env and add the key.")
        return 2

    system_instruction = "\n\n".join(
        [_read_prompt("system_prompt.txt"), _read_prompt("tool_descriptions.txt"), _read_prompt("examples.txt")]
    )
    print("Apple Financial Research Agent. Type 'exit' to quit.")
    while True:
        try:
            query = input("\nQuestion> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if query.lower() in {"exit", "quit"}:
            return 0
        if not query:
            continue
        registry = EvidenceRegistry()
        document_tool = DocumentQATool(index, registry)
        web_tool = TavilySearchTool(settings.tavily_api_key, registry)
        calculator = FinancialCalculator(registry)
        agent = GeminiResearchAgent(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            system_instruction=system_instruction,
            registry=registry,
            tool_handlers={
                "search_documents": document_tool.search_documents,
                "search_web": web_tool.search_web,
                "calculate_financial_metric": calculator.execute,
            },
        )
        try:
            response = agent.run(query)
            print("\n" + format_response(response, registry))
        except Exception as exc:
            print(f"Unable to complete the research request safely: {exc}")


def main(argv: list[str] | None = None) -> int:
    argument_parser = argparse.ArgumentParser(description="Apple financial research agent")
    argument_parser.add_argument("command", nargs="?", choices=["index"], help="Build or validate the local filing index")
    argument_parser.add_argument("--verbose", action="store_true", help="Enable debug logging without secrets")
    arguments = argument_parser.parse_args(argv)
    settings = Settings.from_environment(ROOT)
    configure_logging("DEBUG" if arguments.verbose else settings.log_level)
    return _index(settings) if arguments.command == "index" else _interactive(settings)


if __name__ == "__main__":
    raise SystemExit(main())

