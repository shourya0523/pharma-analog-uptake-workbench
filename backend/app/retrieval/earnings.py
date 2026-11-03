"""Read a product's quarterly sales from the earnings-release exhibits filed with the SEC.

Filers that tag no product in XBRL still print product sales in the results
release they file as an exhibit - to an 8-K under Item 2.02 (results of
operations), or to a 6-K for a foreign filer. EDGAR full-text search finds the
exhibits that mention the product in the weeks after the quarter; each is read
by Gemini without search, and its answer is accepted only when code can check
it against the exhibit:

- the row it quotes is in the exhibit, verbatim, and the value is in that row;
- the period labels it quotes - a table's header cells above the value, or
  the row itself when it is a sentence - are in the exhibit and name the
  quarter;
- the unit agrees with the scale the exhibit declares, when it declares one;
- the line is the filer's own product sales, not a royalty, collaboration,
  licence, milestone or alliance line on someone else's sales.

Every filer whose exhibit passes is returned, labelled with its own row as
printed, so a product sold by partners in different territories comes back as
two readings rather than one guess. With an issuer named, its reading is the
answer.
"""

from __future__ import annotations

import calendar
import datetime as dt
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.retrieval.single_product import filing_text
from app.retrieval.xbrl import SEARCH_URL, names_issuer, quarter_end, sec_get

# The weeks after a quarter in which its results release is filed.
RELEASE_WINDOW_DAYS = 60
# Exhibits read per question. Competitors and partners mention a brand in
# their releases too; with an issuer named, its exhibits are read first.
MAX_EXHIBITS = 6
PASSAGE_CHARS = 1500
MAX_PASSAGE_TOTAL = 60_000

# Words that mark revenue earned on another company's sales of the product.
_NOT_OWN_SALES = re.compile(r"royalt|collaborat|licens|milestone|alliance|profit[- ]shar|co-promot", re.IGNORECASE)
_SCALES = {"thousands": 1e3, "millions": 1e6, "billions": 1e9, "units": 1.0}
_DECLARED_SCALE = re.compile(r"\bin (thousands|millions|billions)\b|\b(thousands|millions|billions) of\b", re.IGNORECASE)
_CURRENCY = re.compile(r"\b(USD|EUR|CHF|GBP|JPY|DKK|SEK|CAD|AUD)\b|(\$|€|£|¥)")
_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}

PROMPT = """\
Below are passages from a company's earnings-release exhibit filed with the SEC.

Find the net sales the company itself reports for the product "{product}" for
the three months ended {end} ({label}). An issuer whose fiscal year does not
start in January may call that quarter by another name; use the column for
those dates.

If the company prints U.S. and international lines and a total, give the total.
If the only line naming the product is a royalty, collaboration, licence,
milestone or alliance line on another company's sales, report it with that kind.

Answer with one JSON object and nothing else:
{{"found": true or false,
  "value": <number exactly as printed in the row>,
  "unit": "<thousands | millions | billions | units>",
  "currency": "<ISO code>",
  "kind": "<product_sales | royalty | collaboration | other>",
  "row_quote": "<the row or sentence holding the value, copied verbatim>",
  "period_labels": ["<each header cell or phrase above the value's column that
                     states its period, copied verbatim, e.g. "Three Months Ended
                     June 30," and "2025"; empty if the row is a sentence that
                     states its own period>"]}}

Passages:
{passages}
"""


@dataclass
class Reading:
    """One filer's exhibit, and what checking the model's answer found."""

    filer: str
    cik: str
    url: str
    status: str  # accepted | not_found | quote_not_in_exhibit | value_not_in_quote | wrong_period | not_own_sales | unit_conflict | error
    value: float | None = None  # in units of currency
    currency: str | None = None
    row_quote: str | None = None
    period_labels: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class EarningsAnswer:
    """The answer for one product-quarter, with every filer's reading.

    `status`: found | other_filer (only a filer other than the named issuer
    passed) | several_sellers (no issuer named, and filers disagree) |
    not_found | no_exhibit | not_a_quarter.
    `scope` is the accepted row as printed - the only description of what the
    figure covers that the filer itself gave.
    """

    status: str
    product: str
    period: str
    value: float | None = None
    currency: str | None = None
    filer: str | None = None
    url: str | None = None
    scope: str | None = None
    readings: list[Reading] = field(default_factory=list)


# ---------------------------------------------------------------- locating


