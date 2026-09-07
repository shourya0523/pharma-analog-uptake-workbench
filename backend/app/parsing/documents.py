from __future__ import annotations

# ruff: noqa: BLE001
import json
import re

from bs4 import BeautifulSoup

from app.domain.models import ParsedDocument, ParsingStatus, RetrievedSource, SourceType
from app.storage.filestore import FileStore


class OCRStub:
    """OCR boundary for future scanned PDFs. Always returns unsupported in v1."""

    async def extract(self, data: bytes) -> tuple[str | None, ParsingStatus]:
        return None, ParsingStatus.UNSUPPORTED


# Document -> tables, as module-level functions so anything that needs the
# pipeline's own reading of a file (the extraction eval, for one) uses this
# path rather than a second implementation that could drift from it. The
# limits are the pipeline's real limits and are deliberately not relaxed here.
#
# HTML_TABLE_LIMIT is a safety valve on work, not the way tables are chosen.
# Which tables are kept is decided by `table_relevance` below; the limit only
# bounds a pathological document, and when it binds it drops the least
# table-like rather than whatever happened to be printed last.
#
# It is set above the 95th percentile of tables-that-state-figures per document
# in both the gold corpus and a held-out one (earnings exhibits settle at 12-17;
# the tail is 10-K and 10-Q filings, one of which offers 225). So for an
# ordinary filing the relevance test is the whole rule and this never binds; it
# exists so that a document with a thousand tables cannot cost a thousand
# tables' work.
HTML_TABLE_LIMIT = 80
HTML_ROW_LIMIT = 40
PDF_PAGE_LIMIT = 40
PDF_TABLE_LIMIT = 5

# A figure, as a filing prints one: thousands separators, a leading currency
# sign, a trailing percent, parentheses for negatives.
_FIGURE = re.compile(r"^\(?\s*[$€£¥]?\s*-?\d[\d,]*(\.\d+)?\s*\)?\s*%?$")
# A table that declares money: a currency sign, a unit statement, an ISO code.
_MONEY = re.compile(
    r"[$€£¥]|\bin\s+(millions|thousands|billions)\b|\b(USD|EUR|GBP|CHF|JPY|DKK|SEK|NOK|AUD|CAD)\b",
    re.IGNORECASE,
)
# A table that declares a period: a period heading, a quarter, a year, a month.
_PERIOD = re.compile(
    r"\b(month|quarter|year|period|week|half)s?\s+ended\b|\bQ[1-4]\b|\b(19|20)\d\d\b"
    r"|\b(january|february|march|april|may|june|july|august|september|october"
    r"|november|december)\b",
    re.IGNORECASE,
)
# One line item is enough to be worth reading: an issuer with a single product
# prints a single row, and a sales schedule row often carries one figure and a
# dash for the year it did not sell in. What is excluded is the table with no
# labelled figure anywhere in it.
MIN_FIGURE_ROWS = 1


def html_table_grid(table) -> list[list[str | None]]:
    """One table as a rectangle, keeping what each cell spans.

    ``html_tables`` returns each row as the cells it happens to contain, so a
    heading row of three cells sits beside a value row of nine and the same
    index means a different column in each. Everything a table states by
    position - which period a column belongs to, which figures a heading covers
    - is lost at that point, and has to be guessed back from prose.

    Here every cell occupies each column it spans, so column indices mean the
    same thing in every row. A cell's text is placed at the column it starts in
    and the columns it continues over hold ``None``:

        origin      the cell's own text, or "" for a genuinely empty cell
        None        a column covered by a cell that began to the left or above

    The distinction matters because spans are not only used for headings.
    Gilead prints "141" with ``colspan=2``, so expanding text into every covered
    column would report that value twice; a reader after figures takes the
    origins, and a reader after a heading's extent walks the ``None``s.
    """
    filled: dict[tuple[int, int], str | None] = {}
    for row_index, tr in enumerate(table.find_all("tr")[:HTML_ROW_LIMIT]):
        column = 0
        for cell in tr.find_all(["td", "th"]):
            while (row_index, column) in filled:
                column += 1
            text = re.sub(r"\s+", " ", cell.get_text(" ", strip=True))
            try:
                across = max(1, int(cell.get("colspan", 1)))
                down = max(1, int(cell.get("rowspan", 1)))
            except (TypeError, ValueError):
                across, down = 1, 1
            for extra_row in range(down):
                for extra_column in range(across):
                    origin = extra_row == 0 and extra_column == 0
                    filled[(row_index + extra_row, column + extra_column)] = (
                        text if origin else None
                    )
            column += across
    if not filled:
        return []
    height = max(row for row, _ in filled) + 1
    width = max(column for _, column in filled) + 1
    return [
        [filled.get((row, column)) for column in range(width)]
        for row in range(height)
    ]


