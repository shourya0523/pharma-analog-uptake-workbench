from __future__ import annotations

import json
import logging

import httpx

from app.domain.models import RetrievedSource, RetrievalStatus, SourceType, new_id
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
                    raw_bytes = json.dumps(data).encode()
                    key = f"sources/{run_id}/{job_id}/{sid}.json"
                    await self.file_store.put(key, raw_bytes, "application/json")
                    logger.info(
                        "openfda_retrieved scope=%s brand=%s results=%s",
                        scope,
                        brand,
                        len(results),
                    )
                    return [
                        RetrievedSource(
                            source_id=sid,
                            source_type=SourceType.OPENFDA,
                            url=url,
                            title=f"OpenFDA: {brand}",
                            raw_text=raw_bytes.decode(),
                            storage_key=key,
                            retrieval_status=RetrievalStatus.SUCCESS,
                            metadata={
                                "results": results,
                                "match_scope": scope,
                                "search": search,
                            },
                            notes=None if scope == "brand" else "openfda_generic_fallback",
                        )
                    ]
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
        except Exception as exc:
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
