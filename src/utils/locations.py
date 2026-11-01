"""Read printed HTML page labels and internal filing references without guessing."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from bs4 import Tag

NOTE_HEADING = re.compile(r"^Note\s+(\d+)\b(?:\s*[.\-–—:]|\s+[^\x00-\x7f])", re.IGNORECASE)
NOTE_REFERENCE = re.compile(r"\bNote\s+(\d+)\b", re.IGNORECASE)
PAGE_FOOTER = re.compile(r"\bForm\s+10-K\s*\|\s*(\d+)\s*$", re.IGNORECASE)


def printed_pages(nodes: list[Tag]) -> dict[int, str]:
    """SEC footers label preceding content; explicit page wrappers take precedence."""
    pages: dict[int, str] = {}
    page: str | None = None
    # Cover and contents pages precede the first body Item and often have no
    # printed number. Do not assign the first numbered footer to that front matter.
    body_start = next((index for index, node in enumerate(nodes)
                       if len(node.get_text(" ", strip=True)) < 200
                       and re.match(r"^Item\s+\d+[a-z]?\b", node.get_text(" ", strip=True), re.IGNORECASE)
                       and not node.find_parent("table")
                       and not node.find("a", href=True)), 0)
    for index in range(len(nodes) - 1, -1, -1):
        node = nodes[index]
        text = node.get_text(" ", strip=True)
        match = PAGE_FOOTER.search(text) if len(text) < 150 else None
        if match and not node.find(["div", "p", "table"]):
            page = match.group(1)
        wrapper = node.find_parent(attrs={"data-page-number": True})
        explicit = node.get("data-page-number") or (wrapper.get("data-page-number") if wrapper else None)
        if explicit and str(explicit).isdigit():
            pages[id(node)] = str(explicit)
        elif page and index >= body_start:
            pages[id(node)] = page
    return pages


def internal_fragment(href: str, document_url: str | None) -> str | None:
    """Accept fragment-only or same-document links, never external documents."""
    link = urlsplit(href)
    if not link.fragment:
        return None
    if href.startswith("#"):
        return unquote(link.fragment)
    if document_url:
        source = urlsplit(document_url)
        if (link.netloc, link.path) == (source.netloc, source.path):
            return unquote(link.fragment)
    return None