def _figure_rows(grid: list[list[str | None]]) -> int:
    """Rows shaped like a line item: a text label, then a figure.

    This is the shape a product sales line has - ``Skyrizi | 3,843 | 580 |
    4,423`` - and it is also the shape of every other financial line item,
    which is the point: the test is "does this row state figures against a
    name", not "is this name a product we know".

    One figure is enough. Gilead files ``Genvoya - U.S. | 141 | -``, a figure
    beside an em dash for the quarter the product did not sell in; demanding
    two would drop the launch quarter of every product in the schedule.
    """
    count = 0
    for row in grid:
        cells = [cell.strip() for cell in row if cell is not None and cell.strip()]
        if len(cells) < 2 or len(cells[0]) < 2 or _FIGURE.match(cells[0]):
            continue
        if any(_FIGURE.match(cell) for cell in cells[1:]):
            count += 1
    return count


def table_relevance(grid: list[list[str | None]]) -> int:
    """How much this table looks like a table of financial data. 0 = discard.

    A document's table order says nothing about where its figures are: a
    Gilead 8-K earnings exhibit holds 39 tables and prints its PRODUCT SALES
    SUMMARY in the thirty-seventh, behind three dozen layout and cover tables.
    Keeping the first N therefore keeps the wrong N. What separates the sales
    schedule from a spacer is not its position but its content - it states
    figures against row labels, and it declares what those figures are in
    (money) or what they cover (a period).

    The gate is the figures. Money and period markers only *rank* what got
    through, because they are evidence rather than requirements: a schedule
    that declares "(in millions)" in the sentence above it rather than inside
    the grid is still a schedule, and 37% of the layout tables in these
    filings carry a stray "$" that means nothing.

    No test here names an issuer, a product, a heading word or a section, so
    nothing in it can learn one filer's layout.
    """
    figures = _figure_rows(grid)
    if figures < MIN_FIGURE_ROWS:
        return 0
    text = " ".join(cell for row in grid for cell in row if cell)
    # More line items is more of a schedule; declaring money and a period is
    # more of a financial statement. Used only to order the survivors, so that
    # the cap - if it ever binds - sheds the weakest rather than the last.
    return (
        figures
        + 2 * bool(_MONEY.search(text))
        + 2 * bool(_PERIOD.search(text))
    )


def html_table_grids(soup: BeautifulSoup) -> list[list[list[str | None]]]:
    """The tables worth keeping, as rectangles rather than ragged rows.

    Selection is by relevance, not by position, and the result stays in
    document order so that index *n* here and index *n* of ``html_tables``
    remain the same table.
    """
    scored: list[tuple[int, int, list[list[str | None]]]] = []
    for position, table in enumerate(soup.find_all("table")):
        grid = html_table_grid(table)
        if not grid:
            continue
        relevance = table_relevance(grid)
        if relevance:
            scored.append((position, relevance, grid))
    if len(scored) > HTML_TABLE_LIMIT:
        scored = sorted(scored, key=lambda item: (-item[1], item[0]))[:HTML_TABLE_LIMIT]
    return [grid for _position, _relevance, grid in sorted(scored, key=lambda i: i[0])]


def flatten_grid(grid: list[list[str | None]]) -> list[list[str]]:
    """A grid read back as ragged rows: the cells that are actually there.

    The origins of a row, in order, are the cells the row was written with, so
    this is the same reading ``html_tables`` always produced. Rows holding
    nothing but continuations of a cell that began above contribute no cells of
    their own and drop out, as they did when they were read a row at a time.
    """
    return [cells for row in grid if (cells := [cell for cell in row if cell is not None])]


def html_tables(soup: BeautifulSoup) -> list[list[list[str]]]:
    """The tables as ragged rows, derived from the same rectangles.

    Deriving one view from the other is what keeps them aligned: table *n* of
    this list and table *n* of ``html_table_grids`` are the same table, so a
    period read off the grid can be trusted to describe the rows here.
    """
    return [rows for grid in html_table_grids(soup) if (rows := flatten_grid(grid))]


