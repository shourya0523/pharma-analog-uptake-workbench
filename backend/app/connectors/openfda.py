from __future__ import annotations

import json

import httpx

from app.domain.models import RetrievedSource, RetrievalStatus, SourceType, new_id
from app.storage.filestore import FileStore


class OpenFDAConnector:
    BASE = "https://api.fda.gov/drug/drugsfda.json"

    def __init__(self, file_store: FileStore) -> None:
        self.file_store = file_store

    async def retrieve(self, *, run_id: str, job_id: str, brand: str, generic: str | None = None) -> list[RetrievedSource]:
        query_parts = [f'openfda.brand_name:"{brand}"']
        if generic:
            query_parts.append(f'openfda.generic_name:"{generic}"')
        search = "+OR+".join(query_parts)
        url = f"{self.BASE}?search={search}&limit=5"
        sid = new_id()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                if resp.status_code == 404:
                    return [
                        RetrievedSource(
                            source_id=sid,
                            source_type=SourceType.OPENFDA,
                            url=url,
                            title="OpenFDA no match",
                            retrieval_status=RetrievalStatus.PARTIAL,
                            notes="No OpenFDA match",
                        )
                    ]
                resp.raise_for_status()
                data = resp.json()
            raw_bytes = json.dumps(data).encode()
            key = f"sources/{run_id}/{job_id}/{sid}.json"
            await self.file_store.put(key, raw_bytes, "application/json")
            return [
                RetrievedSource(
                    source_id=sid,
                    source_type=SourceType.OPENFDA,
                    url=url,
                    title=f"OpenFDA: {brand}",
                    raw_text=raw_bytes.decode(),
                    storage_key=key,
                    retrieval_status=RetrievalStatus.SUCCESS,
                    metadata={"results": data.get("results", [])[:3]},
                )
            ]
        except Exception as exc:
            return [
                RetrievedSource(
                    source_id=sid,
                    source_type=SourceType.OPENFDA,
                    url=url,
                    title=f"OpenFDA: {brand}",
                    retrieval_status=RetrievalStatus.FAILED,
                    notes=str(exc),
                )
            ]
