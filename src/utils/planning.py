"""Visible research approach, produced before any retrieval or provider request."""

from __future__ import annotations


def research_plan(query: str) -> tuple[str, ...]:
    """Describe observable actions, without requesting private model reasoning."""
    lowered = query.lower()
    steps = ["Find relevant sections and financial tables in the indexed Apple 10-K; follow note references."]
    if any(term in lowered for term in ("microsoft", "current", "today", "competitor")):
        steps.append("Search current external sources when needed and check reporting periods.")
    if any(term in lowered for term in ("percent", "ratio", "margin", "r&d", "compare")):
        steps.append("Calculate requested metrics using retrieved financial facts and compatible units.")
    steps.append("Answer with section/page citations and explain missing information or limitations.")
    return tuple(steps)


def format_plan(query: str) -> str:
    return "To answer this, I will:\n" + "\n".join(
        f"{index}. {step}" for index, step in enumerate(research_plan(query), start=1)
    )