def pdf_tables(raw: bytes) -> tuple[list[str], list[list[list[str]]]]:
    import io

    import pdfplumber

    blocks: list[str] = []
    tables: list[list[list[str]]] = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for index, page in enumerate(pdf.pages[:PDF_PAGE_LIMIT]):
            text = page.extract_text() or ""
            if text.strip():
                blocks.append(f"[page {index + 1}]\n{text}")
            for table in (page.extract_tables() or [])[:PDF_TABLE_LIMIT]:
                tables.append([[(cell or "") for cell in row] for row in table])
    return blocks, tables


class DocumentParser:
    def __init__(self, file_store: FileStore) -> None:
        self.file_store = file_store
        self.ocr = OCRStub()

    async def parse(self, source: RetrievedSource) -> ParsedDocument:
        if source.retrieval_status.value not in {"success", "partial"}:
            return ParsedDocument(
                source_id=source.source_id,
                parsing_status=ParsingStatus.FAILED,
                notes="Skipped parse because retrieval failed",
            )

        raw: bytes | None = None
        if source.storage_key:
            try:
                raw = await self.file_store.get(source.storage_key)
            except Exception as exc:
                return ParsedDocument(
                    source_id=source.source_id,
                    parsing_status=ParsingStatus.FAILED,
                    notes=f"Could not load storage key: {exc}",
                )

        if source.source_type == SourceType.OPENFDA and source.raw_text:
            return self._parse_openfda(source)

        if source.storage_key and source.storage_key.endswith(".pdf"):
            return await self._parse_pdf(source, raw or b"")

        # Prefer full bytes from FileStore over in-memory raw_text (often truncated at retrieve)
        text = None
        if raw:
            text = raw.decode("utf-8", errors="ignore")
        elif source.raw_text:
            text = source.raw_text
        if not text:
            return ParsedDocument(
                source_id=source.source_id,
                parsing_status=ParsingStatus.FAILED,
                notes="No text available",
            )
        return self._parse_html(source, text)

    def _parse_openfda(self, source: RetrievedSource) -> ParsedDocument:
        try:
            data = json.loads(source.raw_text or "{}")
            blocks = [json.dumps(data.get("results", [])[:2], indent=2)]
            return ParsedDocument(
                source_id=source.source_id,
                text_blocks=blocks,
                page_or_section="openfda.results",
                parsing_status=ParsingStatus.SUCCESS,
            )
        except Exception as exc:
            return ParsedDocument(
                source_id=source.source_id,
                parsing_status=ParsingStatus.FAILED,
                notes=str(exc),
            )

    @staticmethod
    def _make_soup(markup: str) -> BeautifulSoup:
        """Parse SEC HTML or XBRL/XML without XMLParsedAsHTMLWarning.

        Modern EDGAR primary docs are often XML-wrapped HTML (Workiva XBRL).
        Use the XML parser when the payload declares XML; otherwise HTML/lxml.
        """
        head = markup.lstrip()[:256].lower()
        if head.startswith(("<?xml", "<xbrl", "<ix:")):
            return BeautifulSoup(markup, "lxml-xml")
        return BeautifulSoup(markup, "lxml")

    def _parse_html(self, source: RetrievedSource, html: str) -> ParsedDocument:
        soup = self._make_soup(html)
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        # chunk long filings (keep enough for multi-year MD&A + product tables)
        max_chars = 400_000
        chunks = [text[i : i + 12000] for i in range(0, min(len(text), max_chars), 12000)]
        grids = html_table_grids(soup)
        tables = [rows for grid in grids if (rows := flatten_grid(grid))]
        return ParsedDocument(
            source_id=source.source_id,
            text_blocks=chunks or [text[:12000]],
            tables=tables,
            table_grids=grids,
            page_or_section="html body",
            parsing_status=ParsingStatus.SUCCESS,
        )

    async def _parse_pdf(self, source: RetrievedSource, raw: bytes) -> ParsedDocument:
        try:
            import pdfplumber

            blocks, tables = pdf_tables(raw)
            if not blocks:
                _, status = await self.ocr.extract(raw)
                return ParsedDocument(
                    source_id=source.source_id,
                    parsing_status=status,
                    notes="No extractable text; OCR stubbed",
                )
            return ParsedDocument(
                source_id=source.source_id,
                text_blocks=blocks,
                tables=tables,
                page_or_section="pdf pages",
                parsing_status=ParsingStatus.SUCCESS,
            )
        except Exception as exc:
            return ParsedDocument(
                source_id=source.source_id,
                parsing_status=ParsingStatus.FAILED,
                notes=str(exc),
            )


def strip_html_noise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
