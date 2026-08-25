from __future__ import annotations

import json

import httpx

from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
from app.storage.filestore import FileStore


class OpenFDAConnector:
    BASE = "https://api.fda.gov/drug/drugsfda.json"
    LABEL_BASE = "https://api.fda.gov/drug/label.json"

    def __init__(self, file_store: FileStore) -> None:
        self.file_store = file_store

    async def retrieve(
        self, *, run_id: str, job_id: str, brand: str, generic: str | None = None
    ) -> list[RetrievedSource]:
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
            sources = [
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
            application_numbers = sorted(
                {
                    str(result.get("application_number") or "").strip()
                    for result in data.get("results", [])
                    if result.get("application_number")
                }
            )
            if application_numbers:
                label_query = "+OR+".join(
                    f'openfda.application_number:"{number}"' for number in application_numbers[:5]
                )
                label_url = f"{self.LABEL_BASE}?search={label_query}&limit=10"
                label_sid = new_id()
                async with httpx.AsyncClient(timeout=30) as label_client:
                    label_resp = await label_client.get(label_url)
                if label_resp.status_code != 404:
                    label_resp.raise_for_status()
                    label_data = label_resp.json()
                    label_bytes = json.dumps(label_data).encode()
                    label_key = f"sources/{run_id}/{job_id}/{label_sid}-label.json"
                    await self.file_store.put(label_key, label_bytes, "application/json")
                    sources.append(
                        RetrievedSource(
                            source_id=label_sid,
                            source_type=SourceType.OPENFDA,
                            url=label_url,
                            title=f"OpenFDA Label: {brand}",
                            raw_text=label_bytes.decode(),
                            storage_key=label_key,
                            retrieval_status=RetrievalStatus.SUCCESS,
                            metadata={
                                "dataset": "drug_label",
                                "matched_by": "application_number",
                                "application_numbers": application_numbers,
                                "results": label_data.get("results", [])[:10],
                            },
                        )
                    )
            else:
                sources[0].notes = (
                    "Drugs@FDA matched, but no stable application number was available; "
                    "label name fallback was not attempted."
                )
            return sources
        except (httpx.HTTPError, OSError, ValueError) as exc:
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
