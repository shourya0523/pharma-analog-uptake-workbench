from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.config import get_settings
from app.domain.claims import stated_text
from app.extraction.prose import periods_named_in, spans_named_in
from app.llm.grounding import (
    apply_structured_field_gates,
    enforce_verbatim_on_candidates,
    quote_is_verbatim,
)
from app.parsing.periods import MONTHS_TO_PERIOD_TYPE, period_key
from app.parsing.evidence import (
    NON_PRODUCT_REVENUE_RE,
    TOTAL_REVENUE_RE,
    product_aliases,
)
from app.quality.candidate_filters import (
    quote_mentions_other_brand,
    quote_mentions_product,
)
from app.quality.sentences import (
    sentence_carrying,
    sentences,
    states_a_change_not_a_level,
)

logger = logging.getLogger(__name__)


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class OpenRouterClient:
    # How many times a connection to the model is tried before the question
    # is treated as unanswered.
    TRANSPORT_ATTEMPTS = 3
    # Statuses that say "not now" rather than "not ever": the gateway is busy
    # or the upstream timed out. They are the same event as a dropped
    # connection, arriving with a status line instead of without one, so they
    # are retried and then treated as an unanswered question - not raised,
    # which ended the whole job.
    RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524})

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

    async def chat_json(self, *, model: str, system: str, user: str) -> dict[str, Any]:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }
        data = await self._post(payload, model=model, timeout=120)
        if not data:
            return {}
        content = data["choices"][0]["message"]["content"]
        return _parse_json_content(content)

    async def _post(
        self, payload: dict[str, Any], *, model: str, timeout: float, web: bool = False
    ) -> dict[str, Any] | None:
        """One completion request; None when the model could not be reached.

        The model sits behind a network, and a connection that fails or times
        out is that one question going unanswered - the same outcome as the
        model having nothing to say, which every caller already handles as an
        empty answer. It is retried, because the endpoint is flaky in bursts;
        it is not raised, because one blip would otherwise end a job that
        already holds every figure the other readers found.
        """
        delay = 2.0
        for attempt in range(self.TRANSPORT_ATTEMPTS):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        f"{self.settings.openrouter_base_url}/chat/completions",
                        headers=self._headers(),
                        json=payload,
                    )
                    self._raise_for_status(resp, model=model, web=web)
                    return resp.json()
            except httpx.TransportError as exc:
                logger.warning("openrouter_unreachable attempt=%d/%d model=%s error=%s: %s",
                               attempt + 1, self.TRANSPORT_ATTEMPTS, model,
                               type(exc).__name__, exc)
                if attempt == self.TRANSPORT_ATTEMPTS - 1:
                    return None
                await asyncio.sleep(delay)
                delay *= 2
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in self.RETRYABLE_STATUSES:
                    raise
                logger.warning("openrouter_busy attempt=%d/%d model=%s status=%s",
                               attempt + 1, self.TRANSPORT_ATTEMPTS, model,
                               exc.response.status_code)
                if attempt == self.TRANSPORT_ATTEMPTS - 1:
                    return None
                await asyncio.sleep(delay)
                delay *= 2
        return None

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
        data = await self._post(payload, model=model, timeout=timeout, web=True)
        if not data:
            return {}
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


# Where a reply that should have been an object arrives as a bare array, the
# array is kept under this key: the model answered the question and left off
# the name, and `listed` reads it back for whichever name the caller expects.
BARE_LIST = "_bare_list"


def _parse_json_content(content: Any) -> dict[str, Any]:
    """A model reply as a mapping, whatever shape it arrived in.

    The return type was a promise the parse did not keep: asked for
    ``{"spans": [...]}`` a model sometimes answers ``[...]``, and the caller's
    ``.get`` raised on the list, which killed the job mid-pipeline rather than
    costing it one answer.
    """
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        return {BARE_LIST: content}
    if not content:
        return {}
    text = content if isinstance(content, str) else str(content)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    parsed: Any
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"[\[{][\s\S]*[\]}]", text)
        if not match:
            return {"raw": text}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {"raw": text}
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {BARE_LIST: parsed}
    return {"raw": text}


def listed(payload: dict[str, Any], key: str) -> list[Any]:
    """The list a reply was asked for, keyed or bare."""
    value = payload.get(key)
    if isinstance(value, list):
        return value
    bare = payload.get(BARE_LIST)
    return bare if isinstance(bare, list) else []


