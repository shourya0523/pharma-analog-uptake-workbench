"""Who sold a product in each year, read from the annual reports that mention it.

A product changes hands: a line reported by one filer continues in another's
reports after an acquisition. For each year, EDGAR full-text search lists the
annual reports (10-K, 20-F, 40-F) that mention the product; each filer's report
is read by Gemini without search, which says whether the filer recorded the
product's sales itself, earned royalties or partner revenue on it, or only
mentioned it - with a quote that code finds verbatim in the report. The sellers
by year are the timeline.

Where the sellers change from one year to the next, `handover` dates the
change: the 8-K (or 8-K/A) Item 2.01 that reports the completed acquisition or
disposition, read for its closing date; failing that, the first 10-Q in which
the new seller reports the product's sales. `seller_for` turns the two into the
sellers of one calendar quarter, split at the closing date when the handover
closed inside it.

This sees SEC filers only: an owner that files nothing with the SEC leaves a
gap in the years it held the product, which the timeline reports as a gap
rather than filling, and `seller_for` reports as a seller it cannot name.
"""

from __future__ import annotations

import calendar
import datetime as dt
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.retrieval.earnings import _flat, passages
from app.retrieval.gemini_search import QuotaExceeded
from app.retrieval.single_product import filing_text
from app.retrieval.xbrl import _ENTITY_WORDS, ARCHIVES, SEARCH_URL, quarter_end, sec_get

# Annual reports for a year are filed in the months after it ends.
ANNUAL_WINDOW_DAYS = 150
MAX_FILERS_PER_YEAR = 5
# An 8-K reporting a completion is due four business days after it.
CURRENT_REPORT_SLACK_DAYS = 10
# A 10-Q is filed within weeks of its quarter; the window reaches past the
# handover's last year so a first quarter after it is still found.
QUARTERLY_SLACK_DAYS = 60
MAX_CURRENT_REPORTS = 4
MAX_QUARTERLY_REPORTS = 3
ITEM_CHARS = 8000
CONTEXT_CHARS = 12_000

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

ITEM_PROMPT = """\
Below is Item 2.01 (Completion of Acquisition or Disposition of Assets) of a
Form 8-K filed by {filer}, followed by passages from the same filing.

Did the transaction this item reports move the product "{product}" from one
company to another - an acquisition or disposition of the product itself, or of
a company that sold it{sold_by}?

Answer with one JSON object and nothing else:
{{"transfers": true | false,
  "closed": "YYYY-MM-DD" (the date the transaction was completed) | null,
  "quote": "<the sentence stating that it was completed and on what date, copied verbatim>"}}

Item 2.01:
{item}

Other passages:
{context}
"""

