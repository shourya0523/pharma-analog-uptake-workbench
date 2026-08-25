from __future__ import annotations

import re
from typing import Any

from app.config import get_settings
from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
from app.llm.client import LLMModules
from app.storage.filestore import FileStore


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
    """OpenRouter native web_search / web_fetch fallback evidence."""

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
        sources_raw = payload.get("sources") or []
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
            storage_key = None
            if excerpt:
                key = f"sources/{run_id}/{job_id}/{sid}.txt"
                await self.file_store.put(key, excerpt.encode("utf-8"), "text/plain")
                storage_key = key
            sources.append(
                RetrievedSource(
                    source_id=sid,
                    source_type=SourceType.LLM_SEARCH,
                    url=url,
                    title=hit.get("title") or url,
                    raw_text=excerpt[:500_000] if excerpt else None,
                    storage_key=storage_key,
                    retrieval_status=RetrievalStatus.SUCCESS if excerpt else RetrievalStatus.PARTIAL,
                    metadata={
                        "search_query": hit.get("query"),
                        "search_snippet": hit.get("snippet") or excerpt[:500],
                        "search_purpose": hit.get("purpose") or goal,
                        "openrouter_web": True,
                    },
                    notes="openrouter_web_search_fetch",
                )
            )
        return sources
