from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.config import get_settings
from app.llm.grounding import (
    apply_structured_field_gates,
    enforce_verbatim_on_candidates,
    quote_is_verbatim,
)
from app.parsing.evidence import TOTAL_REVENUE_RE, product_aliases
from app.quality.candidate_filters import (
    quote_mentions_other_brand,
    quote_mentions_product,
)

logger = logging.getLogger(__name__)


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class OpenRouterClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _headers(self) -> dict[str, str]:
        if not self.settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        return {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/pharma-analog-uptake-workbench",
            "X-Title": "Pharma Analog Uptake Workbench",
        }

    def _raise_for_status(self, resp: httpx.Response, *, model: str, web: bool = False) -> None:
        if resp.is_error:
            logger.error(
                "openrouter_http_error status=%s model=%s web=%s body=%s",
                resp.status_code,
                model,
                web,
                (resp.text or "")[:500],
            )
        resp.raise_for_status()

    async def chat_json(self, *, model: str, system: str, user: str, max_tokens: int = 6000) -> dict[str, Any]:
        # max_tokens bounds what the router reserves against the account's
        # balance; without it a model's full output window is reserved.
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.settings.openrouter_base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            self._raise_for_status(resp, model=model)
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return _parse_json_content(content)

    def _web_tools(self, *, fetch: bool = False) -> list[dict[str, Any]]:
        domains = [
            d.strip()
            for d in (self.settings.llm_search_allowed_domains or "").split(",")
            if d.strip()
        ]
        search_params: dict[str, Any] = {
            "engine": self.settings.llm_search_engine or "auto",
            "max_results": min(max(self.settings.llm_search_max_urls, 1), 10),
            "max_total_results": min(max(self.settings.llm_search_max_urls * 2, 5), 20),
            "search_context_size": "medium",
        }
        if domains:
            search_params["allowed_domains"] = domains
        tools: list[dict[str, Any]] = [{"type": "openrouter:web_search", "parameters": search_params}]
        if fetch:
            fetch_params: dict[str, Any] = {
                "engine": "auto",
                "max_uses": min(max(self.settings.llm_search_max_urls, 1), 8),
                "max_content_tokens": 40000,
            }
            if domains:
                fetch_params["allowed_domains"] = domains
            tools.append({"type": "openrouter:web_fetch", "parameters": fetch_params})
        return tools

    async def chat_json_with_web(
        self,
        *,
        model: str,
        system: str,
        user: str,
        fetch: bool = False,
        timeout: float = 180,
    ) -> dict[str, Any]:
        """JSON chat with OpenRouter native web_search (+ optional web_fetch) server tools."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tools": self._web_tools(fetch=fetch),
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self.settings.openrouter_base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            self._raise_for_status(resp, model=model, web=True)
            data = resp.json()
        message = data["choices"][0]["message"]
        parsed = _parse_json_content(message.get("content") or "{}")
        citations = _citations_from_message(message)
        if citations and "search_results" not in parsed and "results" not in parsed:
            parsed["_citations"] = citations
        elif citations:
            parsed.setdefault("_citations", citations)
        return parsed


def load_prompt(name: str) -> dict[str, Any]:
    path = PROMPTS_DIR / f"{name}.yaml"
    with path.open() as f:
        return yaml.safe_load(f)


def _parse_json_content(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if not content:
        return {}
    text = content if isinstance(content, str) else str(content)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                repaired = _repair_json(match.group(0))
                if repaired is not None:
                    return repaired
        return {"raw": text}


def _repair_json(text: str) -> dict[str, Any] | None:
    """Recover JSON a model spoiled with an unescaped quote inside a string.

    Verbatim quotes copied from filings carry inch marks and nested quotes.
    Scanning the text as a string-aware tokenizer, a double quote inside a
    string that is not followed by a structural character is escaped; a
    trailing comma before a closing bracket is dropped.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                out.append(char)
                escaped = False
                continue
            if char == "\\":
                out.append(char)
                escaped = True
                continue
            if char == '"':
                rest = text[index + 1 :].lstrip()
                if rest[:1] in {",", "}", "]", ":"} or not rest:
                    in_string = False
                    out.append(char)
                else:
                    out.append('\\"')
                continue
            if char == "\n":
                out.append("\\n")
                continue
            out.append(char)
            continue
        if char == '"':
            in_string = True
        out.append(char)
    candidate = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _citations_from_message(message: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for ann in message.get("annotations") or []:
        if not isinstance(ann, dict):
            continue
        cite = ann.get("url_citation") or ann
        url = cite.get("url") or ""
        if not url:
            continue
        out.append(
            {
                "url": url,
                "title": cite.get("title") or url,
                "snippet": cite.get("content") or cite.get("snippet") or "",
            }
        )
    return out


def _filter_hallucinated_spans(spans: list[dict[str, Any]], source_text: str) -> list[dict[str, Any]]:
    good: list[dict[str, Any]] = []
    for i, span in enumerate(spans):
        text = (span.get("span_text") or "").strip()
        if not text:
            continue
        if not quote_is_verbatim(text, source_text, min_len=1):
            continue
        sid = span.get("span_id") or f"s{i + 1}"
        good.append({**span, "span_id": sid, "span_text": text})
    return good


class LLMModules:
    def __init__(self, client: OpenRouterClient | None = None) -> None:
        self.client = client or OpenRouterClient()
        self.settings = get_settings()

    async def find_revenue_spans(
        self,
        *,
        product: str,
        company: str | None,
        source_meta: dict,
        text: str,
    ) -> list[dict[str, Any]]:
        prompt = load_prompt("revenue_span_finder")
        clipped = text[:50000]
        if not self.settings.openrouter_api_key:
            return []
        user = prompt["user_template"].format(
            product=product,
            company=company or "",
            source_meta=json.dumps(source_meta),
            text=clipped,
        )
        result = await self.client.chat_json(
            model=self.settings.openrouter_model_extract,
            system=prompt["system"],
            user=user,
        )
        spans = result.get("spans") or []
        return _filter_hallucinated_spans(spans, clipped)

    async def extract_revenue_from_spans(
        self,
        *,
        product: str,
        company: str | None,
        source_meta: dict,
        spans: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not spans:
            return {"candidates": [], "spans": []}
        prompt = load_prompt("revenue_extractor")
        if not self.settings.openrouter_api_key:
            return {"candidates": [], "spans": spans, "note": "OPENROUTER_API_KEY missing; skipped LLM"}
        # Cap span payload
        compact = [
            {
                "span_id": s.get("span_id"),
                "span_text": (s.get("span_text") or "")[:4000],
                "why_relevant": s.get("why_relevant"),
                "looks_like_table": s.get("looks_like_table"),
            }
            for s in spans[:20]
        ]
        user = prompt["user_template"].format(
            product=product,
            company=company or "",
            source_meta=json.dumps(source_meta),
            spans_json=json.dumps(compact, indent=2)[:48000],
        )
        result = await self.client.chat_json(
            model=self.settings.openrouter_model_extract,
            system=prompt["system"],
            user=user,
        )
        candidates = result.get("candidates") or []
        # Grounding gates
        corpus = "\n\n".join(s.get("span_text") or "" for s in compact)
        kept_v, drop_v = enforce_verbatim_on_candidates(candidates, source_text=corpus, spans=compact)
        kept_s, drop_s = apply_structured_field_gates(kept_v)
        return {
            "candidates": kept_s,
            "spans": compact,
            "dropped": drop_v + drop_s,
        }

    async def extract_revenue(
        self,
        *,
        product: str,
        company: str | None,
        source_meta: dict,
        text: str,
    ) -> dict[str, Any]:
        """Two-pass extract: find verbatim spans, then fill candidates from spans only."""
        spans = await self.find_revenue_spans(
            product=product,
            company=company,
            source_meta=source_meta,
            text=text,
        )
        if not spans:
            return {"candidates": [], "spans": [], "note": "no_product_revenue_spans"}
        filled = await self.extract_revenue_from_spans(
            product=product,
            company=company,
            source_meta=source_meta,
            spans=spans,
        )
        return filled

    async def extract_metadata(self, *, product: str, text: str, source_meta: dict) -> dict[str, Any]:
        prompt = load_prompt("metadata_extractor")
        user = prompt["user_template"].format(
            product=product,
            source_meta=json.dumps(source_meta),
            text=text[:40000],
        )
        if not self.settings.openrouter_api_key:
            return {"fields": [], "note": "OPENROUTER_API_KEY missing; skipped LLM"}
        return await self.client.chat_json(
            model=self.settings.openrouter_model_extract,
            system=prompt["system"],
            user=user,
        )

    async def judge(
        self,
        *,
        product: str,
        candidate: dict,
        quote: str,
        context: str,
        generic: str | None = None,
        extra_aliases: list[str] | None = None,
    ) -> dict[str, Any]:
        prompt = load_prompt("evidence_judge")
        user = prompt["user_template"].format(
            product=product,
            candidate=json.dumps(candidate),
            quote=quote,
            context=context[:8000],
        )
        if not self.settings.openrouter_api_key:
            return {
                "validation_status": "needs_review",
                "support_classification": "unknown",
                "issues": ["LLM judge unavailable"],
                "explanation": "OPENROUTER_API_KEY missing",
            }
        result = await self.client.chat_json(
            model=self.settings.openrouter_model_judge,
            system=prompt["system"],
            user=user,
        )
        # Deterministic hard vetoes after judge
        return apply_judge_hard_vetoes(
            product=product,
            candidate=candidate,
            quote=quote,
            judgment=result,
            generic=generic,
            extra_aliases=extra_aliases,
        )

    async def judge_profile_field(
        self,
        *,
        product: str,
        generic: str | None,
        aliases: list[str] | None,
        field: str,
        value: str,
        source: dict[str, Any],
    ) -> dict[str, Any]:
        """Check one profile field against independent web search.

        Returns {} when search judging is unavailable, so callers keep the value
        they already have rather than treating silence as a contradiction.
        """
        if not self.settings.openrouter_api_key or not self.settings.enable_llm_search:
            return {}
        prompt = load_prompt("profile_field_judge")
        user = prompt["user_template"].format(
            product=product,
            generic=generic or "",
            aliases=json.dumps((aliases or [])[:20]),
            field=field,
            value=value,
            source=json.dumps(source)[:2000],
        )
        return await self.client.chat_json_with_web(
            model=self.settings.openrouter_model_judge,
            system=prompt["system"],
            user=user,
            fetch=True,
        )

    async def reconcile(self, *, product: str, candidates: list[dict]) -> dict[str, Any]:
        prompt = load_prompt("conflict_reconciler")
        user = prompt["user_template"].format(
            product=product,
            candidates=json.dumps(candidates)[:40000],
        )
        if not self.settings.openrouter_api_key:
            return {"resolved": [], "conflicts": []}
        return await self.client.chat_json(
            model=self.settings.openrouter_model_judge,
            system=prompt["system"],
            user=user,
        )

    async def completeness(
        self,
        *,
        product: str,
        profile: dict,
        datapoints: list[dict],
        unresolved: list[dict],
        timeline: dict | None = None,
    ) -> dict[str, Any]:
        prompt = load_prompt("completeness_analyzer")
        user = prompt["user_template"].format(
            product=product,
            profile=json.dumps(profile),
            timeline=json.dumps(timeline or {}),
            datapoints=json.dumps(datapoints)[:20000],
            unresolved=json.dumps(unresolved),
        )
        if not self.settings.openrouter_api_key:
            n = len(datapoints)
            u = len(unresolved)
            pct = round(100 * n / max(n + u, 1), 1)
            return {
                "completeness_pct": pct,
                "missing_periods": [x.get("period") for x in unresolved],
                "limitations": [],
                "recommended_next_steps": [],
            }
        return await self.client.chat_json(
            model=self.settings.openrouter_model_extract,
            system=prompt["system"],
            user=user,
        )

    async def expand_aliases(
        self,
        *,
        product: str,
        generic: str | None,
        manufacturer: str | None,
        ticker: str | None,
        indication: str | None = None,
    ) -> dict[str, Any]:
        prompt = load_prompt("alias_expander")
        if not self.settings.openrouter_api_key:
            return {"aliases": [], "parent_companies": [], "formulations": [], "search_terms": []}
        user = prompt["user_template"].format(
            product=product,
            generic=generic or "",
            manufacturer=manufacturer or "",
            ticker=ticker or "",
            indication=indication or "",
        )
        return await self.client.chat_json(
            model=self.settings.openrouter_model_extract,
            system=prompt["system"],
            user=user,
        )

    async def web_search(
        self,
        *,
        goal: str,
        product: str,
        aliases: list[str],
        manufacturer: str | None,
        ticker: str | None,
        context: str = "",
    ) -> dict[str, Any]:
        """Search via OpenRouter openrouter:web_search server tool."""
        prompt = load_prompt("search_planner")
        if not self.settings.openrouter_api_key or not self.settings.enable_llm_search:
            return {"results": []}
        user = prompt["user_template"].format(
            goal=goal,
            product=product,
            aliases=json.dumps(aliases[:20]),
            manufacturer=manufacturer or "",
            ticker=ticker or "",
            context=context[:4000],
        )
        return await self.client.chat_json_with_web(
            model=self.settings.openrouter_model_extract,
            system=prompt["system"],
            user=user,
            fetch=False,
        )

    async def web_search_and_fetch(
        self,
        *,
        goal: str,
        product: str,
        aliases: list[str],
        manufacturer: str | None,
        ticker: str | None,
        context: str = "",
        max_sources: int | None = None,
    ) -> dict[str, Any]:
        """Search + fetch pages via OpenRouter web_search and web_fetch tools."""
        prompt = load_prompt("search_fetch")
        if not self.settings.openrouter_api_key or not self.settings.enable_llm_search:
            return {"sources": []}
        user = prompt["user_template"].format(
            goal=goal,
            product=product,
            aliases=json.dumps(aliases[:20]),
            manufacturer=manufacturer or "",
            ticker=ticker or "",
            context=context[:4000],
            max_sources=max_sources or self.settings.llm_search_max_urls,
        )
        return await self.client.chat_json_with_web(
            model=self.settings.openrouter_model_extract,
            system=prompt["system"],
            user=user,
            fetch=True,
            timeout=240,
        )

    async def resolve_cik_via_search(
        self,
        *,
        product: str,
        aliases: list[str],
        manufacturer: str | None,
        ticker: str | None,
    ) -> dict[str, Any]:
        prompt = load_prompt("search_extract")
        if not self.settings.openrouter_api_key or not self.settings.enable_llm_search:
            return {}
        user = prompt["user_template"].format(
            product=product,
            aliases=json.dumps(aliases[:20]),
            manufacturer=manufacturer or "",
            ticker=ticker or "",
        )
        return await self.client.chat_json_with_web(
            model=self.settings.openrouter_model_extract,
            system=prompt["system"],
            user=user,
            fetch=False,
        )

    async def judge_with_search(
        self,
        *,
        product: str,
        aliases: list[str],
        candidate: dict,
        quote: str,
        context: str,
        search_snippets: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Judge with OpenRouter web_search; optional prefetched snippets as extra context."""
        prompt = load_prompt("judge_search_validator")
        if not self.settings.openrouter_api_key or not self.settings.enable_llm_search:
            return {}
        extra = ""
        if search_snippets:
            block = "\n\n".join(
                f"URL: {s.get('url')}\nTitle: {s.get('title')}\nSnippet: {s.get('snippet')}"
                for s in search_snippets[:10]
            )
            extra = f"\nPrefetched snippets (optional):\n{block[:8000]}\n"
        user = prompt["user_template"].format(
            product=product,
            aliases=json.dumps(aliases[:20]),
            candidate=json.dumps(candidate),
            quote=quote,
            context=(context[:4000] + extra),
        )
        result = await self.client.chat_json_with_web(
            model=self.settings.openrouter_model_judge,
            system=prompt["system"],
            user=user,
            fetch=False,
        )
        return apply_judge_hard_vetoes(
            product=product,
            candidate=candidate,
            quote=quote,
            judgment=result,
            generic=None,
            extra_aliases=aliases,
        )

    # Back-compat aliases used by older connector code paths
    async def plan_search(self, **kwargs: Any) -> dict[str, Any]:
        return await self.web_search(**kwargs)

    async def extract_from_search_snippets(
        self,
        *,
        task: str,
        product: str,
        manufacturer: str | None,
        ticker: str | None,
        snippets: list[dict[str, str]],
    ) -> dict[str, Any]:
        if task == "resolve_cik":
            return await self.resolve_cik_via_search(
                product=product,
                aliases=[],
                manufacturer=manufacturer,
                ticker=ticker,
            )
        return {"snippets": snippets}


def apply_judge_hard_vetoes(
    *,
    product: str,
    candidate: dict,
    quote: str,
    judgment: dict[str, Any],
    generic: str | None = None,
    extra_aliases: list[str] | None = None,
) -> dict[str, Any]:
    """Force misclassified/needs_review for known bad patterns even if model is soft."""
    issues = list(judgment.get("issues") or [])
    q = quote or ""
    period_type = (candidate.get("period_type") or "").lower()
    mentions = quote_mentions_product(q, product, generic, extra_aliases=extra_aliases)
    other = quote_mentions_other_brand(q, product, generic, extra_aliases=extra_aliases)
    veto = False

    if TOTAL_REVENUE_RE.search(q) and not mentions:
        issues.append("hard_veto:company_total_without_product")
        veto = True
    if other and not mentions:
        issues.append(f"hard_veto:other_brand:{other}")
        veto = True
    if period_type == "quarterly" and re_ytd_language(q):
        issues.append("hard_veto:ytd_language_as_quarterly")
        veto = True
    if not mentions and (candidate.get("revenue_scope") or "") not in {"Company total", ""}:
        aliases = product_aliases(product, generic, extra=extra_aliases)
        if aliases:
            issues.append("hard_veto:product_missing_from_quote")
            veto = True

    if veto:
        judgment = {
            **judgment,
            "support_classification": "misclassified",
            "validation_status": "needs_review",
            "issues": issues,
        }
    return judgment


def re_ytd_language(quote: str) -> bool:
    return bool(
        re.search(
            r"\b(six\s+months?\s+ended|nine\s+months?\s+ended|year[\s-]to[\s-]date|\bYTD\b|year\s+ended)\b",
            quote or "",
            re.IGNORECASE,
        )
    )