QUARTER_PROMPT = """\
Below are passages from a quarterly report filed by {filer} for the period
ending {period_end}.

Did the company itself record net product sales of "{product}" in that period?
If the company acquired the product, or a company that sold it, does the
report state the date that acquisition was completed?

Answer with one JSON object and nothing else:
{{"sells": true | false,
  "quote": "<the sentence that shows it records the product's sales, copied verbatim>",
  "closed": "YYYY-MM-DD" | null,
  "closing_quote": "<the sentence stating the completion date, copied verbatim>" | null}}

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
    # `handover` results by (from_year, to_year), so quarters of one year share one reading.
    handovers: dict[tuple[int, int], Handover] = field(default_factory=dict)

    def sellers(self, year: int) -> list[str]:
        return sorted({r.filer for r in self.years.get(year, []) if r.role == "seller"})

    def seller_ciks(self, year: int) -> dict[str, str]:
        """`{cik: filer}` of the year's sellers. A filer renamed between years
        keeps its CIK, so sellers are compared across years by CIK."""
        return {r.cik: r.filer for r in self.years.get(year, []) if r.role == "seller"}

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


def _ask(ask: Callable, prompt: str) -> dict[str, Any] | None:
    """The model's answer, or None when it could not be had. A spent quota
    stops the run rather than reading as a refusal."""
    try:
        return ask(prompt)
    except Exception as exc:
        if isinstance(exc, QuotaExceeded) or getattr(exc, "status_code", None) == 429:
            raise QuotaExceeded(str(exc)) from exc
        return None


def timeline(product: str, years: range, *, get: Callable = sec_get, ask: Callable = gemini_role,
             into: Timeline | None = None) -> Timeline:
    """The product's sellers, partners and mentions in each year of `years`.

    Years already in `into` are kept as they are and not read again; the
    others are added to it, and it is returned."""
    result = into if into is not None else Timeline(product)
    for year in years:
        if year in result.years:
            continue
        roles = []
        for report in annual_reports(product, year, get=get):
            role = YearRole(year, report["filer"], report["cik"], report["url"], "error")
            try:
                text = filing_text(get(report["url"]).content)
            except Exception:  # noqa: BLE001 - an unreadable report leaves the others to answer
                roles.append(role)
                continue
            answer = _ask(ask, ROLE_PROMPT.format(year=year, product=product, passages=passages(text, product)))
            if answer is None:
                roles.append(role)
                continue
            quote = str(answer.get("quote") or "")
            verified = bool(quote) and _flat(quote) in _flat(text)
            role.role = str(answer.get("role")) if verified else "unverified"
            role.quote = quote or None
            roles.append(role)
        result.years[year] = roles
    return result


# ---------------------------------------------------------------- dates


_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WRITTEN_DATES = [
    (re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.IGNORECASE), ("month", "day", "year")),
    (re.compile(rf"\b(\d{{1,2}})\s+({_MONTH})\.?,?\s+(\d{{4}})\b", re.IGNORECASE), ("day", "month", "year")),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), ("year", "month", "day")),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), ("month", "day", "year")),
]


def dates_in(text: str) -> set[dt.date]:
    """Every calendar date written out in `text`: "June 16, 2017", "16 June
    2017", "Jun. 16, 2017", "2017-06-16" or "6/16/2017"."""
    found = set()
    for pattern, order in _WRITTEN_DATES:
        for m in pattern.finditer(text):
            parts = dict(zip(order, m.groups()))
            month = parts["month"]
            month = _MONTHS[month.lower()] if not month.isdigit() else int(month)
            try:
                found.add(dt.date(int(parts["year"]), month, int(parts["day"])))
            except ValueError:
                continue
    return found


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def quarter_of(day: dt.date) -> str:
    """The calendar quarter holding `day`, written as `quarter_end` reads it."""
    return f"Q{(day.month - 1) // 3 + 1} {day.year}"


def nearest_quarter(period_end: dt.date) -> str:
    """The calendar quarter whose end is nearest a fiscal period's end, so a
    quarter closing on the Sunday after the calendar quarter counts as it."""
    ends = [dt.date(y, m, calendar.monthrange(y, m)[1])
            for y in (period_end.year - 1, period_end.year) for m in (3, 6, 9, 12)]
    return quarter_of(min(ends, key=lambda e: abs((e - period_end).days)))


def quarter_start(period: str) -> dt.date | None:
    end = quarter_end(period)
    return dt.date(end.year, end.month - 2, 1) if end else None


# ---------------------------------------------------------------- handover


@dataclass
class Party:
    """A seller. `cik` is None for one that files nothing with the SEC (or that
    no filing names), whose name is then unknown and left so."""
    filer: str | None
    cik: str | None

    @property
    def sec_filer(self) -> bool:
        return self.cik is not None


UNKNOWN = Party(None, None)


@dataclass
class Handover:
    product: str
    from_sellers: list[Party]
    to_sellers: list[Party]
    status: str  # dated | quarter_only | none | unknown
    closed: dt.date | None = None  # the first day the new seller held it
    quarter: str | None = None  # the calendar quarter it changed hands in
    source: str | None = None  # "8-K item 2.01" | "10-Q"
    url: str | None = None
    quote: str | None = None
    note: str | None = None


