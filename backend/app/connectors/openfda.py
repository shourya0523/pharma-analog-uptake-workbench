from __future__ import annotations

import json
import logging

import httpx

from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
from app.storage.filestore import FileStore

logger = logging.getLogger(__name__)


# Where drugsFDA states a brand name. Both are brand-scoped: an application
# carries its brands in the `openfda` block that is built from its marketed
# NDC listings, and again in its own `products[]` array. An application with no
# marketed listing - an older or a discontinued one - has no `openfda` block at
# all and only the second path answers, so a brand query that names one path is
# a brand query that cannot see those products.
BRAND_SEARCH_PATHS = ("openfda.brand_name", "products.brand_name")

# The molecule, for context only, and on a path of its own so a caller can tell
# a molecule-wide answer from the product's own.
GENERIC_SEARCH_PATH = "openfda.generic_name"


def search_queries(brand: str, generic: str | None = None) -> list[tuple[str, str]]:
    """drugsFDA searches to try, in order, as (match_scope, search expression).

    Brand name is queried on its own first, on every path that states one,
    because those are the only queries whose every result is the requested
    product. A brand-OR-generic query returns every application for the
    molecule - the generic filers' and the competitors' brands that share it -
    and openFDA documents no result order, so there is no position at which the
    requested product can be relied on to appear. It may not be in the returned
    window at all.

    The generic query is therefore a fallback for molecule context only, and
    carries its own ``match_scope`` so a caller cannot mistake a molecule-wide
    field for the product's own.
    """
    queries = []
    if brand and brand.strip():
        queries += [(f"brand:{path}", f'{path}:"{brand.strip()}"') for path in BRAND_SEARCH_PATHS]
    if generic and generic.strip():
        queries.append(("generic", f'{GENERIC_SEARCH_PATH}:"{generic.strip()}"'))
    return queries


class OpenFDAConnector:
    BASE = "https://api.fda.gov/drug/drugsfda.json"
    LABEL_BASE = "https://api.fda.gov/drug/label.json"
    LIMIT = 10

    def __init__(self, file_store: FileStore) -> None:
        self.file_store = file_store

    async def _search(self, client, scope: str, search: str, *, brand: str) -> list[dict]:
        """One drugsFDA search, or an empty list when it matched nothing."""
        resp = await client.get(f"{self.BASE}?search={search}&limit={self.LIMIT}")
        if resp.status_code == 404:
            logger.info("openfda_no_match scope=%s brand=%s", scope, brand)
            return []
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if results:
            logger.info("openfda_retrieved scope=%s brand=%s results=%s", scope, brand, len(results))
        return results

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
            # Every brand path is asked and their answers are unioned: a brand
            # query returns only that brand, and the paths do not return the
            # same applications - one path can hold an application the other
            # does not, so stopping at the first path that answers is a brand
            # query that cannot see the rest. The molecule query stays a
            # fallback for when no brand path answered at all, never an
            # addition, because its results are the whole molecule's.
            merged: dict[str, dict] = {}
            scopes: list[str] = []
            searches: list[str] = []

            async def collect(client, scope: str, search: str) -> int:
                results = await self._search(client, scope, search, brand=brand)
                for index, result in enumerate(results):
                    key = str(result.get("application_number") or f"{scope}:{index}")
                    merged.setdefault(key, result)
                if results:
                    scopes.append(scope)
                    searches.append(search)
                return len(results)

            brand_queries = [pair for pair in queries if pair[0].startswith("brand")]
            molecule_queries = [pair for pair in queries if not pair[0].startswith("brand")]
            async with httpx.AsyncClient(timeout=30) as client:
                for scope, search in brand_queries:
                    await collect(client, scope, search)
                if not merged:
                    for scope, search in molecule_queries:
                        if await collect(client, scope, search):
                            break
                if merged:
                    url = f"{self.BASE}?search={searches[0]}&limit={self.LIMIT}"
                    data = {"results": list(merged.values())}
                    match_scope = " ".join(scopes)
                    matched_search = " ".join(searches)

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
                        None
                        if match_scope.startswith("brand")
                        else "openfda_generic_fallback"
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
