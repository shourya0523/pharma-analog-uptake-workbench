"""Ask Gemini, grounded in Google Search, for one product's reported revenue.

This is the raw probe: one question in, the model's answer and everything it
cited out. Nothing here judges whether the number is right - that is for the
caller, against documents the citations point at.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MODEL = "gemini-3.8-flash"

PROMPT = """\
Find the net product revenue {issuer_clause}reported for {product} for {period}.

Use the issuer's own disclosure (an SEC filing, an annual report, or an earnings
release) rather than a news article or an analyst estimate. If the issuer did
not report the product on its own line for that period, say so instead of
estimating.

Answer with one JSON object and nothing else:
{{
  "product": "<product as named in the source>",
  "period": "<period exactly as the source labels it>",
  "value": <number as printed, or null if not reported>,
  "unit": "<millions | thousands | billions | units>",
  "currency": "<ISO code>",
  "geography": "<worldwide | US | ex-US | other, as the source scopes it>",
  "source_url": "<URL of the document the number is printed in>",
  "source_quote": "<the line or sentence the number is read from, verbatim>",
  "not_reported_reason": "<why, if value is null>"
}}
"""


@dataclass
class Citation:
    """A URL the model attached to a span of its answer."""

    url: str | None
    title: str | None
    cited_text: str | None


@dataclass
class RevenueAnswer:
    """What one grounded call returned, kept whole so a run can be audited."""

    product: str
    period: str
    model: str
    text: str
    parsed: dict[str, Any] | None
    citations: list[Citation] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    interaction_id: str | None = None


def build_prompt(product: str, period: str, issuer: str | None = None) -> str:
    issuer_clause = f"that {issuer} " if issuer else ""
    return PROMPT.format(product=product, period=period, issuer_clause=issuer_clause)


def ask_revenue(
    product: str,
    period: str,
    issuer: str | None = None,
    *,
    model: str | None = None,
    client: Any = None,
) -> RevenueAnswer:
    """Run one Google-Search-grounded call and return its answer and citations.

    `client` defaults to a `google.genai.Client`, which reads `GEMINI_API_KEY`
    from the environment. `model` defaults to `GEMINI_MODEL`, then
    `DEFAULT_MODEL`.
    """
    model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    if client is None:
        from google import genai

        client = genai.Client()
    interaction = client.interactions.create(
        model=model,
        input=build_prompt(product, period, issuer),
        tools=[{"type": "google_search"}],
    )
    data = interaction.model_dump(mode="json") if hasattr(interaction, "model_dump") else interaction
    return parse_interaction(data, product=product, period=period, model=model)


def parse_interaction(data: dict[str, Any], *, product: str, period: str, model: str) -> RevenueAnswer:
    """Pull the answer text, citations and search queries out of an interaction.

    Works on the interaction as a plain dict, so a saved response can be
    re-parsed without the SDK.
    """
    texts: list[str] = []
    citations: list[Citation] = []
    queries: list[str] = []
    for step in data.get("steps") or []:
        kind = step.get("type")
        if kind == "google_search_call":
            queries.extend((step.get("arguments") or {}).get("queries") or [])
        elif kind == "model_output":
            for block in step.get("content") or []:
                if block.get("type") != "text":
                    continue
                text = block.get("text") or ""
                texts.append(text)
                for note in block.get("annotations") or []:
                    if note.get("type") != "url_citation":
                        continue
                    start, end = note.get("start_index"), note.get("end_index")
                    span = text[start:end] if start is not None and end is not None else None
                    citations.append(Citation(note.get("url"), note.get("title"), span))
    text = data.get("output_text") or "".join(texts)
    return RevenueAnswer(
        product=product,
        period=period,
        model=model,
        text=text,
        parsed=parse_json_answer(text),
        citations=citations,
        search_queries=queries,
        usage=data.get("usage"),
        interaction_id=data.get("id"),
    )


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json_answer(text: str) -> dict[str, Any] | None:
    """Read the JSON object out of the answer, or None if there is not one.

    Takes a fenced block if the model wrapped one, otherwise the span from the
    first `{` to the last `}`.
    """
    fenced = _FENCE.search(text)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