def short_name(filer: str) -> str:
    """A filer's display name as prose writes it: no `(CIK ...)` or ticker
    parentheses, no `/DE/`-style state suffix, no trailing entity words.
    `CALDERON LABS CORP /DE/  (CLB)  (CIK 0000000001)` becomes `CALDERON LABS`."""
    name = re.sub(r"\([^)]*\)", " ", filer)
    name = re.sub(r"/[A-Z]{2,3}/?", " ", name)
    words = re.findall(r"[A-Za-z0-9&'.-]+", name)
    while words and words[-1].lower().strip(".,") in _ENTITY_WORDS:
        words.pop()
    return " ".join(words).strip(" ,.")


def _url(cik: str, accession: str, document: str) -> str:
    return f"{ARCHIVES}/{cik}/{accession.replace('-', '')}/{document}"


def primary_document(cik: str, accession: str, form: str, *, get: Callable = sec_get) -> str | None:
    """The URL of the filing's own form document (not an exhibit), read from
    the Type column of the filing's index page. The link's text is the file
    name; its target is not used, since an inline-XBRL document links to the
    viewer (`/ix?doc=...`) rather than to the file."""
    folder = f"{ARCHIVES}/{cik}/{accession.replace('-', '')}"
    page = get(f"{folder}/{accession}-index.htm").content.decode("utf-8", "ignore")
    rows = re.findall(r'<a href="[^"]*/Archives/edgar/data/[^"]*">([^<]+)</a>(?:(?!</td>).)*</td>\s*<td[^>]*>([^<]*)</td>', page, re.DOTALL)
    for name, kind in rows:
        if kind.strip().upper() == form.upper():
            return f"{folder}/{name.strip()}"
    return None


_ITEM_HEADING = re.compile(r"Item\s*2\.01\.?\s*Completion", re.IGNORECASE)
_NEXT_ITEM = re.compile(r"Item\s*\d\.\d\d(?!\s*of\b)", re.IGNORECASE)


def item_201(text: str) -> str:
    """The text under the Item 2.01 heading, up to the next item heading. A
    filing that prints no such heading gives its opening text instead."""
    m = _ITEM_HEADING.search(text)
    if not m:
        return text[:ITEM_CHARS]
    rest = text[m.start():]
    nxt = next((n for n in _NEXT_ITEM.finditer(rest, m.end() - m.start())
                if not n.group().replace(" ", "").lower().startswith("item2.01")), None)
    return rest[: nxt.start() if nxt else ITEM_CHARS][:ITEM_CHARS]


def _search(get: Callable, **params: Any) -> list[dict]:
    return get(SEARCH_URL, **params).json().get("hits", {}).get("hits", [])


def completion_reports(terms: list[str], ciks: set[str], start: dt.date, end: dt.date, *,
                       get: Callable = sec_get) -> list[dict]:
    """8-Ks and 8-K/As reporting Item 2.01 that mention any of `terms`, filed
    by one of `ciks` between `start` and `end`, one per filing, oldest first.

    Full-text search's `forms=8-K` returns amendments with it (and a list
    naming `8-K/A` beside it returns nothing), so the form is filtered here."""
    filings: dict[str, dict] = {}
    for term in terms:
        hits = _search(get, q=f'"{term}"', forms="8-K", items="2.01", dateRange="custom",
                       startdt=start.isoformat(), enddt=end.isoformat())
        for hit in hits:
            src = hit["_source"]
            cik = src["ciks"][0].lstrip("0")
            if cik not in ciks or not src.get("form", "").upper().startswith("8-K") \
                    or "2.01" not in (src.get("items") or []):
                continue
            accession, document = hit["_id"].split(":", 1)
            filing = filings.setdefault(accession, {"accession": accession, "cik": cik, "form": src["form"],
                                                    "filer": src["display_names"][0], "filed": src.get("file_date"),
                                                    "documents": []})
            if document not in filing["documents"]:
                filing["documents"].append(document)
    return sorted(filings.values(), key=lambda f: (f["filed"] or "", f["accession"]))


