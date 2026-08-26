from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx
import yaml
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config

from app.aws_session import boto3_session
from app.config import get_settings
from app.llm.grounding import apply_structured_field_gates, enforce_verbatim_on_candidates, quote_is_verbatim
from app.parsing.evidence import TOTAL_REVENUE_RE, product_aliases
from app.quality.candidate_filters import quote_mentions_other_brand, quote_mentions_product


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class BedrockClient:
    """Amazon Bedrock transport: Converse (Claude) + Mantle Responses web search (GPT)."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._runtime = None

    def _runtime_client(self):
        if self._runtime is None:
            self._runtime = boto3_session().client(
                "bedrock-runtime",
                config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
            )
        return self._runtime

    def _converse_text(self, *, model: str, system: str, user: str) -> str:
        resp = self._runtime_client().converse(
            modelId=model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={
                "maxTokens": self.settings.bedrock_max_tokens,
                "temperature": 0.1,
            },
        )
        parts = resp.get("output", {}).get("message", {}).get("content") or []
        texts = [p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p]
        return "\n".join(t for t in texts if t)

    async def chat_json(self, *, model: str, system: str, user: str) -> dict[str, Any]:
        content = await asyncio.to_thread(
            self._converse_text,
            model=model,
            system=system,
            user=user,
        )
        return _parse_json_content(content)

    def _web_search_tool(self) -> dict[str, Any]:
        return {"type": "web_search", "external_web_access": False}

    def _domain_hint(self) -> str:
        domains = [
            d.strip()
            for d in (self.settings.llm_search_allowed_domains or "").split(",")
            if d.strip()
        ]
        if not domains:
            return ""
        return (
            " Prefer sources on these domains when possible: "
            + ", ".join(domains)
            + "."
        )

    def _mantle_responses(self, *, model: str, system: str, user: str) -> dict[str, Any]:
        url = f"{self.settings.mantle_base_url}/responses"
        payload = {
            "model": model,
            "instructions": system + self._domain_hint() + " Return valid JSON only.",
            "input": user,
            "tools": [self._web_search_tool()],
            "temperature": 0.1,
            "max_output_tokens": self.settings.bedrock_max_tokens,
        }
        body = json.dumps(payload).encode("utf-8")
        session = boto3_session()
        creds = session.get_credentials()
        if creds is None:
            raise RuntimeError("AWS credentials required for Bedrock Mantle web search")
        frozen = creds.get_frozen_credentials()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        aws_req = AWSRequest(method="POST", url=url, data=body, headers=headers)
        SigV4Auth(frozen, "bedrock", self.settings.aws_region).add_auth(aws_req)
        prepared = dict(aws_req.headers.items())
        with httpx.Client(timeout=240) as client:
            resp = client.post(url, content=body, headers=prepared)
            resp.raise_for_status()
            return resp.json()

    async def chat_json_with_web(
        self,
        *,
        model: str,
        system: str,
        user: str,
        fetch: bool = False,
        timeout: float = 180,
    ) -> dict[str, Any]:
        """JSON chat with Bedrock native web_search (Search + Fetch in one tool)."""
        _ = fetch, timeout  # Fetch is server-side via the same web_search tool
        search_model = self.settings.bedrock_model_search
        data = await asyncio.to_thread(
            self._mantle_responses,
            model=search_model,
            system=system,
            user=user,
        )
        text, citations = _text_and_citations_from_responses(data)
        parsed = _parse_json_content(text or "{}")
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
            return json.loads(match.group(0))
        return {"raw": text}


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


def _text_and_citations_from_responses(data: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """Parse Bedrock Mantle / OpenAI Responses API payload."""
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        text = data["output_text"]
    else:
        chunks: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "message":
                continue
            for block in item.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "output_text":
                    chunks.append(block.get("text") or "")
        text = "\n".join(chunks)

    citations: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in data.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if not isinstance(block, dict):
                continue
            for ann in block.get("annotations") or []:
                if not isinstance(ann, dict):
                    continue
                cite = ann.get("url_citation") if ann.get("type") == "url_citation" else ann
                if not isinstance(cite, dict):
                    cite = ann
                url = (cite.get("url") or "").strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                citations.append(
                    {
                        "url": url,
                        "title": cite.get("title") or url,
                        "snippet": cite.get("content") or cite.get("snippet") or "",
                    }
                )
    return text, citations


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
    def __init__(self, client: BedrockClient | None = None) -> None:
        self.client = client or BedrockClient()
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
        user = prompt["user_template"].format(
            product=product,
            company=company or "",
            source_meta=json.dumps(source_meta),
            text=clipped,
        )
        result = await self.client.chat_json(
            model=self.settings.bedrock_model_extract,
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
            model=self.settings.bedrock_model_extract,
            system=prompt["system"],
            user=user,
        )
        candidates = result.get("candidates") or []
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
        return await self.client.chat_json(
            model=self.settings.bedrock_model_extract,
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
        result = await self.client.chat_json(
            model=self.settings.bedrock_model_judge,
            system=prompt["system"],
            user=user,
        )
        return apply_judge_hard_vetoes(
            product=product,
            candidate=candidate,
            quote=quote,
            judgment=result,
            generic=generic,
            extra_aliases=extra_aliases,
        )

    async def reconcile(self, *, product: str, candidates: list[dict]) -> dict[str, Any]:
        prompt = load_prompt("conflict_reconciler")
        user = prompt["user_template"].format(
            product=product,
            candidates=json.dumps(candidates)[:40000],
        )
        return await self.client.chat_json(
            model=self.settings.bedrock_model_judge,
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
        return await self.client.chat_json(
            model=self.settings.bedrock_model_extract,
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
        user = prompt["user_template"].format(
            product=product,
            generic=generic or "",
            manufacturer=manufacturer or "",
            ticker=ticker or "",
            indication=indication or "",
        )
        return await self.client.chat_json(
            model=self.settings.bedrock_model_extract,
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
        """Search via Bedrock native web_search tool."""
        prompt = load_prompt("search_planner")
        if not self.settings.enable_llm_search:
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
            model=self.settings.bedrock_model_search,
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
        """Search + fetch via Bedrock web_search (Search + Fetch operations)."""
        prompt = load_prompt("search_fetch")
        if not self.settings.enable_llm_search:
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
            model=self.settings.bedrock_model_search,
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
        if not self.settings.enable_llm_search:
            return {}
        user = prompt["user_template"].format(
            product=product,
            aliases=json.dumps(aliases[:20]),
            manufacturer=manufacturer or "",
            ticker=ticker or "",
        )
        return await self.client.chat_json_with_web(
            model=self.settings.bedrock_model_search,
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
        """Judge with Bedrock web_search; optional prefetched snippets as extra context."""
        prompt = load_prompt("judge_search_validator")
        if not self.settings.enable_llm_search:
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
            model=self.settings.bedrock_model_search,
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
            re.I,
        )
    )
