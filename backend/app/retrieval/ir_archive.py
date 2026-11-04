"""Read a product's quarterly sales from an issuer's own investor-relations site.

Some owners file nothing with the SEC: their quarterly results exist only as
PDF data books, releases and HTML pages on their own sites. This route reaches
them in three steps, with the model used only where code cannot:

1. `find_results_archive(issuer)` - one grounded Gemini call names the
   issuer's results-archive page. Code fetches it with the same declared
   User-Agent `verify.py` uses (never a browser's) and accepts it only if it
   lists links to dated reports.
2. `list_reports(archive_url)` - every link on the page, HTML or PDF, is given
   the period its own text, its URL or the text around it names ("Q2 2023",
   "2Q23", "H1 2023", "9M 2023", "FY2023 Q2", "April-June 2023", "six months
   ended June 30, 2023"). No model. An issuer's fiscal year is read off the
   page ("fiscal year ending March", "FY2023/3", "FY2023 (April 1, 2023 -")
   and its quarter labels are mapped through it, so a fiscal Q1 that starts
   in April is calendar April-June.
3. `read_ir_quarter(product, period, issuer)` - the reports covering the
   calendar quarter are read: one labelled with the quarter itself first, then
   one for a longer year to date ending with it (which may print the quarter
   as a column, or else gives the quarter as its year to date less the
   previous one), then the next year's same-quarter report for its
   comparative column. An HTML report is read as `earnings.py` reads an
   exhibit; a PDF is sent to Gemini as a document. Either way the answer is
   accepted only when code finds the quoted row in the document's own text
   (pdfplumber for a PDF), the value in that row, the period labels in the
   document and naming the target span, and a unit the document does not
   contradict.
"""

from __future__ import annotations

import base64
import calendar
import datetime as dt
import functools
import io
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from app.retrieval.earnings import (
    _NOT_OWN_SALES,
    _SCALES,
    _flat,
    declared_scale,
    names_quarter,
    passages,
    read_exhibit,
    stated_scale,
    value_in_quote,
)
from app.retrieval.gemini_search import (
    _FINISHED,
    DEFAULT_MODEL,
    MAX_WAIT_SECONDS,
    POLL_SECONDS,
    InteractionFailed,
    QuotaExceeded,
    _as_dict,
    is_quota,
    make_client,
    parse_interaction,
    parse_json_answer,
)
from app.retrieval.verify import DEFAULT_USER_AGENT, REFUSALS
from app.retrieval.xbrl import first_name, quarter_end

# An archive page must list reports for at least this many distinct periods;
# a page with one dated link is a single release, not the archive.
MIN_DATED_PERIODS = 2
# Documents read by the model per question; each is one no-search call.
MAX_READS = 6
# PDFs fetched from a period's HTML page when the page itself does not name
# the product (a results page linking its release, slides and data book).
MAX_CHILD_DOCUMENTS = 8
# Ancestors of a link searched for a period label printed beside it.
ANCESTOR_LEVELS = 4
ANCESTOR_TEXT_CHARS = 600

# ---------------------------------------------------------------- periods