def _read_completion(filing: dict, product: str, from_sellers: list[Party], window: tuple[dt.date, dt.date], *,
                     get: Callable, ask: Callable) -> tuple[dt.date, str, str] | None:
    """`(closing date, url, quote)` when the filing's Item 2.01 reports the
    product changing hands, with the date in a quote found verbatim in it."""
    url = primary_document(filing["cik"], filing["accession"], filing["form"], get=get)
    if not url:
        return None
    text = filing_text(get(url).content)
    item = item_201(text)
    terms = [product] + [short_name(p.filer) for p in from_sellers if p.filer]
    context = []
    for document in filing["documents"]:
        doc_url = _url(filing["cik"], filing["accession"], document)
        if doc_url == url:
            continue
        doc_text = filing_text(get(doc_url).content)
        context.extend(passages(doc_text, term) for term in terms)
        text += " " + doc_text
    named = [p.filer for p in from_sellers if p.filer]
    sold_by = f" (in the prior year it was sold by {', '.join(short_name(n) for n in named)})" if named else ""
    answer = _ask(ask, ITEM_PROMPT.format(filer=filing["filer"], product=product, sold_by=sold_by, item=item,
                                          context="\n...\n".join(c for c in context if c)[:CONTEXT_CHARS]))
    if not answer or answer.get("transfers") is not True:
        return None
    closed, quote = _date(answer.get("closed")), str(answer.get("quote") or "")
    if not closed or not quote or _flat(quote) not in _flat(text) or closed not in dates_in(quote):
        return None
    if not window[0] <= closed <= window[1]:
        return None
    return closed, url, quote


def first_quarterly_sales(product: str, seller: Party, start: dt.date, end: dt.date, extra_terms: list[str], *,
                          get: Callable = sec_get, ask: Callable = gemini_role) -> dict | None:
    """The earliest 10-Q of `seller` filed between `start` and `end` in which
    it records the product's sales: `{period_end, url, quote, closed,
    closing_quote}`, the closing date only when a verbatim quote states it."""
    hits = _search(get, q=f'"{product}"', forms="10-Q", ciks=seller.cik.zfill(10), dateRange="custom",
                   startdt=start.isoformat(), enddt=end.isoformat())
    reports, seen = [], set()
    for hit in hits:
        src = hit["_source"]
        accession, document = hit["_id"].split(":", 1)
        if accession in seen or src.get("file_type", "").upper().startswith("EX-") or not src.get("period_ending"):
            continue
        seen.add(accession)
        reports.append((src["period_ending"], _url(seller.cik, accession, document)))
    for period_end, url in sorted(reports)[:MAX_QUARTERLY_REPORTS]:
        text = filing_text(get(url).content)
        chunks = [passages(text, term) for term in [product, *extra_terms,
                                                     "completed the acquisition", "completion of the acquisition"]]
        answer = _ask(ask, QUARTER_PROMPT.format(filer=seller.filer, period_end=period_end, product=product,
                                                 passages="\n...\n".join(c for c in chunks if c)[:CONTEXT_CHARS]))
        if not answer or answer.get("sells") is not True:
            continue
        quote = str(answer.get("quote") or "")
        if not quote or _flat(quote) not in _flat(text):
            continue
        found = {"period_end": dt.date.fromisoformat(period_end), "url": url, "quote": quote,
                 "closed": None, "closing_quote": None}
        closed, closing_quote = _date(answer.get("closed")), str(answer.get("closing_quote") or "")
        if closed and closing_quote and _flat(closing_quote) in _flat(text) and closed in dates_in(closing_quote) \
                and closed <= found["period_end"]:
            found["closed"], found["closing_quote"] = closed, closing_quote
        return found
    return None