def find_exhibits(product: str, end: dt.date, *, get: Callable = sec_get) -> list[dict]:
    """Results exhibits mentioning the product, filed in the weeks after `end`.

    An 8-K's exhibit counts when the 8-K reports Item 2.02; a 6-K's exhibit
    counts as filed (6-Ks carry no items). Both properties come from the
    search response.
    """
    body = get(SEARCH_URL, q=f'"{product.split("/")[0].strip()}"', forms="8-K,6-K", dateRange="custom",
               startdt=end.isoformat(), enddt=(end + dt.timedelta(days=RELEASE_WINDOW_DAYS)).isoformat()).json()
    found, seen = [], set()
    for hit in body.get("hits", {}).get("hits", []):
        src = hit["_source"]
        file_type = (src.get("file_type") or "").upper()
        if not file_type.startswith("EX-99"):
            continue
        if src.get("form", "").startswith("8-K") and "2.02" not in (src.get("items") or []):
            continue
        accession, document = hit["_id"].split(":", 1)
        url = f"https://www.sec.gov/Archives/edgar/data/{src['ciks'][0].lstrip('0')}/{accession.replace('-', '')}/{document}"
        if url in seen:
            continue
        seen.add(url)
        found.append({"filer": src["display_names"][0], "cik": src["ciks"][0].lstrip("0"), "url": url})
    return found


# ---------------------------------------------------------------- checking


def _flat(text: str) -> str:
    """Lower case, with everything but letters, digits and the marks a figure
    carries (`.`, `,`, `%`) reduced to single spaces - so a quoted row matches
    the exhibit whether or not it kept the `$` signs and cell padding."""
    return re.sub(r"[^a-z0-9.,%]+", " ", text.replace("\xa0", " ").lower()).strip()


def passages(text: str, product: str) -> str:
    """The text around each mention of the product, joined, capped."""
    flat = re.sub(r"\s+", " ", text.replace("\xa0", " "))
    chunks, last = [], -(10 ** 9)
    for m in re.finditer(re.escape(product.split("/")[0].strip()), flat, re.IGNORECASE):
        if m.start() - last > PASSAGE_CHARS:
            chunks.append(flat[max(0, m.start() - PASSAGE_CHARS): m.start() + PASSAGE_CHARS])
            last = m.start()
    return "\n...\n".join(chunks)[:MAX_PASSAGE_TOTAL]


def value_in_quote(value: Any, quote: str) -> bool:
    """Whether `value` is one of the figures printed in `quote`."""
    try:
        target = abs(float(value))
    except (TypeError, ValueError):
        return False
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?", quote):
        try:
            if abs(float(token.replace(",", "")) - target) < 1e-9:
                return True
        except ValueError:
            continue
    return False


def names_quarter(header: str, end: dt.date) -> bool:
    """Whether a column header names the quarter ending `end`.

    It must carry the year (`2023` or `'23`/`Q3 23`) and say a quarter - the
    quarter's number, the end month, or "three months" - and must not be a
    longer year-to-date column alone.
    """
    h = header.lower()
    n, year = end.month // 3, end.year
    has_year = str(year) in h or re.search(rf"(?<!\d){str(year)[2:]}(?!\d)", h) is not None
    month = calendar.month_name[end.month].lower()
    quarter_words = {1: "first", 2: "second", 3: "third", 4: "fourth"}[n]
    ordinal = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}[n]
    says_quarter = bool(re.search(rf"(?<![a-z0-9])(?:q{n}|{n}q)(?![a-z])|{quarter_words}[- ]quarter|{ordinal} q|"
                                  rf"three months|3 months|\b{month[:3]}", h))
    longer_only = bool(re.search(r"six months|nine months|twelve months|full year|year to date|ytd|fy\b", h)) and not \
        re.search(r"three months|quarter|\bq\d", h)
    return has_year and says_quarter and not longer_only


def declared_scale(text: str) -> set[str]:
    """The scales an exhibit declares for its figures ("in millions")."""
    return {(m.group(1) or m.group(2)).lower() for m in _DECLARED_SCALE.finditer(text)}


