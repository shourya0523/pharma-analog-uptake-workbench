from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from app.domain.models import ParsedDocument, ParsingStatus, RetrievedSource, SourceType
from app.storage.filestore import FileStore


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

        text = source.raw_text
        if not text and raw:
            text = raw.decode("utf-8", errors="ignore")
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

    def _parse_html(self, source: RetrievedSource, html: str) -> ParsedDocument:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        # chunk long filings
        chunks = [text[i : i + 12000] for i in range(0, min(len(text), 240000), 12000)]
        tables: list[list[list[str]]] = []
        for table in soup.find_all("table")[:20]:
            rows = []
            for tr in table.find_all("tr")[:50]:
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if cells:
                    rows.append(cells)
            if rows:
                tables.append(rows)
        return ParsedDocument(
            source_id=source.source_id,
            text_blocks=chunks or [text[:12000]],
            tables=tables,
            page_or_section="html body",
            parsing_status=ParsingStatus.SUCCESS,
        )

    async def _parse_pdf(self, source: RetrievedSource, raw: bytes) -> ParsedDocument:
        try:
            import pdfplumber

            blocks: list[str] = []
            tables: list[list[list[str]]] = []
            with pdfplumber.open(__import__("io").BytesIO(raw)) as pdf:
                for i, page in enumerate(pdf.pages[:40]):
                    t = page.extract_text() or ""
                    if t.strip():
                        blocks.append(f"[page {i + 1}]\n{t}")
                    extracted = page.extract_tables() or []
                    for tbl in extracted[:5]:
                        clean = [[(c or "") for c in row] for row in tbl]
                        tables.append(clean)
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