def handover(product: str, from_year: int, to_year: int, *, known: Timeline | None = None,
             get: Callable = sec_get, ask: Callable = gemini_role) -> Handover:
    """When the product passed from the sellers of `from_year` to the new
    sellers of `to_year`.

    Sellers come from the timeline (read for the two years if `known` lacks
    them). A seller of `to_year` that did not sell in `from_year` is new; the
    sellers of `from_year` are the old ones, and none there means an owner the
    SEC does not see. The 8-K Item 2.01 search names the product and each old
    seller, and keeps only filings by an old or new seller - the acquirer, or
    a seller disposing of it. The 10-Q fallback dates the change to the
    quarter of the new seller's first 10-Q that records the product's sales,
    or to the day when that 10-Q states the closing date.
    """
    tl = timeline(product, range(from_year, to_year + 1), get=get, ask=ask, into=known)
    if (from_year, to_year) in tl.handovers:
        return tl.handovers[(from_year, to_year)]
    tl.handovers[(from_year, to_year)] = result = _handover(product, tl, from_year, to_year, get=get, ask=ask)
    return result


def _handover(product: str, tl: Timeline, from_year: int, to_year: int, *, get: Callable,
              ask: Callable) -> Handover:
    before, after = tl.seller_ciks(from_year), tl.seller_ciks(to_year)
    old = [Party(n, c) for c, n in sorted(before.items())]
    new = [Party(n, c) for c, n in sorted(after.items()) if c not in before]
    result = Handover(product, old or [UNKNOWN], new, "unknown")
    if not new:
        result.status = "none" if after else "unknown"
        result.note = (f"the sellers of {to_year} also sold it in {from_year}" if after else
                       f"no SEC filer's annual report records selling it in {to_year}")
        return result

    start = dt.date(from_year, 1, 1)
    end = dt.date(to_year, 12, 31) + dt.timedelta(days=CURRENT_REPORT_SLACK_DAYS)
    terms = [product] + [short_name(p.filer) for p in old]
    ciks = {p.cik for p in old + new if p.cik}
    window = (start, dt.date(to_year, 12, 31))
    for filing in completion_reports(terms, ciks, start, end, get=get)[:MAX_CURRENT_REPORTS]:
        read = _read_completion(filing, product, old, window, get=get, ask=ask)
        if read:
            result.closed, result.url, result.quote = read
            result.status, result.source, result.quarter = "dated", "8-K item 2.01", quarter_of(read[0])
            return result

    q_end = dt.date(to_year, 12, 31) + dt.timedelta(days=QUARTERLY_SLACK_DAYS)
    firsts = [(p, first_quarterly_sales(product, p, start, q_end, [short_name(o.filer) for o in old if o.filer],
                                        get=get, ask=ask)) for p in new]
    firsts = sorted(((p, f) for p, f in firsts if f), key=lambda pf: pf[1]["period_end"])
    if firsts:
        _, first = firsts[0]
        result.source, result.url = "10-Q", first["url"]
        if first["closed"]:
            result.status, result.closed, result.quote = "dated", first["closed"], first["closing_quote"]
            result.quarter = quarter_of(first["closed"])
        else:
            result.status, result.quote = "quarter_only", first["quote"]
            result.quarter = nearest_quarter(first["period_end"])
            result.note = "the first 10-Q recording the new seller's sales of it; no closing date stated"
        return result
    result.note = "no Item 2.01 report or 10-Q dates the change"
    return result


# ---------------------------------------------------------------- one quarter


@dataclass
class QuarterSeller:
    filer: str | None
    cik: str | None
    start: dt.date | None  # first day in the quarter it sold the product; None if not known
    end: dt.date | None  # last day in the quarter it sold the product; None if not known
    note: str | None = None

    @property
    def sec_filer(self) -> bool:
        return self.cik is not None