def check(answer: dict[str, Any], exhibit_text: str, product: str, end: dt.date) -> tuple[str, str]:
    """`(status, reason)` for a model answer against the exhibit it read."""
    if not answer.get("found"):
        return "not_found", "the model found no line for the product"
    flat = _flat(exhibit_text)
    quote = str(answer.get("row_quote") or "")
    labels = [str(x) for x in (answer.get("period_labels") or []) if str(x).strip()]
    if not quote or _flat(quote) not in flat:
        return "quote_not_in_exhibit", "the quoted row is not in the exhibit"
    if not value_in_quote(answer.get("value"), quote):
        return "value_not_in_quote", "the value is not one of the quoted row's figures"
    missing = [x for x in labels if _flat(x) not in flat]
    if missing:
        return "wrong_period", f"period labels not in the exhibit: {missing}"
    # Labels say which column; a sentence says its own period. A row of
    # figures is never the period's evidence, so it counts only without labels.
    evidence = " ".join(labels) if labels else quote
    if not names_quarter(evidence, end):
        return "wrong_period", f"{evidence[:120]!r} does not name the quarter"
    if answer.get("kind") != "product_sales" or _NOT_OWN_SALES.search(quote):
        return "not_own_sales", f"kind {answer.get('kind')!r}; row: {quote[:80]!r}"
    unit = str(answer.get("unit") or "").lower()
    if unit not in _SCALES:
        return "unit_conflict", f"unit {unit!r} is not a scale"
    # A sentence states its own scale ("$84.0 million"), whatever the tables
    # declare; a table row takes the scale its exhibit declares.
    stated = stated_scale(quote, answer.get("value"))
    if stated:
        if stated != unit:
            return "unit_conflict", f"the row says {stated}, the answer says {unit}"
        return "accepted", ""
    declared = declared_scale(exhibit_text)
    if len(declared) == 1 and unit != "units" and unit not in declared:
        return "unit_conflict", f"the exhibit declares {declared.pop()}, the answer says {unit}"
    return "accepted", ""


def stated_scale(quote: str, value: Any) -> str | None:
    """The scale word printed right after the value in the quote, if any:
    `$84.0 million` says millions."""
    try:
        target = abs(float(value))
    except (TypeError, ValueError):
        return None
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|mn|bn|m|k)\b", quote, re.IGNORECASE):
        try:
            if abs(float(m.group(1).replace(",", "")) - target) < 1e-9:
                word = m.group(2).lower()
                return {"thousand": "thousands", "k": "thousands", "million": "millions", "mn": "millions",
                        "m": "millions", "billion": "billions", "bn": "billions"}[word]
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- reading


def gemini_reader(prompt: str, *, client: Any = None, model: str | None = None) -> dict[str, Any]:
    """Ask Gemini, without search, and return its JSON answer (or {})."""
    from app.retrieval.gemini_search import make_client, parse_json_answer

    client = client or make_client()
    model = model or os.environ.get("GEMINI_READER_MODEL") or "gemini-3.8-flash"
    reply = client.interactions.create(model=model, input=prompt)
    data = reply.model_dump(mode="json") if hasattr(reply, "model_dump") else reply
    return parse_json_answer(data.get("output_text") or "") or {}


def read_exhibit(exhibit: dict, product: str, end: dt.date, label: str, *, get: Callable, ask: Callable) -> Reading:
    reading = Reading(exhibit["filer"], exhibit["cik"], exhibit["url"], "error")
    try:
        text = filing_text(get(exhibit["url"]).content)
        answer = ask(PROMPT.format(product=product, end=f"{calendar.month_name[end.month]} {end.day}, {end.year}",
                                   label=label, passages=passages(text, product)))
    except Exception as exc:  # one unreadable exhibit leaves the others to answer
        reading.reason = f"{type(exc).__name__}: {exc}"
        return reading
    reading.status, reading.reason = check(answer, text, product, end)
    reading.row_quote = answer.get("row_quote")
    reading.period_labels = [str(x) for x in (answer.get("period_labels") or [])]
    if reading.status == "accepted":
        reading.value = float(answer["value"]) * _SCALES[str(answer["unit"]).lower()]
        reading.currency = str(answer.get("currency") or "").upper() or None
    return reading


def read_earnings(
    product: str,
    period: str,
    issuer: str | None = None,
    *,
    get: Callable = sec_get,
    ask: Callable = gemini_reader,
) -> EarningsAnswer:
    """The product's sales for calendar quarter `period` from results exhibits."""
    end = quarter_end(period)
    if end is None:
        return EarningsAnswer("not_a_quarter", product, period)
    exhibits = find_exhibits(product, end, get=get)
    if issuer:
        exhibits.sort(key=lambda e: not names_issuer(e["filer"], issuer))
    exhibits = exhibits[:MAX_EXHIBITS]
    answer = EarningsAnswer("no_exhibit" if not exhibits else "not_found", product, period)
    answer.readings = [read_exhibit(e, product, end, period, get=get, ask=ask) for e in exhibits]
    accepted = [r for r in answer.readings if r.status == "accepted"]
    if issuer:
        own = [r for r in accepted if names_issuer(r.filer, issuer)]
        if not own:
            if accepted:
                answer.status = "other_filer"
            return answer
        accepted = own
    if not accepted:
        return answer
    if len({(r.value, r.currency) for r in accepted}) > 1 and not issuer:
        answer.status = "several_sellers"
        return answer
    chosen = accepted[0]
    answer.status, answer.value, answer.currency = "found", chosen.value, chosen.currency
    answer.filer, answer.url, answer.scope = chosen.filer, chosen.url, chosen.row_quote
    return answer