_MONTH = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_MONTH_NUMBER = {name: i for i, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_Y4 = r"((?:19|20)\d\d)"
# A part of a year: its offset from the fiscal year's first month and length.
_PART = (r"((?:q[1-4]|[1-4]q)[\s-]?ytd|q[1-4]|[1-4]q|h[12]|[12]h|hy|9m|6m|half[- ]?year(?:ly)?|first[- ]half|interim|nine[- ]months?|"
         r"(?:first|second|third|fourth|1st|2nd|3rd|4th)[- ]quarter|full[- ]year|annual|year[- ]end)")
_YEAR = r"(fy|fiscal(?: year)?|cy)?\s?((?:19|20)\d\d|'?\d\d)(?:/(\d{1,2})(?![\d/]))?"
_SEP = r"[\s_\-/,'.]{0,3}(?:of\s+|results\s+|report\s+)?"
_ORDINAL = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
_SPAN_MONTHS = {"three": 3, "3": 3, "six": 6, "6": 6, "nine": 9, "9": 9, "twelve": 12, "12": 12}

_MONTHS_ENDED = re.compile(rf"(three|six|nine|twelve|3|6|9|12)[- ]months?\s+(?:period\s+)?end(?:ed|ing)\s+"
                           rf"(?:on\s+)?{_MONTH}\s*(?:\d{{1,2}},?\s*)?{_Y4}", re.IGNORECASE)
_MONTH_RANGE = re.compile(rf"(?<![a-z]){_MONTH}\s*(?:\d{{1,2}},?\s*)?(?:{_Y4}\s*)?(?:-|–|—|~|to|through)\s*"
                          rf"{_MONTH}\s*(?:\d{{1,2}},?\s*)?{_Y4}", re.IGNORECASE)
_PART_YEAR = re.compile(rf"(?<![a-z0-9]){_PART}{_SEP}{_YEAR}(?![\d])", re.IGNORECASE)
_YEAR_PART = re.compile(rf"(?<![a-z0-9]){_YEAR}{_SEP}{_PART}(?![a-z0-9])", re.IGNORECASE)
_FISCAL_YEAR = re.compile(r"(?<![a-z0-9])(fy|fiscal year|fiscal)\s?((?:19|20)\d\d|\d\d)(?:/(\d{1,2})(?![\d/]))?(?!\d)",
                          re.IGNORECASE)
_PART_ALONE = re.compile(rf"(?<![a-z0-9]){_PART}(?![a-z0-9])", re.IGNORECASE)
_BARE_YEAR = re.compile(r"(?<![\d])(?:19|20)\d\d(?![\d])")


def _add(ym: tuple[int, int], months: int) -> tuple[int, int]:
    index = ym[0] * 12 + ym[1] - 1 + months
    return index // 12, index % 12 + 1


@dataclass(frozen=True)
class Span:
    """A run of calendar months, both ends inclusive, as (year, month)."""

    start: tuple[int, int]
    end: tuple[int, int]

    @property
    def months(self) -> int:
        return (self.end[0] - self.start[0]) * 12 + self.end[1] - self.start[1] + 1

    def end_date(self) -> dt.date:
        year, month = self.end
        return dt.date(year, month, calendar.monthrange(year, month)[1])

    def shifted(self, months: int) -> Span:
        return Span(_add(self.start, months), _add(self.end, months))

    def describe(self) -> str:
        words = {3: "three", 6: "six", 9: "nine", 12: "twelve"}.get(self.months, str(self.months))
        end = self.end_date()
        first = calendar.month_name[self.start[1]]
        return (f"the {words} months ended {calendar.month_name[end.month]} {end.day}, {end.year} "
                f"({first} {self.start[0]} - {calendar.month_name[end.month]} {end.year})")

    def __str__(self) -> str:
        return f"{self.start[0]}-{self.start[1]:02d}..{self.end[0]}-{self.end[1]:02d}"


@dataclass(frozen=True)
class FiscalCalendar:
    """When an issuer's fiscal year starts, and which year names it.

    `named_by_start`: FY2023 starting April 2023 (true) or ending in 2023.
    With a January start the two agree.
    """

    start_month: int = 1
    named_by_start: bool = True

    def year_start(self, fiscal_year: int, end_month: int | None = None) -> tuple[int, int]:
        """The first month of fiscal year `fiscal_year`; `end_month` is a
        "FY2024/3" label's own statement of the month it ends."""
        if end_month:
            return _add((fiscal_year, end_month), -11)
        if self.start_month == 1 or self.named_by_start:
            return fiscal_year, self.start_month
        return fiscal_year - 1, self.start_month

    def label(self, span: Span) -> str | None:
        """How this calendar names `span`, if it is a fiscal quarter, half or
        year to date: "Q1 FY2023", "H1 FY2023", "9M FY2023"."""
        offset = (span.start[1] - self.start_month) % 12
        first = _add(span.start, -offset)
        fiscal_year = first[0] if (self.start_month == 1 or self.named_by_start) else first[0] + 1
        if span.months == 3:
            return f"Q{offset // 3 + 1} FY{fiscal_year}" if offset % 3 == 0 else None
        if offset == 0:
            return {6: f"H1 FY{fiscal_year}", 9: f"9M FY{fiscal_year}", 12: f"FY{fiscal_year}"}.get(span.months)
        return None


CALENDAR_YEAR = FiscalCalendar()


def _year(text: str) -> int:
    digits = text.strip("'")
    return int(digits) + 2000 if len(digits) == 2 else int(digits)


def _part_span(part: str) -> tuple[int, int] | None:
    """(offset from the fiscal year's start, months) for a part-of-year word."""
    p = re.sub(r"[\s-]", "", part.lower())
    if re.fullmatch(r"(?:q[1-4]|[1-4]q)ytd", p):
        return 0, 3 * int(re.sub(r"\D", "", p))
    if re.fullmatch(r"q[1-4]|[1-4]q", p):
        return 3 * (int(re.sub(r"\D", "", p)) - 1), 3
    if p in ("h1", "1h", "hy", "6m", "halfyear", "halfyearly", "firsthalf", "interim"):
        return 0, 6
    if p in ("h2", "2h"):
        return 6, 6
    if p in ("9m", "ninemonths", "ninemonth"):
        return 0, 9
    if p in ("fullyear", "annual", "yearend"):
        return 0, 12
    m = re.fullmatch(r"(first|second|third|fourth|1st|2nd|3rd|4th)quarter", p)
    if m:
        return 3 * (_ORDINAL[m.group(1)] - 1), 3
    return None


def parse_periods(text: str, fiscal: FiscalCalendar = CALENDAR_YEAR) -> list[tuple[Span, str]]:
    """Every period label in `text`, as (span, the label as printed), in order.

    Spelled-out months are calendar dates whatever the issuer's fiscal year:
    "six months ended June 30, 2023" and "April-June 2023". A quarter, half or
    year to date named against a year ("Q2 2023", "2Q23", "FY2023 Q2", "H1
    2023", "9M 2023", "full year 2023") is a part of that fiscal year under
    `fiscal`. "FY2023" alone is the whole fiscal year. A bare year is not a
    period: it is as often a publication date as a span.
    """
    found: list[tuple[int, int, Span, str]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e, _, _ in found)

    def keep(m: re.Match, span: Span | None) -> None:
        if span is not None and span.months in (3, 6, 9, 12) and free(m.start(), m.end()):
            found.append((m.start(), m.end(), span, m.group(0).strip()))

    for m in _MONTHS_ENDED.finditer(text):
        end = (int(m.group(3)), _MONTH_NUMBER[m.group(2).lower()[:3]])
        keep(m, Span(_add(end, 1 - _SPAN_MONTHS[m.group(1).lower()]), end))
    for m in _MONTH_RANGE.finditer(text):
        first, last, year = _MONTH_NUMBER[m.group(1).lower()[:3]], _MONTH_NUMBER[m.group(3).lower()[:3]], int(m.group(4))
        start_year = int(m.group(2)) if m.group(2) else (year if first <= last else year - 1)
        keep(m, Span((start_year, first), (year, last)))

    def fiscal_part(m: re.Match, part: str, prefix: str | None, year: str, end_month: str | None, year_first: bool) -> None:
        shape = _part_span(part)
        if shape is None:
            return
        digits = year.strip("'")
        # A two-digit year needs a prefix or a compact label beside it
        # ("FY23", "2Q23"); "Q2 23" in running text is too often something else.
        if len(digits) == 2 and not prefix and (year_first or not re.fullmatch(r"[qh]\d|\d[qh]|\dm", part.lower())):
            return
        start = fiscal.year_start(_year(year), int(end_month) if end_month and 1 <= int(end_month) <= 12 else None)
        first = _add(start, shape[0])
        keep(m, Span(first, _add(first, shape[1] - 1)))

    for m in _PART_YEAR.finditer(text):
        fiscal_part(m, m.group(1), m.group(2), m.group(3), m.group(4), False)
    for m in _YEAR_PART.finditer(text):
        fiscal_part(m, m.group(4), m.group(1), m.group(2), m.group(3), True)
    for m in _FISCAL_YEAR.finditer(text):
        end_month = int(m.group(3)) if m.group(3) and 1 <= int(m.group(3)) <= 12 else None
        start = fiscal.year_start(_year(m.group(2)), end_month)
        keep(m, Span(start, _add(start, 11)))
    found.sort(key=lambda f: f[0])
    return [(span, label) for _, _, span, label in found]


def quarter_span(period: str) -> Span | None:
    """Calendar quarter `period` ("Q2 2023", "2023Q2") as a span."""
    end = quarter_end(period)
    if end is None:
        return None
    return Span((end.year, end.month - 2), (end.year, end.month))


_FY_ENDING = re.compile(rf"fiscal year (?:ending|ended|ends|end)\s+(?:on\s+)?{_MONTH}", re.IGNORECASE)
_FY_SLASH = re.compile(r"(?<![a-z0-9])fy\s?(?:19|20)\d\d/(\d{1,2})(?![\d/])", re.IGNORECASE)
_FY_OPENS = re.compile(rf"(?:fy|fiscal year)\s?{_Y4}\s*\(\s*{_MONTH}\s*(?:\d{{1,2}},?\s*)?{_Y4}", re.IGNORECASE)
_Q1_OPENS = re.compile(rf"(?<![a-z0-9])(?:q1|1q|first quarter)(?:\s*fy\s?\d{{2,4}})?\s*[(:]\s*{_MONTH}", re.IGNORECASE)


def detect_calendar(text: str) -> FiscalCalendar | None:
    """The fiscal calendar a page states, or None if it states none.

    Read from "fiscal year ending March", "FY2024/3", "FY2023 (April 1,
    2023 - ..." and "Q1 (April - June)"; the most frequent start month wins.
    Which year names the fiscal year comes from a "FY2023 (April 2023" pairing
    when one is printed; without one it is the year the fiscal year starts in,
    the convention of the April-start issuers this exists for.
    """
    starts: Counter[int] = Counter()
    by_start: Counter[bool] = Counter()
    for m in _FY_ENDING.finditer(text):
        starts[_MONTH_NUMBER[m.group(1).lower()[:3]] % 12 + 1] += 1
    for m in _FY_SLASH.finditer(text):
        month = int(m.group(1))
        if 1 <= month <= 12:
            starts[month % 12 + 1] += 1
    for m in _FY_OPENS.finditer(text):
        month = _MONTH_NUMBER[m.group(2).lower()[:3]]
        starts[month] += 1
        if month != 1:
            by_start[int(m.group(1)) == int(m.group(3))] += 1
    for m in _Q1_OPENS.finditer(text):
        starts[_MONTH_NUMBER[m.group(1).lower()[:3]]] += 1
    if not starts:
        return calendar_from_release_dates(text)
    start = starts.most_common(1)[0][0]
    named_by_start = by_start.most_common(1)[0][0] if by_start else True
    return FiscalCalendar(start, named_by_start)


# "Q2/FY2026 Financial results November 4, 2026": a fiscal quarter beside the
# date its results came out.
_RELEASED = re.compile(rf"(?<![a-z0-9])(?:q([1-4])|([1-4])q)\s*/\s*fy\s?{_Y4}(?![\d/])"
                       rf"(?:(?!(?:q[1-4]|[1-4]q)\s*/\s*fy).){{0,60}}?"
                       rf"{_MONTH}\s+(\d{{1,2}}),?\s+{_Y4}", re.IGNORECASE | re.DOTALL)
# A results release trails its quarter's close by at least the time it takes to
# close the books, and by less than a third of a year; outside those bounds the
# pairing names a different calendar.
MIN_RELEASE_LAG_DAYS = 14
MAX_RELEASE_LAG_DAYS = 120


def calendar_from_release_dates(text: str) -> FiscalCalendar | None:
    """The fiscal calendar implied by fiscal quarters printed beside their
    release dates, for a page that never states its fiscal year.

    "Q1/FY2026 ... August 5, 2026" fits a year starting in April and named by
    its start (Q1 closes June 30). Every calendar under which each release
    falls a plausible time after its quarter's close is a candidate. The one
    that fits the most pairs wins, so one mislabelled link cannot veto the
    page; among those, the shortest lags win, since a release cannot precede
    the close and a later start month would put it too soon after. A calendar
    fitting no more than half the pairs is no answer.
    """
    pairs = []
    for m in _RELEASED.finditer(text):
        try:
            released = dt.date(int(m.group(6)), _MONTH_NUMBER[m.group(4).lower()[:3]], int(m.group(5)))
        except ValueError:
            continue
        pairs.append((int(m.group(1) or m.group(2)), int(m.group(3)), released))
    if not pairs:
        return None
    best = None
    for start in range(1, 13):
        for by_start in ((True,) if start == 1 else (True, False)):
            fiscal = FiscalCalendar(start, by_start)
            lags = []
            for quarter, fiscal_year, released in pairs:
                first = _add(fiscal.year_start(fiscal_year), 3 * (quarter - 1))
                lags.append((released - Span(first, _add(first, 2)).end_date()).days)
            fitting = [lag for lag in lags if MIN_RELEASE_LAG_DAYS <= lag <= MAX_RELEASE_LAG_DAYS]
            key = (-len(fitting), sum(fitting))
            if 2 * len(fitting) > len(lags) and (best is None or key < best[0]):
                best = (key, fiscal)
    return best[1] if best else None


# ---------------------------------------------------------------- fetching


@dataclass
class Document:
    """A fetched page or file. `url` is where it ended up after redirects."""

    url: str
    content: bytes
    content_type: str = ""
    _text: str | None = field(default=None, repr=False)

    @property
    def is_pdf(self) -> bool:
        return "pdf" in self.content_type.lower() or self.content[:5] == b"%PDF-"

    @property
    def text(self) -> str:
        """The document's text: pdfplumber's for a PDF, the visible text for HTML."""
        if self._text is None:
            if self.is_pdf:
                import pdfplumber

                with pdfplumber.open(io.BytesIO(self.content)) as pdf:
                    self._text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            else:
                from app.retrieval.single_product import filing_text

                self._text = filing_text(self.content)
        return self._text


class Refused(RuntimeError):
    """The host answered this declared client with a refusal (401/403/429/503)."""


def fetch_document(url: str) -> Document:
    """GET `url` as the declared client `verify.py` is - never a browser's
    User-Agent. A refusal raises `Refused`; any other failure propagates."""
    import httpx

    agent = os.environ.get("SEC_USER_AGENT") or DEFAULT_USER_AGENT
    response = httpx.get(url, headers={"User-Agent": agent}, follow_redirects=True, timeout=90)
    if response.status_code in REFUSALS:
        raise Refused(f"{response.status_code} from {urlparse(url).netloc}")
    response.raise_for_status()
    return Document(str(response.url), response.content, response.headers.get("content-type", ""))


# ---------------------------------------------------------------- listing


@dataclass(frozen=True)
class Report:
    """A link on an archive page, and the period it was assigned.

    `basis` says where the period came from: link | url | nearby | year_nearby
    (a bare part of a year in the link, its year from the URL or around it) |
    parent (a file linked from a period's own page).
    """

    url: str
    kind: str  # pdf | html
    span: Span
    label: str
    text: str
    basis: str


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _url_words(url: str) -> str:
    parsed = urlparse(url)
    return re.sub(r"[/_\-.=&?+]+", " ", unquote(f"{parsed.path} {parsed.query}"))


def _single(text: str, fiscal: FiscalCalendar) -> tuple[Span, str] | None:
    """The one period `text` names, or None if it names none or several."""
    found = parse_periods(text, fiscal)
    spans = {span for span, _ in found}
    return found[0] if len(spans) == 1 else None


def _kind(url: str) -> str:
    return "pdf" if urlparse(url).path.lower().endswith(".pdf") else "html"


def reports_on_page(document: Document, fiscal: FiscalCalendar) -> list[Report]:
    """Every link on a page that a period can be assigned to, deduplicated by URL."""
    import lxml.html

    try:
        root = lxml.html.fromstring(document.content)
    except (ValueError, TypeError, lxml.etree.ParserError):
        return []
    root.make_links_absolute(document.url, resolve_base_href=True)
    reports, seen = [], set()
    for anchor in root.iter("a"):
        href = (anchor.get("href") or "").split("#")[0]
        if not href.startswith("http") or href in seen or href.rstrip("/") == document.url.rstrip("/"):
            continue
        own = _clean(" ".join([*anchor.itertext(), anchor.get("title") or "", anchor.get("aria-label") or ""]))
        assigned = None
        for basis, text in (("link", own), ("url", _url_words(href))):
            single = _single(text, fiscal)
            if single:
                assigned = (*single, basis)
                break
        ancestors = list(anchor.iterancestors())[:ANCESTOR_LEVELS + 1]
        # A link that names its own part of a year ("Q3") takes only the year
        # from around it; a period printed nearby may be a sibling's.
        part = _PART_ALONE.search(own) if assigned is None else None
        if assigned is None and not part:
            for ancestor in ancestors:
                text = _clean(" ".join(ancestor.itertext()))[:ANCESTOR_TEXT_CHARS]
                if not parse_periods(text, fiscal):
                    continue
                single = _single(text, fiscal)
                if single:
                    assigned = (*single, "nearby")
                break
        if assigned is None and part:
            years = set(_BARE_YEAR.findall(_url_words(href)))
            for ancestor in ancestors:
                if len(years) == 1:
                    break
                years = set(_BARE_YEAR.findall(_clean(" ".join(ancestor.itertext()))[:ANCESTOR_TEXT_CHARS * 4]))
                if len(years) > 1:
                    break
            if len(years) == 1:
                single = _single(f"{part.group(0)} {years.pop()}", fiscal)
                if single:
                    assigned = (*single, "year_nearby")
        if assigned is None:
            continue
        seen.add(href)
        span, label, basis = assigned
        reports.append(Report(href, _kind(href), span, label, own, basis))
    return reports


@dataclass
class Archive:
    """An issuer's results-archive page, its reports and its fiscal calendar."""

    url: str
    reports: list[Report]
    fiscal: FiscalCalendar
    fiscal_stated: bool  # whether the page stated its calendar, or January was assumed


def load_archive(url: str, *, fetch: Callable = fetch_document, fiscal: FiscalCalendar | None = None) -> Archive:
    """Fetch an archive page and list its reports under its stated calendar."""
    page = fetch(url)
    stated = fiscal or detect_calendar(page.text)
    calendar_ = stated or CALENDAR_YEAR
    return Archive(page.url, reports_on_page(page, calendar_), calendar_, stated is not None)


def list_reports(archive_url: str, *, fetch: Callable = fetch_document,
                 fiscal: FiscalCalendar | None = None) -> list[Report]:
    """The links on `archive_url` that name a period, each assigned its span.

    `fiscal` defaults to the calendar the page itself states, else January.
    """
    return load_archive(archive_url, fetch=fetch, fiscal=fiscal).reports


def is_archive(archive: Archive) -> bool:
    """Whether a page lists reports for several distinct periods."""
    return len({r.span for r in archive.reports}) >= MIN_DATED_PERIODS


# ---------------------------------------------------------------- locating

ARCHIVE_PROMPT = """\
Find the page on {issuer}'s own website that lists its past quarterly and
annual financial results - the investor-relations archive with a link to each
period's results release, presentation, financial statements or data book
(HTML pages or PDFs). Prefer the page that lists the most periods. Do not give
a third-party site, a news article or a single period's release.

Answer with one JSON object and nothing else:
{{"archive_url": "<URL of that page>",
  "other_urls": ["<other pages on the issuer's site that list results by period, if any>"]}}
"""


def grounded_json(prompt: str, *, client: Any = None, model: str | None = None) -> tuple[dict[str, Any], list[str]]:
    """One Google-Search-grounded call: its JSON answer and the URLs it cited.

    Runs in background mode and is polled, as `gemini_search.ask_revenue`
    does. Raises `QuotaExceeded` on a 429.
    """
    model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    client = client or make_client()
    try:
        data = _as_dict(client.interactions.create(model=model, input=prompt, tools=[{"type": "google_search"}],
                                                   background=True))
        waited = 0.0
        while data.get("status") and data["status"] not in _FINISHED:
            if waited >= MAX_WAIT_SECONDS:
                raise InteractionFailed(f"{data.get('id')}: still {data['status']} after {waited:.0f} s")
            time.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
            data = _as_dict(client.interactions.get(data["id"]))
    except Exception as exc:
        if is_quota(exc):
            raise QuotaExceeded(f"{model}: {exc}") from exc
        raise
    if data.get("status") not in (None, "completed"):
        raise InteractionFailed(f"{data.get('id')}: {data.get('status')}: {data.get('errors')}")
    answer = parse_interaction(data, product="", period="", model=model)
    return answer.parsed or {}, [c.url for c in answer.citations if c.url]


def locate_archive(issuer: str, *, ask: Callable = grounded_json, fetch: Callable = fetch_document,
                   fiscal: FiscalCalendar | None = None) -> tuple[Archive | None, list[str]]:
    """`find_results_archive` without the cache: the archive, and why each
    candidate URL was refused."""
    parsed, cited = ask(ARCHIVE_PROMPT.format(issuer=issuer))
    candidates = [parsed.get("archive_url"), *(parsed.get("other_urls") or []), *cited]
    refusals, tried = [], set()
    for url in candidates:
        if not isinstance(url, str) or not url.startswith("http") or url in tried:
            continue
        tried.add(url)
        try:
            archive = load_archive(url, fetch=fetch, fiscal=fiscal)
        except Exception as exc:  # one unreadable candidate leaves the others
            refusals.append(f"{url}: {type(exc).__name__}: {exc}")
            continue
        if is_archive(archive):
            return archive, refusals
        refusals.append(f"{url}: {len({r.span for r in archive.reports})} dated period(s) linked")
    return None, refusals


@functools.lru_cache(maxsize=256)
def find_results_archive(issuer: str) -> Archive | None:
    """The issuer's results-archive page, or None.

    One grounded call proposes the page; it is accepted only if, fetched by
    this client, it links reports for several periods. Kept in memory for the
    process: a cache in front of the call that produces it, nothing on disk.
    """
    return locate_archive(issuer)[0]


# ---------------------------------------------------------------- reading

DOCUMENT_PROMPT = """\
{source}

Find the net sales {issuer} itself reports for the product "{product}" for
{described}.{fiscal_note} If the document prints the period beside others
(a prior year, a year to date, a single quarter), use the column for exactly
these months. If it prints regional lines and a total, give the total. If the
only line naming the product is a royalty, collaboration, licence, milestone
or alliance line on another company's sales, report it with that kind.

Answer with one JSON object and nothing else:
{{"found": true or false,
  "value": <number exactly as printed in the row>,
  "unit": "<thousands | millions | billions | units>",
  "currency": "<ISO code>",
  "kind": "<product_sales | royalty | collaboration | other>",
  "row_quote": "<the row or sentence holding the value, copied verbatim>",
  "period_labels": ["<each header cell or phrase above the value's column that
                     states its period, copied verbatim; empty if the row is a
                     sentence that states its own period>"]}}
{passages}"""

_FISCAL_NOTE = """
The issuer's fiscal year starts in {month}, so it may label these months
"{label}"; use the column for these calendar months, whatever it is called."""


def build_document_prompt(product: str, issuer: str | None, span: Span, fiscal: FiscalCalendar,
                          text_passages: str | None = None) -> str:
    label = fiscal.label(span) if fiscal.start_month != 1 else None
    return DOCUMENT_PROMPT.format(
        source=("The attached document is a company's published results report." if text_passages is None
                else "Below are passages from a company's published results report."),
        issuer=issuer or "the company",
        product=product,
        described=span.describe(),
        fiscal_note=_FISCAL_NOTE.format(month=calendar.month_name[fiscal.start_month], label=label) if label else "",
        passages="" if text_passages is None else f"\nPassages:\n{text_passages}\n",
    )


def document_reader(prompt: str, document: bytes | None = None, *, client: Any = None,
                    model: str | None = None) -> dict[str, Any]:
    """Ask Gemini, without search, about a prompt and optionally a PDF sent as
    a document content block; return its JSON answer (or {})."""
    client = client or make_client()
    model = model or os.environ.get("GEMINI_READER_MODEL") or DEFAULT_MODEL
    payload: Any = prompt
    if document is not None:
        payload = [{"type": "text", "text": prompt},
                   {"type": "document", "mime_type": "application/pdf",
                    "data": base64.b64encode(document).decode("ascii")}]
    try:
        reply = client.interactions.create(model=model, input=payload)
    except Exception as exc:
        if is_quota(exc):
            raise QuotaExceeded(f"{model}: {exc}") from exc
        raise
    data = _as_dict(reply)
    return parse_json_answer(data.get("output_text") or "") or {}


def names_span(evidence: str, span: Span, fiscal: FiscalCalendar) -> bool:
    """Whether period labels name exactly `span`.

    Labels that parse as periods must include `span`. Labels that parse as
    none fall back to `earnings.names_quarter` - only for a calendar-year
    issuer and a quarter, since its quarter numbers are calendar ones.
    """
    found = {s for s, _ in parse_periods(evidence, fiscal)}
    if found:
        return span in found
    return fiscal.start_month == 1 and span.months == 3 and names_quarter(evidence, span.end_date())


_RESTATED = re.compile(r"restat|re-present|represent|recast|as adjusted|revised", re.IGNORECASE)


def check_answer(answer: dict[str, Any], text: str, span: Span, fiscal: FiscalCalendar) -> tuple[str, str]:
    """`(status, reason)` for a model answer against the document's own text -
    `earnings.check`'s tests, with the period checked against any span."""
    if not answer.get("found"):
        return "not_found", "the model found no line for the product"
    flat = _flat(text)
    quote = str(answer.get("row_quote") or "")
    labels = [str(x) for x in (answer.get("period_labels") or []) if str(x).strip()]
    if not quote or _flat(quote) not in flat:
        return "quote_not_in_document", "the quoted row is not in the document's text"
    if not value_in_quote(answer.get("value"), quote):
        return "value_not_in_quote", "the value is not one of the quoted row's figures"
    missing = [x for x in labels if _flat(x) not in flat]
    if missing:
        return "wrong_period", f"period labels not in the document: {missing}"
    evidence = " ".join(labels) if labels else quote
    if not names_span(evidence, span, fiscal):
        return "wrong_period", f"{evidence[:120]!r} does not name {span}"
    if _RESTATED.search(evidence):
        return "restated_basis", f"the column is marked {_RESTATED.search(evidence).group()!r}"
    if answer.get("kind") != "product_sales" or _NOT_OWN_SALES.search(quote):
        return "not_own_sales", f"kind {answer.get('kind')!r}; row: {quote[:80]!r}"
    unit = str(answer.get("unit") or "").lower()
    if unit not in _SCALES:
        return "unit_conflict", f"unit {unit!r} is not a scale"
    stated = stated_scale(quote, answer.get("value"))
    if stated:
        return ("accepted", "") if stated == unit else ("unit_conflict", f"the row says {stated}, the answer says {unit}")
    declared = declared_scale(text)
    if len(declared) == 1 and unit != "units" and unit not in declared:
        return "unit_conflict", f"the document declares {declared.pop()}, the answer says {unit}"
    return "accepted", ""


@dataclass
class IRReading:
    """One document read for one span, and what checking the answer found."""

    url: str
    span: str
    status: str  # accepted | not_found | product_not_in_report | quote_not_in_document | value_not_in_quote | wrong_period | restated_basis | not_own_sales | unit_conflict | refused_by_host | error
    value: float | None = None  # in units of currency
    currency: str | None = None
    row_quote: str | None = None
    period_labels: list[str] = field(default_factory=list)
    method: str | None = None  # pdf | html_grid | html_text
    reason: str = ""


def _mentions(text: str, product: str) -> int:
    return len(re.findall(re.escape(first_name(product)), text, re.IGNORECASE))


def read_document(document: Document, product: str, issuer: str | None, span: Span, fiscal: FiscalCalendar,
                  ask: Callable) -> IRReading:
    """Read one fetched report for `span`. `ask(prompt, pdf_bytes_or_None)`
    returns the model's JSON answer."""
    reading = IRReading(document.url, str(span), "error")
    text = document.text
    if not _mentions(text, product):
        reading.status, reading.reason = "product_not_in_report", "the document's text never names the product"
        return reading
    if not document.is_pdf and fiscal.start_month == 1 and span.months == 3:
        # A calendar-year issuer's HTML quarter reads exactly as an exhibit does.
        exhibit = read_exhibit({"filer": issuer or "", "cik": "", "url": document.url}, product, span.end_date(),
                               f"Q{span.end[1] // 3} {span.end[0]}", get=lambda _url: document,
                               ask=lambda prompt: ask(prompt, None))
        reading.status, reading.reason = exhibit.status, exhibit.reason
        reading.status = "quote_not_in_document" if reading.status == "quote_not_in_exhibit" else reading.status
        reading.value, reading.currency, reading.row_quote = exhibit.value, exhibit.currency, exhibit.row_quote
        reading.period_labels = exhibit.period_labels
        reading.method = f"html_{exhibit.method or 'text'}"
        return reading
    if document.is_pdf:
        answer = ask(build_document_prompt(product, issuer, span, fiscal), document.content)
        reading.method = "pdf"
    else:
        answer = ask(build_document_prompt(product, issuer, span, fiscal, passages(text, product)), None)
        reading.method = "html_text"
    reading.status, reading.reason = check_answer(answer, text, span, fiscal)
    reading.row_quote = answer.get("row_quote")
    reading.period_labels = [str(x) for x in (answer.get("period_labels") or [])]
    if reading.status == "accepted":
        reading.value = float(answer["value"]) * _SCALES[str(answer["unit"]).lower()]
        reading.currency = str(answer.get("currency") or "").upper() or None
    return reading


@dataclass
class IRAnswer:
    """The answer for one product-quarter from the issuer's own site.

    `status`: found | not_found | no_archive | no_report | not_a_quarter |
    refused_by_host. `method`: direct (a document printed the quarter),
    comparative (the next year's report printed it as a prior-year column) or
    ytd_difference (a year to date less the previous one). `value` is in
    units of `currency`.
    """

    status: str
    product: str
    period: str
    issuer: str | None
    value: float | None = None
    currency: str | None = None
    url: str | None = None
    method: str | None = None
    scope: str | None = None
    evidence: str | None = None
    archive_url: str | None = None
    fiscal_start_month: int | None = None
    readings: list[IRReading] = field(default_factory=list)


class _Reader:
    """Fetches and reads documents for one question, within `MAX_READS`."""

    def __init__(self, product: str, issuer: str | None, fiscal: FiscalCalendar, fetch: Callable, ask: Callable):
        self.product, self.issuer, self.fiscal, self.fetch, self.ask = product, issuer, fiscal, fetch, ask
        self.documents: dict[str, Document | Exception] = {}
        self.readings: list[IRReading] = []
        self.reads = 0

    def get(self, url: str) -> Document | Exception:
        if url not in self.documents:
            try:
                self.documents[url] = self.fetch(url)
            except QuotaExceeded:
                raise
            except Exception as exc:  # recorded per document; others may still answer
                self.documents[url] = exc
        return self.documents[url]

    def documents_for(self, report: Report) -> list[Document]:
        """The report itself if it names the product, else the PDFs its page
        links (for a period's HTML page), most mentions first."""
        doc = self.get(report.url)
        if isinstance(doc, Exception):
            status = "refused_by_host" if isinstance(doc, Refused) else "error"
            self.readings.append(IRReading(report.url, str(report.span), status, reason=f"{type(doc).__name__}: {doc}"))
            return []
        if doc.is_pdf or _mentions(doc.text, self.product):
            return [doc]
        children = [r.url for r in reports_on_page(doc, self.fiscal) if r.kind == "pdf" and r.span == report.span]
        if not children:
            import lxml.html

            try:
                root = lxml.html.fromstring(doc.content)
                root.make_links_absolute(doc.url, resolve_base_href=True)
                # A period's own page often links its files without labels;
                # they inherit the page's period.
                children = [a.get("href") for a in root.iter("a") if _kind(a.get("href") or "") == "pdf"]
            except (ValueError, TypeError):
                children = []
        found = []
        for url in dict.fromkeys(children[:MAX_CHILD_DOCUMENTS]):
            child = self.get(url)
            if isinstance(child, Document) and _mentions(child.text, self.product):
                found.append(child)
        found.sort(key=lambda d: -_mentions(d.text, self.product))
        if not found:
            self.readings.append(IRReading(report.url, str(report.span), "product_not_in_report",
                                           reason="neither the page nor the files it links name the product"))
        return found

    def read(self, report: Report, span: Span) -> IRReading | None:
        """The first accepted reading of `span` among the report's documents."""
        for doc in self.documents_for(report):
            if not _mentions(doc.text, self.product):
                self.readings.append(IRReading(doc.url, str(span), "product_not_in_report",
                                               reason="the document's text never names the product"))
                continue
            if self.reads >= MAX_READS:
                return None
            self.reads += 1
            try:
                reading = read_document(doc, self.product, self.issuer, span, self.fiscal, self.ask)
            except QuotaExceeded:
                raise
            except Exception as exc:  # one unreadable document leaves the others
                reading = IRReading(doc.url, str(span), "error", reason=f"{type(exc).__name__}: {exc}")
            self.readings.append(reading)
            if reading.status == "accepted":
                return reading
        return None


def _plan(reports: list[Report], target: Span) -> tuple[list[Report], list[tuple[Report, list[Report]]], list[Report]]:
    """Reports printing the quarter itself; years to date ending with it,
    each with the reports for the year to date before it; and the next year's
    same-quarter reports."""
    exact = [r for r in reports if r.span == target]
    longer = sorted((r for r in reports if r.span.end == target.end and r.span.months > target.months),
                    key=lambda r: r.span.months)
    pairs = []
    for r in longer:
        previous = Span(r.span.start, _add(target.start, -1))
        pairs.append((r, [p for p in reports if p.span == previous]))
    later = [r for r in reports if r.span == target.shifted(12)]
    return exact, pairs, later


def _contains(outer: Span, inner: Span) -> bool:
    return outer.start <= inner.start and inner.end <= outer.end and outer != inner


def _sub_archive_reports(reports: list[Report], target: Span, fiscal: FiscalCalendar, reader: _Reader) -> list[Report]:
    """The reports listed on HTML pages for a longer period holding the
    quarter, or the same quarter a year on - an archive paginated by year,
    one level down. Pages that cannot be fetched are skipped."""
    known = {r.url for r in reports}
    found: list[Report] = []
    for report in reports:
        if report.kind != "html" or not (_contains(report.span, target) or _contains(report.span, target.shifted(12))):
            continue
        page = reader.get(report.url)
        if isinstance(page, Document):
            for child in reports_on_page(page, fiscal):
                if child.url not in known:
                    known.add(child.url)
                    found.append(child)
    return found


def read_ir_quarter(
    product: str,
    period: str,
    issuer: str,
    *,
    archive: Callable[[str], Archive | None] = find_results_archive,
    fetch: Callable = fetch_document,
    ask: Callable = document_reader,
    fiscal: FiscalCalendar | None = None,
) -> IRAnswer:
    """The product's sales for calendar quarter `period` from `issuer`'s own
    results archive.

    `fiscal` overrides the calendar the archive page states. Raises
    `QuotaExceeded` when Gemini's quota is spent, so the caller can record it.
    """
    target = quarter_span(period)
    if target is None:
        return IRAnswer("not_a_quarter", product, period, issuer)
    page = archive(issuer)
    if page is None:
        return IRAnswer("no_archive", product, period, issuer)
    reports = page.reports
    calendar_ = page.fiscal
    if fiscal is not None and fiscal != page.fiscal:
        reports, calendar_ = list_reports(page.url, fetch=fetch, fiscal=fiscal), fiscal
    answer = IRAnswer("no_report", product, period, issuer, archive_url=page.url,
                      fiscal_start_month=calendar_.start_month)
    reader = _Reader(product, issuer, calendar_, fetch, ask)
    reports = reports + _sub_archive_reports(reports, target, calendar_, reader)
    exact, pairs, later = _plan(reports, target)
    if not (exact or pairs or later):
        return answer

    def found(reading: IRReading, method: str, evidence: str) -> IRAnswer:
        answer.status, answer.value, answer.currency = "found", reading.value, reading.currency
        answer.url, answer.scope, answer.method, answer.evidence = reading.url, reading.row_quote, method, evidence
        answer.readings = reader.readings
        return answer

    for report in exact:
        reading = reader.read(report, target)
        if reading:
            return found(reading, "direct", f"{report.label} ({report.basis}); {reading.period_labels}")
    for report, previous in pairs:
        reading = reader.read(report, target)
        if reading:
            return found(reading, "direct", f"{report.label} ({report.basis}); {reading.period_labels}")
        current = reader.read(report, report.span)
        if current is None:
            continue
        for prior_report in previous:
            prior = reader.read(prior_report, prior_report.span)
            if prior is None:
                continue
            if prior.currency != current.currency:
                reader.readings.append(IRReading(prior.url, prior.span, "inputs_differ",
                                                 reason=f"currencies {current.currency} and {prior.currency}"))
                continue
            value = current.value - prior.value
            if value < 0:
                reader.readings.append(IRReading(prior.url, prior.span, "negative",
                                                 reason="a later year to date smaller than the earlier one"))
                continue
            derived = IRReading(current.url, str(target), "accepted", value, current.currency,
                                f"{current.row_quote} less {prior.row_quote}", method="ytd_difference")
            return found(derived, "ytd_difference",
                         f"{report.label} {current.value:g} less {prior_report.label} {prior.value:g}")
    for report in later:
        reading = reader.read(report, target)
        if reading:
            return found(reading, "comparative", f"{report.label} ({report.basis}); {reading.period_labels}")
    answer.readings = reader.readings
    refused = [r for r in reader.readings if r.status == "refused_by_host"]
    answer.status = "refused_by_host" if refused and len(refused) == len(reader.readings) else "not_found"
    return answer
