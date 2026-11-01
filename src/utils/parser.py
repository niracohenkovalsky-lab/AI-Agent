"""Structure-preserving parser for SEC filing HTML."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from email import policy
from email.parser import BytesParser
from pathlib import Path

from bs4 import BeautifulSoup, Tag


ITEM_PATTERN = re.compile(r"^\s*item\s+(1a|7|8)\b", re.IGNORECASE)
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


@dataclass
class _SectionBuilder:
    title: str
    locator: str
    paragraphs: list[str] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)


class SecHtmlParser:
    """Extract filing sections and tabular financial context from an SEC HTML filing."""

    def parse_file(self, path: Path) -> ParsedFiling:
        if not path.exists():
            raise FileNotFoundError(f"Filing not found: {path}")
        soup = BeautifulSoup(self._read_html_payload(path.read_bytes()), "lxml")
        return self.parse_soup(soup, path)

    def parse_soup(self, soup: BeautifulSoup, source_file: Path) -> ParsedFiling:
        body = soup.body or soup
        builders: list[_SectionBuilder] = []
        current = _SectionBuilder("Filing overview", "overview")
        builders.append(current)
        table_count = 0

        for node in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "table"]):
            if not isinstance(node, Tag):
                continue
            if node.name == "div" and node.find(["div", "p", "table"], recursive=False):
                # SEC Workiva filings use nested layout divs. Keep only their leaf text blocks.
                continue
            text = self._clean(node.get_text(" ", strip=True))
            if node.name != "table" and self._is_section_heading(text) and not self._is_toc_link(node):
                locator = node.get("id") or self._slug(text)
                current = _SectionBuilder(text, locator)
                builders.append(current)
                continue
            if node.name in {"p", "div"}:
                if text and not self._looks_like_heading(text):
                    current.paragraphs.append(text)
                continue
            if node.name == "table":
                table_count += 1
                current.tables.append(
                    self.normalize_table(
                        node,
                        f"{current.locator}-table-{table_count}",
                        current.title,
                        " ".join(current.paragraphs[-3:]),
                    )
                )

        sections = tuple(
            ParsedSection(
                title=builder.title,
                locator=builder.locator,
                paragraphs=tuple(builder.paragraphs),
                tables=tuple(builder.tables),
            )
            for builder in builders
            if builder.paragraphs or builder.tables or builder.title != "Filing overview"
        )
        raw_text = body.get_text(" ", strip=True)
        return ParsedFiling(
            source_file=source_file,
            filing_year=self._filing_year(soup, raw_text),
            filing_date=self._find_filing_date(raw_text),
            sections=sections,
        )

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
        return bool(ITEM_PATTERN.match(text))

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
