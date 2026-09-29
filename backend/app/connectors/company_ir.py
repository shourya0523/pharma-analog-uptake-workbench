"""Company-hosted investor-relations pages, found by search then filtered.

IR is a procedure: search for the issuer's own earnings and product-sales
pages, keep the ones that look like issuer documents, fetch them. It is not
a table of URLs. Delete this file and nothing produces an IR page from a
company name, which is why the file has to actually search.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from urllib.parse import urlparse

from app.config import get_settings
from app.connectors.llm_search import _normalize_results
from app.connectors.sources import fetch_page
from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
from app.llm.client import LLMModules
from app.parsing.periods import quarters_in_locator
from app.storage.filestore import FileStore

logger = logging.getLogger(__name__)

# Labels a company puts on the host that serves its own filings-to-investors.
# Derived from how those hosts are named (ir.example, investors.example), not
# from a catalogue of issuers. A news host does not carry them.
_IR_HOST_LABELS = frozenset({"ir", "investor", "investors"})

# Path tokens that, on an issuer host or beside a company name, name an
# earnings or financial document rather than a product page.
_IR_PATH_TOKENS = frozenset({
    "earnings", "financial", "financials", "investors", "investor", "ir",
    "press", "release", "releases", "results", "quarterly", "annual",
    "newsroom",
})


def looks_like_issuer_ir(url: str, title: str = "") -> bool:
    """Whether this locator is an issuer IR/earnings document, not a news hit.

    A host labelled ir/investor, or a path that names earnings/financials on
    a page whose title or path also names a document, is the shape a human
    follows from the company's own site. A third-party news URL is not.
    """
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    labels = {part for part in host.split(".") if part}
    if labels & _IR_HOST_LABELS:
        return True
    path = (parsed.path or "").lower()
    tokens = {part for part in path.replace("-", "/").replace("_", "/").split("/") if part}
    hay = f"{path} {(title or '').lower()}"
    if tokens & _IR_PATH_TOKENS and any(
        mark in hay for mark in ("htm", "pdf", "html", "earnings", "release", "results")
    ):
        return True
    return False


def _locator_answers(url: str, title: str, asked: set[str]) -> bool:
    """Keep a hit that names an asked quarter, or that names no quarter yet."""
    named = set(quarters_in_locator(title, url))
    if not named:
        return True
    return bool(named & asked)


class CompanyIRConnector:
    """Search, filter by fitness, fetch. No table of document URLs."""

    def __init__(self, file_store: FileStore, llm: LLMModules | None = None) -> None:
        self.file_store = file_store
        self.llm = llm or LLMModules()
        self.settings = get_settings()

    async def retrieve_for_issuer(
        self,
        *,
        run_id: str,
        job_id: str,
        company_name: str | None,
        ticker: str | None,
        product: str,
        aliases: list[str] | None = None,
        extra_urls: Iterable[str] | None = None,
        asked_quarters: Iterable[str] | None = None,
    ) -> list[RetrievedSource]:
        """IR pages for this issuer, searched then filtered, plus any extra URLs."""
        asked = {str(q) for q in (asked_quarters or ()) if q}
        hits = await self._discover(
            product=product,
            aliases=list(aliases or []),
            company_name=company_name,
            ticker=ticker,
        )
        kept: list[dict[str, str]] = []
        seen: set[str] = set()
        for hit in hits:
            url = (hit.get("url") or "").strip()
            if not url or url in seen:
                continue
            if not looks_like_issuer_ir(url, hit.get("title") or ""):
                logger.info("ir_hit_unfit url=%s title=%s", url, (hit.get("title") or "")[:80])
                continue
            if asked and not _locator_answers(url, hit.get("title") or "", asked):
                logger.info(
                    "ir_hit_outside_window url=%s title=%s asked=%s",
                    url, (hit.get("title") or "")[:80], sorted(asked),
                )
                continue
            seen.add(url)
            kept.append(hit)
        for url in extra_urls or ():
            url = (url or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            kept.append({"url": url, "title": url, "snippet": "", "query": "extra_url"})
        return await self._fetch(run_id=run_id, job_id=job_id, hits=kept)

    async def _discover(
        self,
        *,
        product: str,
        aliases: list[str],
        company_name: str | None,
        ticker: str | None,
    ) -> list[dict[str, str]]:
        if not self.settings.enable_llm_search:
            return []
        payload = await self.llm.web_search(
            goal="company_ir",
            product=product,
            aliases=aliases,
            manufacturer=company_name,
            ticker=ticker,
            context=(
                "Official investor-relations earnings releases, quarterly "
                "product net sales schedules, and historical sales PDFs on "
                "the issuer's own IR site. Not third-party news."
            ),
        )
        return _normalize_results(payload)[: self.settings.llm_search_max_urls]

    async def _fetch(
        self, *, run_id: str, job_id: str, hits: list[dict[str, str]]
    ) -> list[RetrievedSource]:
        sources: list[RetrievedSource] = []
        for hit in hits:
            url = (hit.get("url") or "").strip()
            if not url:
                continue
            sid = new_id()
            try:
                _content, content_type, storage_key = await fetch_page(
                    self.file_store,
                    run_id=run_id,
                    job_id=job_id,
                    source_id=sid,
                    url=url,
                    user_agent=self.settings.sec_user_agent,
                )
            except Exception as exc:  # noqa: BLE001
                logger.info("ir_page_unfetchable url=%s error=%s", url, exc)
                sources.append(
                    RetrievedSource(
                        source_id=sid,
                        source_type=SourceType.COMPANY_IR,
                        url=url,
                        title=hit.get("title") or url,
                        retrieval_status=RetrievalStatus.FAILED,
                        metadata={
                            "search_query": hit.get("query"),
                            "search_snippet": (hit.get("snippet") or "")[:500],
                            "content_type": None,
                        },
                        notes=f"company_ir; page could not be fetched: {exc}",
                    )
                )
                continue
            sources.append(
                RetrievedSource(
                    source_id=sid,
                    source_type=SourceType.COMPANY_IR,
                    url=url,
                    title=hit.get("title") or url,
                    storage_key=storage_key,
                    retrieval_status=RetrievalStatus.SUCCESS,
                    metadata={
                        "search_query": hit.get("query"),
                        "search_snippet": (hit.get("snippet") or "")[:500],
                        "content_type": content_type,
                    },
                    notes="company_ir",
                )
            )
        return sources
