"""Who sold a product in each year, read from the annual reports that mention it.

A product changes hands: a line reported by one filer continues in another's
reports after an acquisition. For each year, EDGAR full-text search lists the
annual reports (10-K, 20-F, 40-F) that mention the product; each filer's report
is read by Gemini without search, which says whether the filer recorded the
product's sales itself, earned royalties or partner revenue on it, or only
mentioned it - with a quote that code finds verbatim in the report. The sellers
by year are the timeline.

This sees SEC filers only: an owner that files nothing with the SEC leaves a
gap in the years it held the product, which the timeline reports as a gap
rather than filling.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from app.retrieval.earnings import _flat, passages
from app.retrieval.single_product import filing_text
from app.retrieval.xbrl import SEARCH_URL, sec_get

# Annual reports for a year are filed in the months after it ends.
ANNUAL_WINDOW_DAYS = 150
MAX_FILERS_PER_YEAR = 5

ROLE_PROMPT = """\
Below are passages from a company's annual report for {year}.

What was the company's relationship to the product "{product}" in {year}?
- "seller": the company itself recorded net product sales of it
- "partner": the company earned royalties, collaboration, licence or milestone
  revenue on someone else's sales of it, or co-promoted it
- "mention": it is only mentioned (a competitor, a pipeline comparison, a
  former product it no longer sold)

Answer with one JSON object and nothing else:
{{"role": "seller" | "partner" | "mention",
  "quote": "<the sentence that shows it, copied verbatim>"}}

Passages:
{passages}
"""


@dataclass
class YearRole:
    year: int
    filer: str
    cik: str
    url: str
    role: str  # seller | partner | mention | unverified | error
    quote: str | None = None


@dataclass
class Timeline:
    product: str
    years: dict[int, list[YearRole]] = field(default_factory=dict)

    def sellers(self, year: int) -> list[str]:
        return sorted({r.filer for r in self.years.get(year, []) if r.role == "seller"})

    def gaps(self) -> list[int]:
        """Years in range with no SEC-filing seller found."""
        return [y for y in sorted(self.years) if not self.sellers(y)]


def annual_reports(product: str, year: int, *, get: Callable = sec_get) -> list[dict]:
    """One annual report per filer mentioning the product, filed after `year`."""
    start = dt.date(year + 1, 1, 1)
    body = get(SEARCH_URL, q=f'"{product}"', forms="10-K,20-F,40-F", dateRange="custom", startdt=start.isoformat(),
               enddt=(start + dt.timedelta(days=ANNUAL_WINDOW_DAYS)).isoformat()).json()
    reports, seen = [], set()
    for hit in body.get("hits", {}).get("hits", []):
        src = hit["_source"]
        cik = src["ciks"][0].lstrip("0")
        if cik in seen or src.get("file_type", "").upper().startswith("EX-"):
            continue
        seen.add(cik)
        accession, document = hit["_id"].split(":", 1)
        reports.append({"filer": src["display_names"][0], "cik": cik,
                        "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{document}"})
    return reports[:MAX_FILERS_PER_YEAR]


def gemini_role(prompt: str, *, client: Any = None) -> dict[str, Any]:
    from app.retrieval.gemini_search import make_client, parse_json_answer

    client = client or make_client()
    reply = client.interactions.create(model=os.environ.get("GEMINI_READER_MODEL") or "gemini-3.8-flash", input=prompt)
    data = reply.model_dump(mode="json") if hasattr(reply, "model_dump") else reply
    return parse_json_answer(data.get("output_text") or "") or {}


def timeline(product: str, years: range, *, get: Callable = sec_get, ask: Callable = gemini_role) -> Timeline:
    """The product's sellers, partners and mentions in each year of `years`."""
    result = Timeline(product)
    for year in years:
        roles = []
        for report in annual_reports(product, year, get=get):
            role = YearRole(year, report["filer"], report["cik"], report["url"], "error")
            try:
                text = filing_text(get(report["url"]).content)
                answer = ask(ROLE_PROMPT.format(year=year, product=product, passages=passages(text, product)))
            except Exception:  # an unreadable report leaves the others to answer
                roles.append(role)
                continue
            quote = str(answer.get("quote") or "")
            verified = bool(quote) and _flat(quote) in _flat(text)
            role.role = str(answer.get("role")) if verified else "unverified"
            role.quote = quote or None
            roles.append(role)
        result.years[year] = roles
    return result
