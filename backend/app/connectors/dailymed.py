from __future__ import annotations

import httpx

from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
from app.storage.filestore import FileStore


class DailyMedConnector:
    """Retrieve SPL XML only when a stable DailyMed set identifier is known."""

    BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls"

    def __init__(self, file_store: FileStore) -> None:
        self.file_store = file_store

    async def retrieve(
        self, *, run_id: str, job_id: str, spl_set_id: str | None
    ) -> list[RetrievedSource]:
        if not spl_set_id:
            return []
        source_id = new_id()
        url = f"{self.BASE}/{spl_set_id}.xml"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url)
                response.raise_for_status()
            key = f"sources/{run_id}/{job_id}/{source_id}-spl.xml"
            await self.file_store.put(key, response.content, "application/xml")
            return [
                RetrievedSource(
                    source_id=source_id,
                    source_type=SourceType.DAILYMED,
                    url=url,
                    title=f"DailyMed SPL {spl_set_id}",
                    raw_text=response.text,
                    storage_key=key,
                    retrieval_status=RetrievalStatus.SUCCESS,
                    metadata={"spl_set_id": spl_set_id, "matched_by": "spl_set_id"},
                )
            ]
        except (httpx.HTTPError, OSError) as exc:
            return [
                RetrievedSource(
                    source_id=source_id,
                    source_type=SourceType.DAILYMED,
                    url=url,
                    title=f"DailyMed SPL {spl_set_id}",
                    retrieval_status=RetrievalStatus.FAILED,
                    notes=str(exc),
                )
            ]

