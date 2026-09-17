from __future__ import annotations

import json
import logging

import httpx

from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
from app.parsing.fda_label import brand_name_paths
from app.storage.filestore import FileStore

logger = logging.getLogger(__name__)


# Where drugsFDA states a brand name. Both are brand-scoped: an application
# carries its brands in the `openfda` block that is built from its marketed
# NDC listings, and again in its own `products[]` array. An application with no
# marketed listing - an older or a discontinued one - has no `openfda` block at
# all and only the second path answers, so a brand query that names one path is
# a brand query that cannot see those products.
#
# The same paths the record reader reads, in query syntax: the search grammar
# names an array member without the `[]` the read syntax uses. Derived, so a
# path added to the reader is a path this asks on.
BRAND_SEARCH_PATHS = tuple(path.replace("[]", "") for path in brand_name_paths())

# The molecule, for context only, and on a path of its own so a caller can tell
# a molecule-wide answer from the product's own.
GENERIC_SEARCH_PATH = "openfda.generic_name"

# What a search matched on. The brand answer is the product's own; the molecule
# answer is the whole molecule's, and a caller that cannot tell them apart
# reads a competitor's application as this product's.
BRAND_SCOPE = "brand"
GENERIC_SCOPE = "generic"


def search_queries(brand: str, generic: str | None = None) -> list[tuple[str, str]]:
    """drugsFDA searches to try, in order, as (match_scope, search expression).

    One brand query names every path that states a brand, joined by openFDA's
    own ``OR``: an application carries its brands in the ``openfda`` block and
    again in its own ``products[]`` array, and one path can hold an application
    the other does not, so a brand query naming one path is a brand query that
    cannot see the rest. The server unions them, which is what asking each path
    in turn was doing by hand.

    Brand comes first because a brand query's every result is the requested
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
        term = brand.strip()
        queries.append(
            (BRAND_SCOPE, "+OR+".join(f'{path}:"{term}"' for path in BRAND_SEARCH_PATHS))
        )
    if generic and generic.strip():
        queries.append((GENERIC_SCOPE, f'{GENERIC_SEARCH_PATH}:"{generic.strip()}"'))
    return queries


class OpenFDAConnector:
    BASE = "https://api.fda.gov/drug/drugsfda.json"
    LABEL_BASE = "https://api.fda.gov/drug/label.json"
    # The result window one search returns. It bounds the brand answer as a
    # whole, because the paths are unioned by the server rather than asked one
    # at a time - so it has to be wide enough for every application a brand has
    # on either path, not for one path's share of them.
    LIMIT = 20

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
            # The queries are tried in order and the first that answers is the
            # answer. The molecule query is the last of them, so it is reached
            # only when the brand query found nothing at all - its results are
            # the whole molecule's, and added to a brand answer they would
            # widen it.
            async with httpx.AsyncClient(timeout=30) as client:
                for scope, search in queries:
                    results = await self._search(client, scope, search, brand=brand)
                    if results:
                        url = f"{self.BASE}?search={search}&limit={self.LIMIT}"
                        data = {"results": results}
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
                    notes=None if match_scope == BRAND_SCOPE else "openfda_generic_fallback",
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
