from __future__ import annotations

import json
import logging

import httpx

from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
from app.storage.filestore import FileStore

logger = logging.getLogger(__name__)


def search_queries(brand: str, generic: str | None = None) -> list[tuple[str, str]]:
    """drugsFDA searches to try, in order, as (match_scope, search expression).

    Brand name is queried on its own first. Searching brand OR generic together
    returns every application for the molecule ordered by application number, which
    can push the requested product out of the result window entirely - a search for
    Opsumit returns five macitentan ANDAs and no OPSUMIT, and one for Yutrepia
    returns Remodulin and Tyvaso. The generic query is only a fallback for molecule
    context, and is reported as such so its fields are not mistaken for the product's.
    """
    queries = []
    if brand and brand.strip():
        queries.append(("brand", f'openfda.brand_name:"{brand.strip()}"'))
    if generic and generic.strip():
        queries.append(("generic", f'openfda.generic_name:"{generic.strip()}"'))
    return queries


class OpenFDAConnector:
    BASE = "https://api.fda.gov/drug/drugsfda.json"
    LABEL_BASE = "https://api.fda.gov/drug/label.json"
    LIMIT = 10

    def __init__(self, file_store: FileStore) -> None:
        self.file_store = file_store

    async def retrieve(
        self, *, run_id: str, job_id: str, brand: str, generic: str | None = None
    ) -> list[RetrievedSource]:
        queries = search_queries(brand, generic)
        if not queries:
            return []
        sid = new_id()
        url = f"{self.BASE}?search={queries[0][1]}&limit={self.LIMIT}"
        try:
            data: dict | None = None
            match_scope: str | None = None
            matched_search: str | None = None
            async with httpx.AsyncClient(timeout=30) as client:
                for scope, search in queries:
                    url = f"{self.BASE}?search={search}&limit={self.LIMIT}"
                    resp = await client.get(url)
                    if resp.status_code == 404:
                        logger.info("openfda_no_match scope=%s brand=%s", scope, brand)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    results = data.get("results") or []
                    if not results:
                        continue
                    logger.info(
                        "openfda_retrieved scope=%s brand=%s results=%s",
                        scope,
                        brand,
                        len(results),
                    )
                    match_scope = scope
                    matched_search = search
                    break

                if data is None or match_scope is None:
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
                    metadata={
                        "results": data.get("results") or [],
                        "match_scope": match_scope,
                        "search": matched_search,
                    },
                    notes=(
                        None if match_scope == "brand" else "openfda_generic_fallback"
                    ),
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
                async with httpx.AsyncClient(timeout=30) as client:
                    label_resp = await client.get(label_url)
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
            logger.warning("openfda_failed brand=%s error=%s", brand, exc)
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
