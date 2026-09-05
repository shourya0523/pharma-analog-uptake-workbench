from __future__ import annotations

# ruff: noqa: BLE001
import json
import re

from bs4 import BeautifulSoup

from app.domain.models import ParsedDocument, ParsingStatus, RetrievedSource, SourceType
from app.parsing.grids import normalize_cells, parse_text_document, recover_text_grids
from app.storage.filestore import FileStore

_TEXT_SUFFIXES = (".md", ".markdown", ".txt", ".text")


class OCRStub:
    """OCR boundary for future scanned PDFs. Always returns unsupported in v1."""

    async def extract(self, data: bytes) -> tuple[str | None, ParsingStatus]:
        return None, ParsingStatus.UNSUPPORTED


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

        key = (source.storage_key or "").lower()
        if key.endswith(_TEXT_SUFFIXES) or (raw is not None and self._looks_like_text(raw)):
            text = raw.decode("utf-8", errors="ignore") if raw else (source.raw_text or "")
            return self._parse_text(source, text)

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
    def _looks_like_text(raw: bytes) -> bool:
        """Markdown or plain text rather than markup: no tags in the head."""
        head = raw[:4000].decode("utf-8", errors="ignore").lstrip().lower()
        if not head:
            return False
        if head.startswith(("<", "%pdf")):
            return False
        return "<html" not in head and "<table" not in head and "<div" not in head and "<p" not in head[:200]

    def _parse_text(self, source: RetrievedSource, text: str) -> ParsedDocument:
        """Markdown or plain text: pipe tables and flattened grids become tables."""
        blocks, tables = parse_text_document(text)
        if not blocks and not tables:
            return ParsedDocument(
                source_id=source.source_id,
                parsing_status=ParsingStatus.FAILED,
                notes="No text available",
            )
        max_chars = 400_000
        prose = "\n\n".join(blocks)
        chunks = [prose[i : i + 12000] for i in range(0, min(len(prose), max_chars), 12000)]
        return ParsedDocument(
            source_id=source.source_id,
            text_blocks=chunks or [prose[:12000]],
            tables=tables,
            page_or_section="text body",
            parsing_status=ParsingStatus.SUCCESS,
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
        tables: list[list[list[str]]] = []
        for table in soup.find_all("table"):
            rows = []
            for tr in table.find_all("tr"):
                cells = normalize_cells([c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])])
                if cells:
                    rows.append(cells)
            if rows:
                caption = self._caption_before(table)
                tables.append(([[caption]] if caption else []) + rows)
        return ParsedDocument(
            source_id=source.source_id,
            text_blocks=chunks or [text[:12000]],
            tables=tables,
            page_or_section="html body",
            parsing_status=ParsingStatus.SUCCESS,
        )

    @staticmethod
    def _caption_before(table, max_chars: int = 400) -> str | None:
        """The prose immediately above a table, where its unit is usually declared.

        Filings put "(in thousands)" in a sentence or a heading before the
        table rather than inside it; without that sentence a grid declares
        nothing about its numbers.
        """
        parts: list[str] = []
        node = table
        seen = 0
        while node is not None and seen < 6:
            node = node.find_previous(["p", "div", "span", "td", "b", "font", "h1", "h2", "h3", "h4"])
            if node is None:
                break
            if node.find("table") is not None or node.find_parent("table") is not None:
                seen += 1
                continue
            text = node.get_text(" ", strip=True)
            if text:
                parts.insert(0, text)
                if sum(len(p) for p in parts) >= max_chars:
                    break
            seen += 1
        unique: list[str] = []
        for part in parts:
            if not unique or part not in unique[-1]:
                unique.append(part)
        caption = " ".join(unique).strip()
        return caption[-max_chars:] if caption else None

    async def _parse_pdf(self, source: RetrievedSource, raw: bytes) -> ParsedDocument:
        try:
            import pdfplumber

            blocks: list[str] = []
            tables: list[list[list[str]]] = []
            with pdfplumber.open(__import__("io").BytesIO(raw)) as pdf:
                for i, page in enumerate(pdf.pages):
                    t = page.extract_text() or ""
                    if t.strip():
                        blocks.append(f"[page {i + 1}]\n{t}")
                    extracted = page.extract_tables() or []
                    for tbl in extracted:
                        clean = [normalize_cells([(c or "") for c in row]) for row in tbl]
                        clean = [row for row in clean if row]
                        if clean:
                            tables.append(clean)
            for block in blocks:
                tables.extend(recover_text_grids(block))
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