@dataclass
class QuarterSellers:
    product: str
    period: str
    status: str  # single | split | split_undated | unknown
    sellers: list[QuarterSeller] = field(default_factory=list)
    handover: Handover | None = None
    evidence: list[str] = field(default_factory=list)  # quotes, each found verbatim in its source


def _whole(parties: list[Party], start: dt.date, end: dt.date, note: str | None = None) -> list[QuarterSeller]:
    return [QuarterSeller(p.filer, p.cik, start, end,
                          note if p.sec_filer else (note or "seller unknown: not found in any SEC filing"))
            for p in parties]


def seller_for(product: str, period: str, *, known: Timeline | None = None,
               get: Callable = sec_get, ask: Callable = gemini_role) -> QuarterSellers:
    """The sellers of `product` in calendar quarter `period` ("Q2 2017").

    The timeline is read for the period's year and the year before. Where the sellers
    change between the two years, `handover` dates the change: a closing date
    inside the quarter splits it - the old sellers through the day before, the
    new ones from that day - and a change dated only to this quarter lists
    both with no dates (`split_undated`). A seller that files nothing with the
    SEC appears with no name and no CIK; it is never filled in.
    """
    end = quarter_end(period)
    if end is None:
        raise ValueError(f"not a calendar quarter: {period!r}")
    start, year = quarter_start(period), end.year
    tl = timeline(product, range(year - 1, year + 1), get=get, ask=ask, into=known)
    result = QuarterSellers(product, period, "unknown")
    before, during = tl.seller_ciks(year - 1), tl.seller_ciks(year)
    evidence = {r.cik: r.quote for y in (year - 1, year) for r in tl.years.get(y, []) if r.role == "seller"}

    if not during:
        result.sellers = [QuarterSeller(None, None, start, end, f"seller unknown: no SEC filer's annual report "
                                                                f"records selling it in {year}")]
        return result

    if set(during) <= set(before):
        result.status = "single"
        result.sellers = [QuarterSeller(n, c, start, end) for c, n in sorted(during.items())]
        result.evidence = [evidence[c] for c in sorted(during) if evidence.get(c)]
        return result

    h = handover(product, year - 1, year, known=tl, get=get, ask=ask)
    result.handover = h
    old, new = h.from_sellers, h.to_sellers

    if h.status == "dated" and h.closed:
        if h.closed <= start:
            result.status, result.sellers = "single", _whole(new, start, end)
        elif h.closed > end:
            result.status, result.sellers = "single", _whole(old, start, end)
        else:
            through = h.closed - dt.timedelta(days=1)
            result.status = "split"
            result.sellers = _whole(old, start, through) + _whole(new, h.closed, end)
        return _with_evidence(result, evidence)

    if h.status == "quarter_only" and h.quarter:
        changed = quarter_end(h.quarter)
        if changed < end:
            result.status, result.sellers = "single", _whole(new, start, end)
        elif changed > end:
            result.status, result.sellers = "single", _whole(old, start, end)
        else:
            note = "changed hands in this quarter; closing date not stated"
            result.status = "split_undated"
            result.sellers = [QuarterSeller(p.filer, p.cik, None, None, note) for p in old + new]
        return _with_evidence(result, evidence)

    note = f"sellers changed during {year}; no filing dates the change"
    result.status = "split_undated"
    result.sellers = [QuarterSeller(p.filer, p.cik, None, None, note) for p in old + new]
    return _with_evidence(result, evidence)


def _with_evidence(result: QuarterSellers, by_cik: dict[str, str | None]) -> QuarterSellers:
    """The handover's quote, then each listed seller's annual-report quote."""
    quotes = [result.handover.quote if result.handover else None] + [by_cik.get(s.cik) for s in result.sellers if s.cik]
    result.evidence = [q for q in quotes if q]
    return result
