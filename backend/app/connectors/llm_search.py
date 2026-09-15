from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from app.config import get_settings
from app.connectors.sources import fetch_page
from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
from app.llm.client import LLMModules, listed
from app.quality.sentences import sentences
from app.storage.filestore import FileStore

logger = logging.getLogger(__name__)


def _normalize_results(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw = payload.get("results") or payload.get("search_results") or payload.get("_citations") or []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "url": url,
                "title": (item.get("title") or url).strip(),
                "snippet": (item.get("snippet") or item.get("excerpt") or item.get("content") or "").strip(),
                "query": (item.get("query") or "").strip(),
                "purpose": (item.get("purpose") or "").strip(),
            }
        )
    return out


class LLMSearchConnector:
    """OpenRouter web_search / web_fetch fallback evidence."""

    def __init__(self, file_store: FileStore, llm: LLMModules | None = None) -> None:
        self.file_store = file_store
        self.llm = llm or LLMModules()
        self.settings = get_settings()

    async def search_snippets(
        self,
        *,
        goal: str,
        product: str,
        aliases: list[str],
        manufacturer: str | None,
        ticker: str | None,
        context: str = "",
    ) -> list[dict[str, str]]:
        if not self.settings.enable_llm_search:
            return []
        payload = await self.llm.web_search(
            goal=goal,
            product=product,
            aliases=aliases,
            manufacturer=manufacturer,
            ticker=ticker,
            context=context,
        )
        results = _normalize_results(payload)
        return results[: self.settings.llm_search_max_urls]

    async def resolve_cik_from_search(
        self,
        *,
        product: str,
        manufacturer: str | None,
        ticker: str | None,
        aliases: list[str],
    ) -> str | None:
        if not self.settings.enable_llm_search:
            return None
        result = await self.llm.resolve_cik_via_search(
            product=product,
            aliases=aliases,
            manufacturer=manufacturer,
            ticker=ticker,
        )
        cik = (result.get("cik") or "").strip()
        if cik and re.fullmatch(r"\d{1,10}", cik):
            return cik.zfill(10)
        return None

    async def fallback_retrieve(
        self,
        *,
        run_id: str,
        job_id: str,
        goal: str,
        product: str,
        aliases: list[str],
        manufacturer: str | None,
        ticker: str | None,
        context: str = "",
    ) -> list[RetrievedSource]:
        if not self.settings.enable_llm_search:
            return []
        payload = await self.llm.web_search_and_fetch(
            goal=goal,
            product=product,
            aliases=aliases,
            manufacturer=manufacturer,
            ticker=ticker,
            context=context,
            max_sources=self.settings.llm_search_max_urls,
        )
        sources_raw = listed(payload, "sources")
        if not sources_raw:
            # Fall back to search-only snippets as thin sources
            sources_raw = _normalize_results(payload)
            for item in sources_raw:
                item.setdefault("excerpt", item.get("snippet") or "")

        sources: list[RetrievedSource] = []
        for hit in sources_raw[: self.settings.llm_search_max_urls]:
            if not isinstance(hit, dict):
                continue
            url = (hit.get("url") or "").strip()
            if not url:
                continue
            excerpt = (hit.get("excerpt") or hit.get("snippet") or hit.get("content") or "").strip()
            sid = new_id()
            # The page is fetched here rather than taken from the model. What
            # the model returns is a pointer and a claim about what is on the
            # page; the document is what a figure is cited against, and until
            # we hold it the quote check is checking the claim against itself.
            try:
                content, content_type, storage_key = await fetch_page(
                    self.file_store, run_id=run_id, job_id=job_id, source_id=sid, url=url,
                    user_agent=self.settings.sec_user_agent,
                )
            except Exception as exc:  # noqa: BLE001 - any failure leaves us without the page
                logger.info("search_page_unfetchable url=%s error=%s", url, exc)
                sources.append(
                    RetrievedSource(
                        source_id=sid,
                        source_type=SourceType.LLM_SEARCH,
                        url=url,
                        title=hit.get("title") or url,
                        retrieval_status=RetrievalStatus.FAILED,
                        metadata={
                            "search_query": hit.get("query"),
                            "search_snippet": excerpt[:500],
                            "search_purpose": hit.get("purpose") or goal,
                            "openrouter_web": True,
                            "excerpt_found_in_page": False,
                        },
                        notes=f"openrouter_web_search; page could not be fetched: {exc}",
                    )
                )
                continue
            found = excerpt_is_on_the_page(excerpt, content, content_type)
            sources.append(
                RetrievedSource(
                    source_id=sid,
                    source_type=SourceType.LLM_SEARCH,
                    url=url,
                    title=hit.get("title") or url,
                    storage_key=storage_key,
                    retrieval_status=RetrievalStatus.SUCCESS,
                    metadata={
                        "search_query": hit.get("query"),
                        "search_snippet": excerpt[:500],
                        "search_purpose": hit.get("purpose") or goal,
                        "openrouter_web": True,
                        "content_type": content_type,
                        # Whether the passage the model reported is in the page
                        # we fetched. The readers read the page either way; this
                        # says whether the model described it or invented it.
                        "excerpt_found_in_page": found,
                    },
                    notes="openrouter_web_search"
                    + ("" if found else "; the reported passage is not in the page"),
                )
            )
        return sources


def excerpt_is_on_the_page(excerpt: str, content: bytes, content_type: str) -> bool:
    """Whether the passage a search reported is in the page we fetched.

    Compared on visible text with whitespace collapsed, because a page prints
    the same sentence across tags and lines. A long excerpt is accepted when
    its longest sentence is there: a model asked for a passage returns one
    joined from neighbouring lines more often than it returns a fabrication,
    and the sentence carrying the figure is the part a citation rests on.
    """
    if not excerpt:
        return False
    if "pdf" in (content_type or "").lower():
        return False
    text = _visible(content)
    if not text:
        return False
    needle = _flat(excerpt)
    if needle and needle in text:
        return True
    longest = max((_flat(s) for s in sentences(excerpt)), key=len, default="")
    return bool(longest) and len(longest) >= 40 and longest in text


def _flat(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def _visible(content: bytes) -> str:
    markup = content.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(markup, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return _flat(soup.get_text(" "))