def mappings(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """The objects a reply was asked for, without the entries that are not objects.

    `listed` returns the entries as they arrived, which is right for a list of
    strings - aliases, formulations - and wrong wherever the caller goes on to
    call `.get` on each entry. Two things put a non-object there. A model asked
    for objects answers with strings; and a reply that is a bare array is
    returned under every key, so a list of strings meant for one question is
    also what the next `listed` call hands back.

    An entry that is not an object cannot carry the fields such a caller reads,
    so it is dropped rather than coerced: a string is a whole span, but it is
    not a period, a value and a quote.
    """
    return [item for item in listed(payload, key) if isinstance(item, dict)]


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


def _filter_hallucinated_spans(spans: list[Any], source_text: str) -> list[dict[str, Any]]:
    """Spans the source actually contains, from whatever shape the model sent.

    A span is meant to be an object carrying `span_text`, and every reader
    downstream calls `.get` on it. A model sometimes sends bare strings, and
    the orchestrator does not guard its `extract_revenue` call, so one reply of
    that shape fails the whole job rather than the single source it came from.

    A string is a span with no id and no rationale, which is all the verbatim
    check needs, so it is read as one rather than discarded.
    """
    good: list[dict[str, Any]] = []
    for i, raw in enumerate(spans or []):
        span: dict[str, Any] = raw if isinstance(raw, dict) else {"span_text": str(raw or "")}
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
        spans = listed(result, "spans")
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
        candidates = mappings(result, "candidates")
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
        peer_names: Iterable[str] | None = None,
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
            peer_names=peer_names,
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

    async def resolve_xbrl_member(
        self,
        *,
        issuer: str,
        member: str,
        siblings: list[str],
        candidates: list[str],
    ) -> dict[str, Any]:
        """Which product an axis member names, when the string rules cannot say.

        Asked once per member, and the answer is written to the register, so
        this is not in the path of reading a filing.
        """
        if not self.settings.openrouter_api_key or not candidates:
            return {}
        prompt = load_prompt("xbrl_member_resolver")
        user = prompt["user_template"].format(
            issuer=issuer,
            member=member,
            siblings="\n".join(f"  - {s}" for s in sorted(siblings)[:40]) or "  (none)",
            candidates="\n".join(f"  - {c}" for c in sorted(candidates)),
        )
        result = await self.client.chat_json(
            model=self.settings.openrouter_model_judge,
            system=prompt["system"],
            user=user,
        )
        product = result.get("product")
        # A model that invents a product outside the list is answering a
        # different question; the answer is dropped rather than repaired.
        if product and product not in candidates:
            return {"product": None, "reason": f"model returned {product!r}, not a candidate",
                    "confidence": 0.0}
        return result

    async def judge_element(self, *, element: str, examples: list[str]) -> dict[str, Any]:
        """Whether an element the filing's linkbase left unplaced measures revenue.

        Empty when no key is set or the model will not commit; an element
        without a verdict is left out of the reading rather than guessed at.
        """
        if not self.settings.openrouter_api_key:
            return {}
        prompt = load_prompt("xbrl_element_judge")
        prefix = element.split(":")[0] if ":" in element else ""
        user = prompt["user_template"].format(
            element=element, prefix=prefix,
            examples="\n".join(f"  - {e}" for e in examples[:12]) or "  (none)",
        )
        result = await self.client.chat_json(
            model=self.settings.openrouter_model_judge,
            system=prompt["system"], user=user,
        )
        verdict = result.get("is_revenue")
        if verdict not in (True, False):
            return {}
        return {"is_revenue": bool(verdict),
                "confidence": float(result.get("confidence") or 0.0),
                "reason": str(result.get("reason") or "")}

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
        peer_names: Iterable[str] | None = None,
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
            peer_names=peer_names,
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


# A period that is part of a year without being a quarter of it is what
# "year to date" names. Both halves come from the period grammar's own map of
# spans to period types, so a span the grammar learns to read is covered here
# without being written down twice.
_YEAR_TO_DATE_SPANS = frozenset(
    months
    for months, period_type in MONTHS_TO_PERIOD_TYPE.items()
    if period_type not in {"quarterly", "annual"}
)
_QUARTER_SPAN = next(
    months for months, period_type in MONTHS_TO_PERIOD_TYPE.items() if period_type == "quarterly"
)
_SPAN_OF_PERIOD_TYPE = {
    period_type: months for months, period_type in MONTHS_TO_PERIOD_TYPE.items()
}


def _period_claimed_by(candidate: dict) -> str | None:
    """The period a candidate claims, as a key in the grammar's namespace.

    A row states its period twice - as a label and as a period type - and the
    two can be written in different namespaces: a nine-month figure carries the
    label `2024`, which is how an annual one is written, and comparing that
    against what a quote names asks a question the row never answered. The
    label is re-keyed for the span the row declares, and a row declaring a span
    the grammar has no name for has not claimed a period at all.
    """
    label = str(candidate.get("period") or "")
    months = _SPAN_OF_PERIOD_TYPE.get(stated_text(candidate.get("period_type")).lower())
    if not label or months is None:
        return None
    return period_key(label, months)


def apply_judge_hard_vetoes(
    *,
    product: str,
    candidate: dict,
    quote: str,
    judgment: dict[str, Any],
    generic: str | None = None,
    extra_aliases: list[str] | None = None,
    peer_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Force misclassified/needs_review for known bad patterns even if model is soft."""
    issues = list(judgment.get("issues") or [])
    q = quote or ""
    period_type = stated_text(candidate.get("period_type")).lower()
    mentions = quote_mentions_product(q, product, generic, extra_aliases=extra_aliases)
    other = quote_mentions_other_brand(
        q, product, generic, extra_aliases=extra_aliases, peer_names=peer_names
    )
    veto = False

    # A quote is a sentence. Where the quote runs to several, the one that
    # carries the value has to carry the product too, and the vetoes below
    # read that sentence rather than the block around it.
    value = candidate.get("value_reported")
    carrying = sentence_carrying(q, value)
    if (
        carrying is not None
        and len(sentences(q)) > 1
        and not quote_mentions_product(carrying, product, generic, extra_aliases=extra_aliases)
    ):
        issues.append("hard_veto:value_and_product_in_different_sentences")
        veto = True
    if carrying is not None and states_a_change_not_a_level(carrying, value):
        issues.append("hard_veto:change_not_level")
        veto = True
    read = carrying if carrying is not None else q

    if TOTAL_REVENUE_RE.search(q) and not mentions:
        issues.append("hard_veto:company_total_without_product")
        veto = True
    if other and not mentions:
        issues.append(f"hard_veto:other_brand:{other}")
        veto = True
    # A figure the sentence states for a six- or nine-month span is not the
    # quarter the row calls it, and the sentence carrying the value is what has
    # to say so. A heading that prints the quarter column beside the
    # year-to-date one names both spans and settles neither, so it is not this
    # veto's business - which period the figure is for is the check below.
    carried = spans_named_in(read)
    if (
        period_type == "quarterly"
        and carried & _YEAR_TO_DATE_SPANS
        and _QUARTER_SPAN not in carried
    ):
        issues.append("hard_veto:ytd_language_as_quarterly")
        veto = True
    # A quote that names periods has said which one its figure is for, and a
    # row that claims a different one is not supported by it. An extractor
    # reading a Q1 release answered a question about Q4 with "1Q 2025 Calderon
    # + NuVessa reported revenue of $21.0M", and every check downstream saw a
    # quote naming the product and carrying the value, so it published.
    #
    # This reads the whole quote rather than the row carrying the value,
    # because a table row states no period at all: the model quotes the
    # heading with the row, and the heading is where the filer wrote the
    # period. A quote carrying no heading still names nothing and is left
    # alone.
    named = periods_named_in(q)
    claimed = _period_claimed_by(candidate)
    if named and claimed and claimed not in named:
        issues.append("hard_veto:quote_states_a_different_period")
        veto = True
    # A milestone earned on the product's sales is stated in the same sentence
    # as the product, so naming the product does not clear it.
    if NON_PRODUCT_REVENUE_RE.search(read) and (candidate.get("revenue_scope") or "") not in {"Company total", ""}:
        issues.append("hard_veto:milestone_or_license_revenue")
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


def names_a_year_to_date_span(text: str) -> bool:
    """Whether this text states a period that is part of a year but not a quarter.

    The spans that mean year-to-date are the ones the period grammar has a name
    for that is neither a quarter nor a year, so this asks the same parser that
    types the period rather than matching the phrases a filer might use to write
    it - "first six months of 2025" is one of those phrases and is not a
    "six months ended".
    """
    return bool(spans_named_in(text) & _YEAR_TO_DATE_SPANS)