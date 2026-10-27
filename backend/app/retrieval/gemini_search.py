"""Ask Gemini, grounded in Google Search, for one product's reported revenue.

This is the raw probe: one question in, the model's answer and everything it
cited out. Nothing here judges whether the number is right - that is for the
caller, against documents the citations point at.
"""

from __future__ import annotations

import calendar
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MODEL = "gemini-3.8-flash"

# Statuses worth retrying: a timeout or an overloaded server clears on its own.
# 429 is left out on purpose - a spent quota or a tier without search grounding
# does not, and retrying it only delays the error.
RETRY_STATUSES = [408, 500, 502, 503, 504]


# A grounded call can run for minutes while the model searches. Held open as
# one request it can be dropped by the server before it finishes, so the call
# runs in background mode and is polled instead.
POLL_SECONDS = 5
MAX_WAIT_SECONDS = 15 * 60
_FINISHED = {"completed", "failed", "cancelled", "incomplete"}


class QuotaExceeded(RuntimeError):
    """Gemini answered 429: the project's quota or rate limit is used up."""


class InteractionFailed(RuntimeError):
    """The interaction ended in a state other than completed, or never ended."""

PROMPT = """\
Find the net product revenue {issuer_clause}reported for {product} for {period}.
{period_note}
Any source is fine - a filing, an earnings release, an annual report, a news
article - as long as it prints the reported figure itself. Do not use an
analyst estimate or forecast. If the product was not reported on its own line
for that period, say so instead of estimating.

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
    raw: dict[str, Any] | None = None


_QUARTER = re.compile(r"^\s*Q([1-4])\s*(\d{4})\s*$|^\s*(\d{4})\s*Q([1-4])\s*$", re.IGNORECASE)
_CALENDAR_YEAR = re.compile(r"^\s*(?:CY\s*)?(\d{4})\s*$", re.IGNORECASE)

FISCAL_NOTE = """
These are calendar dates. An issuer whose fiscal year does not start in January
labels this period with a different quarter or year (the three months ended
June 30 can be its fiscal Q1); report the figure that covers these dates,
whatever the issuer calls it.
"""


def describe_period(period: str) -> tuple[str, bool]:
    """Spell a period out as dates where it names a calendar span.

    "Q2 2020" and "2020Q2" become "the three months ended June 30, 2020
    (calendar Q2 2020)"; "2020" and "CY2020" become the twelve months ended
    December 31. Anything else, "FY2020" included, is passed through as written,
    since an issuer's fiscal year is its own. The flag says whether dates were
    given, and so whether the fiscal-year note applies.
    """
    quarter = _QUARTER.match(period)
    if quarter:
        q = int(quarter.group(1) or quarter.group(4))
        year = int(quarter.group(2) or quarter.group(3))
        month = q * 3
        end = f"{calendar.month_name[month]} {calendar.monthrange(year, month)[1]}, {year}"
        return f"the three months ended {end} (calendar Q{q} {year})", True
    year = _CALENDAR_YEAR.match(period)
    if year:
        return f"the twelve months ended December 31, {year.group(1)} (calendar year {year.group(1)})", True
    return period, False


def build_prompt(product: str, period: str, issuer: str | None = None) -> str:
    issuer_clause = f"that {issuer} " if issuer else ""
    described, dated = describe_period(period)
    return PROMPT.format(
        product=product,
        period=described,
        period_note=FISCAL_NOTE if dated else "",
        issuer_clause=issuer_clause,
    )


def ask_revenue(
    product: str,
    period: str,
    issuer: str | None = None,
    *,
    model: str | None = None,
    client: Any = None,
) -> RevenueAnswer:
    """Run one Google-Search-grounded call and return its answer and citations.

    `client` defaults to `make_client()`. `model` defaults to `GEMINI_MODEL`,
    then `DEFAULT_MODEL`. Raises `QuotaExceeded` on a 429, without retrying,
    and `InteractionFailed` if the call does not complete.
    """
    model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    if client is None:
        client = make_client()
    try:
        interaction = client.interactions.create(
            model=model,
            input=build_prompt(product, period, issuer),
            tools=[{"type": "google_search"}],
            background=True,
        )
        data = _as_dict(interaction)
        waited = 0.0
        while data.get("status") and data["status"] not in _FINISHED:
            if waited >= MAX_WAIT_SECONDS:
                raise InteractionFailed(f"{data.get('id')}: still {data['status']} after {waited:.0f} s")
            time.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
            data = _as_dict(client.interactions.get(data["id"]))
    except Exception as exc:
        if getattr(exc, "status_code", None) == 429:
            raise QuotaExceeded(f"{model}: {exc}") from exc
        raise
    if data.get("status") not in (None, "completed"):
        raise InteractionFailed(f"{data.get('id')}: {data.get('status')}: {data.get('errors')}")
    return parse_interaction(data, product=product, period=period, model=model)


def _as_dict(interaction: Any) -> dict[str, Any]:
    return interaction.model_dump(mode="json") if hasattr(interaction, "model_dump") else interaction


def make_client() -> Any:
    """A `google.genai.Client` that reads `GEMINI_API_KEY` and never retries a 429."""
    from google import genai
    from google.genai import types

    return genai.Client(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(http_status_codes=RETRY_STATUSES)
        )
    )


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
        raw=data,
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
