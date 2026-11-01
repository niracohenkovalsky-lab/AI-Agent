"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    gemini_model: str
    tavily_api_key: str | None
    filing_path: Path
    chroma_path: Path
    embedding_model: str
    log_level: str
    chunk_size: int = 1_400
    chunk_overlap: int = 180
    # Bump whenever parsing or numeric-fact extraction changes so stale Chroma metadata is rebuilt.
    chunking_version: str = "sec-html-v4"

    @classmethod
    def from_environment(cls, root: Path) -> "Settings":
        try:
            from dotenv import load_dotenv

            load_dotenv(root / ".env")
        except ImportError:
            # Tests and minimal local tooling can still inject environment values.
            pass

        def env_path(name: str, default: str) -> Path:
            value = Path(os.getenv(name, default))
            return value if value.is_absolute() else root / value

        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            tavily_api_key=os.getenv("TAVILY_API_KEY"),
            filing_path=env_path("FILING_PATH", "data/apple_10k_2025.htm"),
            chroma_path=env_path("CHROMA_PATH", "data/chroma"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )

