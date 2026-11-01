"""Structure-preserving parser for SEC filing HTML."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from email import policy
from email.parser import BytesParser
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from src.utils.locations import NOTE_HEADING, NOTE_REFERENCE, PAGE_FOOTER, internal_fragment, printed_pages


ITEM_PATTERN = re.compile(r"^\s*item\s+\d{1,2}[a-z]?\b", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}")
NUMBER_PATTERN = re.compile(r"^[\d,]+(?:\.\d+)?$")


@dataclass(frozen=True)
class ParsedTable:
    title: str
    locator: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    unit: str | None
    years: tuple[str, ...]

    def render(self) -> str:
        header_line = " | ".join(self.headers)
        row_lines = [" | ".join(row) for row in self.rows]
        return "\n".join([self.title, header_line, *row_lines])


@dataclass(frozen=True)
class ParsedSection:
    title: str
    locator: str
    paragraphs: tuple[str, ...]
    tables: tuple[ParsedTable, ...]
    page_number: str | None = None
    references: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        parts = [self.title, *self.paragraphs]
        parts.extend(table.render() for table in self.tables)
        return "\n\n".join(part for part in parts if part)


@dataclass(frozen=True)
class ParsedFiling:
    source_file: Path
    filing_year: str | None
    filing_date: str | None
    sections: tuple[ParsedSection, ...]
    document_url: str | None = None
    anchor_ids: tuple[str, ...] = ()


@dataclass
class _SectionBuilder:
    title: str
    locator: str
    paragraphs: list[str] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)
    page_number: str | None = None
    references: list[str] = field(default_factory=list)


@dataclass
class _FilingBuilder:
    document_url: str | None
    builders: list[_SectionBuilder] = field(default_factory=lambda: [_SectionBuilder("Filing overview", "overview")])
    anchors: dict[str, str] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    node_locations: dict[int, str] = field(default_factory=dict)
    table_count: int = 0

    @property
    def current(self) -> _SectionBuilder:
        return self.builders[-1]


class SecHtmlParser:
    """Extract filing sections and tabular financial context from an SEC HTML filing."""

    def parse_file(self, path: Path) -> ParsedFiling:
        if not path.exists():
            raise FileNotFoundError(f"Filing not found: {path}")
        soup = BeautifulSoup(self._read_html_payload(path.read_bytes()), "lxml")
        return self.parse_soup(soup, path)

    def parse_soup(self, soup: BeautifulSoup, source_file: Path) -> ParsedFiling:
        body = soup.body or soup
        state = _FilingBuilder(self._document_url(soup))
        nodes = body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "table"])
        pages = printed_pages(nodes)
        for node in nodes:
            self._process_node(node, pages, state)
        self._resolve_anchors(soup, state)
        return self._build_filing(soup, body, source_file, state)

    def _document_url(self, soup: BeautifulSoup) -> str | None:
        canonical = soup.find("link", rel="canonical")
        if canonical and canonical.get("href"):
            return str(canonical.get("href"))
        # Saved SEC HTML often retains absolute links to its original document.
        for link in soup.find_all("a", href=True):
            href = str(link["href"])
            if href.startswith("https://www.sec.gov/Archives/") and "#" in href:
                return href.split("#", 1)[0]
        return None

    def _skip_node(self, node: Tag) -> bool:
        if not isinstance(node, Tag) or node.find_parent("table"):
            return True
        # Workiva layout wrappers duplicate the text of their leaf blocks.
        return node.name == "div" and bool(node.find(
            ["div", "p", "table", "h1", "h2", "h3", "h4", "h5", "h6"], recursive=False))

    def _process_node(self, node: Tag, pages: dict[int, str], state: _FilingBuilder) -> None:
        if self._skip_node(node):
            return
        text = self._clean(node.get_text(" ", strip=True))
        if PAGE_FOOTER.search(text) and len(text) < 150:
            return
        if node.name != "table" and self._is_section_heading(text) and not self._is_toc_link(node):
            self._start_section(node, text, pages.get(id(node)), state)
        else:
            self._append_node(node, text, pages.get(id(node)), state)

    def _start_section(self, node: Tag, text: str, page: str | None, state: _FilingBuilder) -> None:
        child_anchor = node.find(id=True)
        locator = node.get("id") or (child_anchor.get("id") if child_anchor else None) or self._slug(text)
        state.builders.append(_SectionBuilder(text, locator, page_number=page))
        note = NOTE_HEADING.match(text)
        if note:
            state.notes[note.group(1)] = locator
        self._record_location(node, state)

    def _record_location(self, node: Tag, state: _FilingBuilder) -> None:
        state.node_locations[id(node)] = state.current.locator
        for element in [node, *node.find_all(id=True)]:
            if element.get("id"):
                state.anchors[str(element["id"])] = state.current.locator

    def _continue_page(self, page: str | None, state: _FilingBuilder) -> None:
        current = state.current
        if page != current.page_number and (current.paragraphs or current.tables):
            state.builders.append(_SectionBuilder(current.title, current.locator, page_number=page))
        else:
            current.page_number = page

    def _append_node(self, node: Tag, text: str, page: str | None, state: _FilingBuilder) -> None:
        self._continue_page(page, state)
        self._record_location(node, state)
        self._collect_references(node, text, state)
        if node.name == "table":
            self._append_table(node, state)
        elif text:
            state.current.paragraphs.append(text)

    def _collect_references(self, node: Tag, text: str, state: _FilingBuilder) -> None:
        for link in node.find_all("a", href=True):
            fragment = internal_fragment(str(link["href"]), state.document_url)
            if fragment:
                state.current.references.append(fragment)
        state.current.references.extend(f"note:{number}" for number in NOTE_REFERENCE.findall(text))

    def _append_table(self, node: Tag, state: _FilingBuilder) -> None:
        state.table_count += 1
        current = state.current
        current.tables.append(self.normalize_table(
            node, f"{current.locator}-table-{state.table_count}",
            current.title, " ".join(current.paragraphs[-3:])))

    def _resolve_anchors(self, soup: BeautifulSoup, state: _FilingBuilder) -> None:
        # Anchors may sit on a wrapper or an empty marker before a heading.
        for target in soup.find_all(id=True):
            destination = self._anchor_destination(target, state.node_locations)
            if destination:
                state.anchors[str(target["id"])] = state.node_locations[id(destination)]

    def _anchor_destination(self, target: Tag, locations: dict[int, str]) -> Tag | None:
        descendant = target.find(lambda tag: id(tag) in locations)
        if descendant:
            return descendant
        if not target.get_text(strip=True):
            return target.find_next(lambda tag: id(tag) in locations and bool(tag.get_text(strip=True)))
        return None

    def _section_references(self, builder: _SectionBuilder, state: _FilingBuilder) -> tuple[str, ...]:
        resolved = (state.notes.get(ref[5:]) if ref.startswith("note:") else state.anchors.get(ref)
                    for ref in builder.references)
        return tuple(dict.fromkeys(ref for ref in resolved if ref and ref != builder.locator))

    def _build_section(self, builder: _SectionBuilder, state: _FilingBuilder) -> ParsedSection:
        return ParsedSection(
            title=builder.title, locator=builder.locator,
            paragraphs=tuple(builder.paragraphs), tables=tuple(builder.tables),
            page_number=builder.page_number, references=self._section_references(builder, state))

    def _build_filing(self, soup: BeautifulSoup, body: Tag, source: Path, state: _FilingBuilder) -> ParsedFiling:
        sections = tuple(self._build_section(builder, state) for builder in state.builders
                         if builder.paragraphs or builder.tables or builder.title != "Filing overview")
        raw_text = body.get_text(" ", strip=True)
        return ParsedFiling(
            source_file=source, filing_year=self._filing_year(soup, raw_text),
            filing_date=self._find_filing_date(raw_text), sections=sections,
            document_url=state.document_url,
            anchor_ids=tuple(str(node["id"]) for node in soup.find_all(id=True)))

    def normalize_table(
        self,
        table: Tag,
        locator: str,
        section_title: str,
        nearby_context: str = "",
    ) -> ParsedTable:
        """Expand HTML table spans into a rectangular grid without losing header context."""
        grid: list[list[str | None]] = []
        row_header_flags: list[list[bool]] = []
        for row_index, tr in enumerate(table.find_all("tr")):
            while len(grid) <= row_index:
                grid.append([])
                row_header_flags.append([])
            column = 0
            for cell in tr.find_all(["th", "td"], recursive=False):
                while column < len(grid[row_index]) and grid[row_index][column] is not None:
                    column += 1
                text = self._clean(cell.get_text(" ", strip=True))
                colspan = self._span(cell.get("colspan"))
                rowspan = self._span(cell.get("rowspan"))
                for target_row in range(row_index, row_index + rowspan):
                    while len(grid) <= target_row:
                        grid.append([])
                        row_header_flags.append([])
                    while len(grid[target_row]) < column + colspan:
                        grid[target_row].append(None)
                        row_header_flags[target_row].append(False)
                    for target_col in range(column, column + colspan):
                        grid[target_row][target_col] = text
                        row_header_flags[target_row][target_col] = cell.name == "th"
                column += colspan

        width = max((len(row) for row in grid), default=0)
        rendered_rows = [tuple((cell or "").strip() for cell in row + [None] * (width - len(row))) for row in grid]
        flags = [row + [False] * (width - len(row)) for row in row_header_flags]
        header_count = self._header_row_count(rendered_rows, flags)
        headers = self._merge_headers(rendered_rows[:header_count], width)
        data_rows = tuple(rendered_rows[header_count:])
        table_text = table.get_text(" ", strip=True)
        unit = self._detect_unit(f"{section_title} {nearby_context} {table_text}")
        years = tuple(dict.fromkeys(YEAR_PATTERN.findall(" ".join(headers))))
        return ParsedTable(
            title=section_title,
            locator=locator,
            headers=headers,
            rows=data_rows,
            unit=unit,
            years=years,
        )

    @staticmethod
    def table_numeric_facts(table: ParsedTable, evidence_id: str):
        """Yield normalized financial values; raw numeric text always retains its table context."""
        from src.utils.evidence import NumericFact

        facts: list[NumericFact] = []
        for row_index, row in enumerate(table.rows):
            if not row:
                continue
            label = row[0]
            for column, raw_value in enumerate(row[1:], start=1):
                value = SecHtmlParser.parse_number(raw_value)
                if value is None:
                    continue
                header = table.headers[column] if column < len(table.headers) else f"column {column + 1}"
                period_match = YEAR_PATTERN.search(header)
                facts.append(
                    NumericFact(
                        fact_id=f"{evidence_id}:fact:{row_index}:{column}",
                        label=f"{label} ({header})",
                        value=value,
                        unit="percent" if SecHtmlParser._is_percent_value(row, column, raw_value) else table.unit,
                        reporting_period=period_match.group(0) if period_match else None,
                        evidence_id=evidence_id,
                    )
                )
        return tuple(facts)

    @staticmethod
    def parse_number(raw_value: str) -> Decimal | None:
        normalized = raw_value.strip()
        if normalized in {"", "—", "-", "N/A"}:
            return None
        negative = normalized.startswith("(") and normalized.endswith(")")
        if negative:
            normalized = normalized[1:-1]
        normalized = normalized.strip().removeprefix("$").strip().removesuffix("%").strip()
        if not NUMBER_PATTERN.fullmatch(normalized):
            return None
        try:
            value = Decimal(normalized.replace(",", ""))
        except InvalidOperation:
            return None
        return -value if negative else value

    @staticmethod
    def _is_percent_value(row: tuple[str, ...], column: int, raw_value: str) -> bool:
        """Recognize percentages whether SEC HTML keeps % with its value or in a sibling cell."""
        if "%" in raw_value:
            return True
        nearby_cells = row[max(0, column - 1) : min(len(row), column + 3)]
        return any(cell.strip() == "%" for cell in nearby_cells)

    @staticmethod
    def _filing_year(soup: BeautifulSoup, raw_text: str) -> str | None:
        """Prefer the authoritative inline-XBRL fiscal-year field over incidental dates."""
        fiscal_year = soup.find(attrs={"name": re.compile(r"(?:^|:)DocumentFiscalYearFocus$", re.IGNORECASE)})
        if fiscal_year:
            match = YEAR_PATTERN.search(fiscal_year.get_text(" ", strip=True))
            if match:
                return match.group(0)
        period_end = soup.find(attrs={"name": re.compile(r"(?:^|:)DocumentPeriodEndDate$", re.IGNORECASE)})
        if period_end:
            match = YEAR_PATTERN.search(period_end.get_text(" ", strip=True))
            if match:
                return match.group(0)
        years = YEAR_PATTERN.findall(raw_text)
        return max(years) if years else None

    @staticmethod
    def _span(value: str | None) -> int:
        try:
            return max(1, int(value or "1"))
        except ValueError:
            return 1

    @staticmethod
    def _header_row_count(rows: list[tuple[str, ...]], flags: list[list[bool]]) -> int:
        for index, row in enumerate(rows):
            nonempty = [cell for cell in row if cell]
            has_numeric = any(SecHtmlParser.parse_number(cell) is not None for cell in nonempty[1:])
            header_like = bool(flags[index]) and all(flags[index][: len(row)])
            if has_numeric and not header_like:
                return max(1, index)
        return min(1, len(rows))

    @staticmethod
    def _merge_headers(rows: list[tuple[str, ...]], width: int) -> tuple[str, ...]:
        headers: list[str] = []
        for column in range(width):
            values = [row[column] for row in rows if column < len(row) and row[column]]
            headers.append(" / ".join(dict.fromkeys(values)) or f"Column {column + 1}")
        return tuple(headers)

    @staticmethod
    def _detect_unit(text: str) -> str | None:
        lowered = text.lower()
        if "in millions" in lowered:
            return "USD millions"
        if "in thousands" in lowered:
            return "USD thousands"
        if "in billions" in lowered:
            return "USD billions"
        return None

    @staticmethod
    def _find_filing_date(text: str) -> str | None:
        match = re.search(r"(?:filed|filing date)\s*[:.]?\s*([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", text)
        return match.group(1) if match else None

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _slug(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

    @staticmethod
    def _looks_like_heading(text: str) -> bool:
        return bool(ITEM_PATTERN.match(text)) or (len(text) < 90 and text.isupper())

    @staticmethod
    def _is_section_heading(text: str) -> bool:
        return len(text) < 200 and bool(ITEM_PATTERN.match(text) or NOTE_HEADING.match(text))

    @staticmethod
    def _is_toc_link(node: Tag) -> bool:
        """Skip table-of-contents links while retaining the actual named Item anchors."""
        anchor = node.find("a", href=True)
        return bool(anchor and "#" in str(anchor.get("href", "")))

    @staticmethod
    def _read_html_payload(raw: bytes) -> str:
        """Accept both direct SEC HTML and Chrome's saved MHTML representation."""
        if raw.startswith(b"From: <Saved by Blink>") or b"Content-Type: multipart/related" in raw[:1_000]:
            message = BytesParser(policy=policy.default).parsebytes(raw)
            for part in message.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return raw.decode("utf-8", errors="replace")
