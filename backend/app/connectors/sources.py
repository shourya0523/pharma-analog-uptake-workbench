from __future__ import annotations

# ruff: noqa: BLE001, RUF012, SIM113
import logging
from datetime import date
from typing import Any

import httpx

from app.config import get_settings
from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
from app.storage.filestore import FileStore

logger = logging.getLogger(__name__)


# Shared across connector instances so concurrent jobs don't stampede EDGAR
_last_sec_request = 0.0


def parse_filing_date(value: object) -> date | None:
    """Lenient ISO date parse for EDGAR filingDate values and caller-supplied bounds."""
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class ManualURLConnector:
    def __init__(self, file_store: FileStore) -> None:
        self.file_store = file_store
        self.settings = get_settings()

    async def retrieve(self, *, run_id: str, job_id: str, url: str) -> list[RetrievedSource]:
        sid = new_id()
        if not url:
            return []
        headers: dict[str, str] = {"User-Agent": self.settings.sec_user_agent}
        if "sec.gov" in url.lower():
            headers["Accept-Encoding"] = "gzip, deflate"
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers=headers) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "text/html")
                ext = "pdf" if "pdf" in content_type or url.lower().endswith(".pdf") else "html"
                key = f"sources/{run_id}/{job_id}/{sid}.{ext}"
                await self.file_store.put(key, resp.content, content_type)
                text = resp.text if ext == "html" else None
                return [
                    RetrievedSource(
                        source_id=sid,
                        source_type=SourceType.USER_URL,
                        url=url,
                        title=url,
                        raw_text=None if key else (text[:500_000] if text else None),
                        storage_key=key,
                        retrieval_status=RetrievalStatus.SUCCESS,
                        metadata={"content_type": content_type},
                    )
                ]
        except Exception as exc:
            return [
                RetrievedSource(
                    source_id=sid,
                    source_type=SourceType.USER_URL,
                    url=url,
                    title=url,
                    retrieval_status=RetrievalStatus.FAILED,
                    notes=str(exc),
                )
            ]


class TranscriptConnectorStub:
    async def retrieve(self, **_: Any) -> list[RetrievedSource]:
        return [
            RetrievedSource(
                source_type=SourceType.TRANSCRIPT,
                url="stub://transcripts",
                title="Earnings call transcripts",
                retrieval_status=RetrievalStatus.NOT_CONFIGURED,
                notes="Transcript connector stubbed in v1",
            )
        ]
