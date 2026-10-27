"""Check a model's revenue answer against the document it says it read.

The grounded call returns no citations of its own, so the `source_url` the model
writes is the only pointer to evidence. This fetches that document and asks two
things of it: is the value printed near the product's name, and does the quote
appear. Neither the model nor any answer key is consulted.
"""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

# SEC asks automated clients to identify themselves with a contact address.
DEFAULT_USER_AGENT = "pharma-analog-uptake-workbench research (set SEC_USER_AGENT)"

# How many numbers after a mention of the product still belong to it. A
# revenue row flattened to text prints the product's name and then its columns:
# up to U.S., international and worldwide for the period and again for the
# comparative period. Past that, the numbers are the next row's.
NUMBERS_PER_ROW = 6

_NUMBER = re.compile(r"\(?-?\$?\d[\d,]*(?:\.\d+)?\)?")
_ELISION = re.compile(r"\.\.\.|…|\|")


@dataclass
class Verification:
    """What the cited document showed. `status` is the one-word verdict."""

    status: str  # verified | value_not_near_product | value_absent | unfetchable | no_value | no_source
    source_url: str | None
    fetched: bool
    product_mentions: int = 0
    value_anywhere: bool = False
    value_near_product: bool = False
    quote_segments_found: int = 0
    quote_segments: int = 0
    near_text: str | None = None
    error: str | None = None


def verify_answer(answer: dict[str, Any] | None, product: str, *, fetch=None) -> Verification:
    """Fetch `answer["source_url"]` and look for the answer's value and quote in it.

    `answer` is the parsed JSON object from the grounded call. `fetch` takes a
    URL and returns the document's text; it defaults to `fetch_text`.
    """
    answer = answer or {}
    url = answer.get("source_url")
    value = answer.get("value")
    if value is None:
        return Verification("no_value", url, fetched=False)
    if not url:
        return Verification("no_source", None, fetched=False)
    try:
        text = (fetch or fetch_text)(url)
    except Exception as exc:  # any failure to read the page leaves nothing to check
        return Verification("unfetchable", url, fetched=False, error=f"{type(exc).__name__}: {exc}")

    flat = _flatten(text)
    mentions = [m.end() for m in re.finditer(re.escape(_flatten(product)), flat)]
    numbers = list(_NUMBER.finditer(flat))
    positions = [m for m in numbers if _same_number(m.group(), value)]
    near = None
    for end in mentions:
        following = [m for m in numbers if m.start() >= end][:NUMBERS_PER_ROW]
        near = next((m for m in following if _same_number(m.group(), value)), None)
        if near is not None:
            break

    segments = [s.strip() for s in _ELISION.split(_flatten(answer.get("source_quote") or "")) if len(s.strip()) >= 3]
    found = sum(1 for s in segments if s in flat)

    if near is not None:
        status = "verified"
    elif positions:
        status = "value_not_near_product"
    else:
        status = "value_absent"
    return Verification(
        status,
        url,
        fetched=True,
        product_mentions=len(mentions),
        value_anywhere=bool(positions),
        value_near_product=near is not None,
        quote_segments_found=found,
        quote_segments=len(segments),
        near_text=flat[max(0, near.start() - 80) : near.end() + 40] if near else None,
    )


def fetch_text(url: str) -> str:
    """Download `url` and return its text, from HTML or PDF."""
    agent = os.environ.get("SEC_USER_AGENT") or DEFAULT_USER_AGENT
    response = httpx.get(url, headers={"User-Agent": agent}, follow_redirects=True, timeout=60)
    response.raise_for_status()
    kind = response.headers.get("content-type", "")
    if "pdf" in kind or response.content[:5] == b"%PDF-":
        import pdfplumber

        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    import warnings

    from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

    # Inline XBRL filings are XHTML; read as HTML they give the same text.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        return BeautifulSoup(response.content, "lxml").get_text(" ")


def _flatten(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip().lower()


def _same_number(token: str, value: Any) -> bool:
    """Whether a printed token is `value`: commas, `$` and accounting parentheses ignored."""
    digits = token.strip("()$").replace("$", "").replace(",", "")
    try:
        return abs(float(digits)) == abs(float(value))
    except (TypeError, ValueError):
        return False
