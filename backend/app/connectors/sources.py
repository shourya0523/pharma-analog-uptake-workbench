"""Finding the filings that report a product's quarterly sales.

This is the step every extraction score is conditional on, and the easiest one
to leave out of a measurement. Handing the pipeline a URL and asking whether it
can read the document tests the reader; only walking EDGAR from an issuer name
and a quarter tests the pipeline.

The walk is: resolve the issuer to a CIK, list the filings of its 8-K family
that carry item 2.02 (results of operations) in the window around the quarter
- an amendment furnishing that item is one of them - and take the EX-99
exhibits attached to them rather than the 8-K itself. That choice is what
`sec_include_8k` defaults to off for: the figures an earnings 8-K reports are
in its exhibits, and the filing's own document is assumed to be the cover that
points at them. It is an assumption about a form, not a measurement of one -
turning the flag on is how to find out where it does not hold.

Two rules that look like details and are not:

* An issuer is resolved by ticker first, then by an exact match on its
  normalised name, and an ambiguous name resolves to nothing. Matching on a
  prefix resolves a one-word query to whichever registrant happens to start
  with it, and a filing from the wrong company is worse than no filing.
* Every EX-99 exhibit of an earnings 8-K is read, not the first. A filer that
  separates its press release from its schedules puts the release in EX-99.1
  and the product sales tables in EX-99.2, so taking one exhibit per filing
  takes the one with no table in it, which reads as "this issuer does not
  disclose product sales".
"""

from __future__ import annotations

# ruff: noqa: BLE001
import asyncio
import html
import logging
import mimetypes
import re
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
from app.storage.filestore import FileStore

logger = logging.getLogger(__name__)

# Which forms report a year rather than a quarter. This picks the label a
# retrieved source carries; it decides nothing about what is read, and a form
# not named here is labelled quarterly.
ANNUAL_FORMS = frozenset({"10-K", "10-K405", "10-KT", "20-F", "40-F", "11-K"})


# How long after a period ends its report is filed: a 10-Q is due about 45
# days after its quarter and a 10-K about 90 after its year, so a window of
# filing dates reaches this far past the periods it means to cover.
REPORTING_LAG = timedelta(days=120)


# Shared across connector instances so concurrent jobs don't stampede EDGAR.
# The floor is SEC's published guidance; the pace above it is not guessed but
# observed, because what the endpoint will accept depends on who else is
# asking from the same address. A refusal slows every caller, and a spell
# without one speeds them back up.
_SEC_LOCK = asyncio.Lock()
_SEC_FLOOR_S = 0.12  # ~8 req/s, under SEC's 10/s guidance
_SEC_CEILING_S = 4.0
# How long the endpoint must go without refusing before the pace halves.
# Recovery is timed rather than counted: a counter of consecutive successes
# is reset by every refusal, so under a steady trickle of them - which is
# what a shared address gets - it never reaches its target and the pace
# stays at the ceiling for the rest of the process. Elapsed quiet cannot be
# starved that way. Each quiet window halves the pace, so the climb down
# from the ceiling takes log2(ceiling/floor) of them.
_SEC_RECOVERY_QUIET_S = 15.0

_sec_pace = _SEC_FLOOR_S
# Monotonic, and compared only against itself. `_last_sec_request` below is on
# the event loop's clock for the same reason: neither is a wall time.
_last_sec_refusal = 0.0
_last_sec_request = 0.0


def sec_pace() -> float:
    """The interval currently kept between requests, for tests and logging."""
    return _sec_pace


def sec_saw_refusal(retry_after: float | None = None) -> None:
    """EDGAR refused for load. Slow every caller, and take its own number
    when it gave one."""
    global _sec_pace, _last_sec_refusal
    _sec_pace = min(_SEC_CEILING_S, max(_sec_pace * 2, retry_after or 0.0))
    _last_sec_refusal = time.monotonic()


def sec_saw_success() -> None:
    """A request got through. Halve the pace once the endpoint has been quiet
    for a full window, so a run that was throttled early does not stay slow
    for the rest of its life."""
    global _sec_pace, _last_sec_refusal
    if _sec_pace <= _SEC_FLOOR_S:
        return
    if time.monotonic() - _last_sec_refusal < _SEC_RECOVERY_QUIET_S:
        return
    _sec_pace = max(_SEC_FLOOR_S, _sec_pace / 2)
    # The halving is what this window bought; the next one starts here.
    _last_sec_refusal = time.monotonic()


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


async def _sec_throttle() -> None:
    global _last_sec_request
    async with _SEC_LOCK:
        now = asyncio.get_event_loop().time()
        wait = _sec_pace - (now - _last_sec_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_sec_request = asyncio.get_event_loop().time()


def is_sec_host(url: str) -> bool:
    """Whether a URL addresses the SEC, read from its host.

    ``"sec.gov" in url`` is also true of ``https://sec.gov.example.com/`` and
    of any URL whose query string mentions the host, and what the pace and the
    request headers follow is who is being asked rather than what the text of
    the URL says.
    """
    host = (urlparse(url).hostname or "").lower()
    return host == "sec.gov" or host.endswith(".sec.gov")


# How long one document is worth waiting out a refusal for. A count of
# attempts is the wrong bound: four attempts with a doubling delay give up
# after seven seconds, which is nothing to a rate limit, and the document is
# then recorded as one that does not exist.
RETRY_BUDGET_S = 90.0


async def get_with_backoff(
    client: httpx.AsyncClient, url: str, *, budget_s: float = RETRY_BUDGET_S
) -> httpx.Response:
    """Fetch one URL, waiting out the refusals a host gives when asked too fast.

    Every read of a page in this module goes through here, because a host's
    rate limit belongs to the host and not to the caller: two fetchers against
    one endpoint, one of them polite, is one impolite fetcher.

    SEC returns 503 or 429 under load rather than a permanent error, and a
    single one costs a whole filing. It is worth waiting out: a document
    missing because of a rate limit reads downstream as an issuer that
    discloses nothing, so without this the same code answers differently from
    one run to the next.

    The pace is kept only for the SEC, whose limit this module knows and
    shares across every caller in the process. A request to any other host is
    still retried on the same budget - a refusal is a refusal - but it neither
    waits for nor moves that pace, so a slow investor-relations site cannot
    slow down EDGAR and a busy EDGAR cannot slow down the site.
    """
    sec = is_sec_host(url)
    deadline = asyncio.get_event_loop().time() + budget_s
    delay = 1.0
    attempt = 0
    while True:
        attempt += 1
        if sec:
            await _sec_throttle()
        last: Exception | None = None
        try:
            response = await client.get(url)
        except httpx.TransportError as exc:
            # A connection that drops is the same refusal without a status
            # line; it reads downstream exactly as a 503 would.
            if sec:
                sec_saw_refusal()
            last = exc
            logger.info("fetch_backoff error=%s attempt=%s url=%s", type(exc).__name__, attempt, url)
        else:
            if response.status_code not in (429, 503):
                if sec:
                    sec_saw_success()
                response.raise_for_status()
                return response
            if sec:
                sec_saw_refusal(_retry_after_seconds(response))
            logger.info(
                "fetch_backoff status=%s attempt=%s pace=%.2f url=%s",
                response.status_code, attempt, sec_pace(), url,
            )
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            if last is not None:
                raise last
            response.raise_for_status()
            return response
        await asyncio.sleep(min(delay, remaining))
        delay = min(delay * 2, 30.0)


def _content_type(doc: str) -> str:
    """What a stored document is, from its own name."""
    return mimetypes.guess_type(doc)[0] or "application/octet-stream"


def parse_filing_date(value: object) -> date | None:
    """Lenient ISO date parse for EDGAR filingDate values and caller-supplied bounds."""
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# Corporate suffixes carry no identity: "Acme Sciences, Inc." and "Acme
# Sciences Inc" are the same registrant, and the SEC title uses whichever the
# filer registered with. A snapshot of the forms of name the index's own
# titles are written with; it goes stale when a registrant carries a form this
# does not name, and that shows up as a company resolving under one spelling
# of its name and not under another.
_REGISTRANT_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "plc", "llc", "lp", "sa", "nv", "ag", "holdings", "group",
}

# The two spellings of a conjunction, which is one word however it is written:
# a registrant joins its name to its suffix with "&" in the SEC title and a
# caller writes "and", or the other way round. Dropped like a suffix rather
# than kept, because the word it joins to is itself usually a suffix: "Acme
# Sciences & Co" and "Acme Sciences and Company" are the same registrant and
# "Acme Sciences" is the name either one carries.
_REGISTRANT_CONNECTIVES = {"&", "and"}


def normalize_registrant(name: str) -> str:
    """A company name reduced to what identifies it, for exact comparison.

    Calderon Respiratory & Co, Calderon Respiratory and Company and Calderon
    Respiratory, Inc. all reduce to ``calderon respiratory``.
    """
    cleaned = re.sub(r"[^a-z0-9&\s]", " ", (name or "").lower())
    words = [
        word
        for word in cleaned.split()
        if word not in _REGISTRANT_SUFFIXES and word not in _REGISTRANT_CONNECTIVES
    ]
    return " ".join(words)


def exhibit_number(declared_type: str | None) -> str | None:
    """The exhibit a filing's declared document type names, or None.

    The type is EDGAR's own, taken from the filing's header: ``EX-99.1`` and
    ``EX-99.01`` and ``EX-99`` all name exhibit 99, ``EX-13`` names exhibit 13
    and ``EX-101.INS`` names exhibit 101. The number before the first dot is
    the family; everything after it is the filer's own numbering within it, so
    the family is read and the spelling of the rest is not.
    """
    match = re.match(r"EX-(\d+)", (declared_type or "").strip().upper())
    return match.group(1) if match else None


def _calculation_linkbase(documents: list[str]) -> str | None:
    """The calculation linkbase in one filing's directory.

    It is the file the filer's own arithmetic lives in - which element is
    added into which total and which is taken away - and every XBRL filing
    ships one beside the instance under the same stem with a ``_cal`` suffix.
    Matched on the suffix alone, so it needs nothing the instance match needs.
    """
    return next((name for name in documents if name and name.endswith("_cal.xml")), None)


# A form family, from the form a filing was filed under: the family is the
# form without its amendment or transition suffix, so 10-K/A and 10-KT are
# both the 10-K family. Read that way, an amendment travels with the form it
# amends, which is how the 8-K/A carrying a whole company's financials came
# to be excluded by a list that named only 8-K and 10-Q.
def form_family(form: str | None) -> str:
    return re.split(r"[/\s]", str(form or "").upper(), maxsplit=1)[0].rstrip("T")


# Which forms report a period. The annual half is the vocabulary this module
# already carries, read as families so a new spelling of one of them needs no
# edit here; the interim half is the quarterly and foreign-issuer forms that
# state a period of their own. A family absent from both is inspected at the
# cost of a directory listing rather than skipped.
INTERIM_FORMS = frozenset({"10-Q", "6-K"})
PERIODIC_FORM_FAMILIES = frozenset(
    {form_family(form) for form in ANNUAL_FORMS} | {form_family(f) for f in INTERIM_FORMS}
)


def reports_a_period(form: str | None) -> bool:
    """Whether a filing under this form states a period of its own.

    Every other form with inline XBRL - an 8-K, a proxy, a registration
    statement - carries a cover page and nothing else, which is why a budget
    filled newest-first by EDGAR's `isXBRL` flag alone held cover pages and
    no quarterly report.
    """
    return bool(form) and form_family(form) in PERIODIC_FORM_FAMILIES


def is_annual(form: str | None) -> bool:
    """Whether a form reports a year. `10-K/A` is its amendment, so it does."""
    return bool(form) and form_family(form) in {form_family(f) for f in ANNUAL_FORMS}


def states_item(items: str | None, item: str) -> bool:
    """Whether a filing's item list names this item.

    EDGAR writes the list as the codes separated by commas - ``2.02,9.01`` -
    so the item is a whole entry in it, not a substring of one. Read as a
    substring, a code is also found inside a longer one that happens to end
    the same way, and the filing is then read for a disclosure it never made.
    """
    return item in {entry.strip() for entry in str(items or "").split(",")}


def reading_order(form: str | None) -> int:
    """Which filings the primary pass reads first when a budget truncates it.

    An annual report states the most periods per document and a report that
    states a period of its own states at least one, so those come before a
    form whose own document is a cover page pointing at its exhibits. Derived
    from `is_annual` and `reports_a_period` rather than keyed on the form
    string: keyed on the string, every amendment falls past every form it
    amends and is read last or not at all.
    """
    if is_annual(form):
        return 0
    if reports_a_period(form):
        return 1
    return 2


# A tagged number under any namespace but the cover page's own. Matched on
# the raw instance rather than parsed, because the question is only whether
# there is anything to parse.
_FINANCIAL_FACT_RE = re.compile(rb"<(?!dei:)[\w.-]+:[\w.-]+\s[^>]*contextRef=")


def _holds_financial_facts(raw: bytes) -> bool:
    """Whether an instance states anything beyond its cover page.

    Every filing with inline XBRL ships an instance, and for most filings it
    holds the cover page's ``dei:`` facts and nothing else. Such an instance is
    not a periodic report whatever its form, and does not count against
    anything: the pipeline reads it, finds no product, and has spent a fetch.
    """
    return bool(_FINANCIAL_FACT_RE.search(raw or b""))


def _instance_document(documents: list[str]) -> str | None:
    """The XBRL instance in one filing's directory, in either era's spelling.

    A filing's facts live in its instance document, and how that document is
    named changed with inline XBRL. Before it, the instance was a plain
    ``acme-20160930.xml`` beside the filing's HTML; after it, the HTML *is* the
    instance and the filer ships an extracted copy as ``acme-20250930_htm.xml``.

    This selected on the ``_htm.xml`` spelling alone, so it saw the second and
    silently skipped the first - every filer's pre-2019 filings, discarded one
    at a time as "no instance". The comment here explained the resulting gap as
    the SEC's, saying a filing from before 2019 "yields an instance with no
    product facts in it", and that is not true. Those instances tag products on
    the ProductOrService axis; they were never fetched to find out.

    The anchor that works in both eras is the filing's own extension schema:
    the instance shares the ``.xsd``'s stem and the linkbases beside it
    (``_cal``, ``_def``, ``_lab``, ``_pre``) do not, so matching on the stem
    picks the instance without knowing the filer's ticker, the period, or which
    era the filing belongs to. A filing may carry more than one ``.xsd``, so the
    loop takes the first stem that has an instance beside it.
    """
    names = [name for name in documents if name]
    available = set(names)
    for schema in sorted(name for name in names if name.endswith(".xsd")):
        stem = schema[: -len(".xsd")]
        # Inline filings ship both the schema and an extracted instance; the
        # `_htm` copy is the instance and `{stem}.xml` is not present.
        for candidate in (f"{stem}_htm.xml", f"{stem}.xml"):
            if candidate in available:
                return candidate
    # A filing with no extension schema is unusual but not impossible; fall
    # back to the spelling this used to look for rather than to nothing.
    return next((name for name in names if name.endswith("_htm.xml")), None)


class SECConnector:
    """SEC EDGAR submissions + filing retrieval. Always stores audit row status."""

    SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
    TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
    ARCHIVES = "https://www.sec.gov/Archives/edgar/data"

    # The families whose primary document this pass reads by default, and the
    # two it reads only when asked. All four sets below are form *families*,
    # so an amendment is read wherever the form it amends is: a 10-K/A carries
    # the statements its 10-K carried, restated.
    #
    # SECONDARY is a snapshot of a judgement about volume, not about what the
    # forms report: an 8-K's own document is a cover page and the figures are
    # in the exhibits `_retrieve_earnings_exhibits` takes, and a foreign issuer
    # furnishes many 6-Ks per quarter. A family leaves this set when the pass
    # is measured to be reading its documents for less than they carry.
    SECONDARY = frozenset({"6-K", "8-K"})
    # `11-K` is an employee-benefit plan's own annual report. It states a
    # period, so `reports_a_period` accepts it and the instance pass spends a
    # directory listing finding out what it tags - but the period is the
    # plan's and the document names no product, so the primary pass does not
    # read it. A snapshot of what `11-K` is; stale if the SEC gave the form to
    # something else.
    NOT_THE_REGISTRANT = frozenset({"11-K"})
    # Derived from the module's own answer to "does this form state a period",
    # so a form it already calls periodic cannot be dropped here by a list
    # that was written before the form existed.
    PRIMARY = frozenset(
        PERIODIC_FORM_FAMILIES
        - {form_family(f) for f in SECONDARY}
        - {form_family(f) for f in NOT_THE_REGISTRANT}
    )
    # "Results of Operations and Financial Condition" - the item an earnings
    # release is furnished under. The SEC assigns the number; it is a snapshot
    # of the 8-K item schedule and would go stale only if that were renumbered.
    EARNINGS_ITEM = "2.02"
    # The form that item schedule belongs to. It travels with EARNINGS_ITEM and
    # goes stale with it. It is spelled as its own family, so a form is
    # compared to it by family and it needs no second call to say so; the
    # guard test is what keeps that true.
    EARNINGS_FORM = "8-K"
    # The exhibit family a press release and the schedules beside it are filed
    # under - Regulation S-K item 601's "additional exhibits". The SEC assigns
    # the number; it is a snapshot of that exhibit table and would go stale
    # only if the table were renumbered. It is the family, not a spelling: the
    # filing's declared type is read through `exhibit_number`, so EX-99,
    # EX-99.1 and EX-99.01 are one thing here and EX-13 is not it.
    EARNINGS_EXHIBIT = "99"
    # Older filings live in dated shards beside filings.recent. A bound keeps a
    # wide window from walking a filer's whole history.
    MAX_SUBMISSION_SHARDS = 4

    def __init__(self, file_store: FileStore) -> None:
        self.file_store = file_store
        self.settings = get_settings()
        self.headers = {
            "User-Agent": self.settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
        }

    async def resolve_cik(
        self, ticker: str | None = None, company_name: str | None = None
    ) -> str | None:
        """The registrant's CIK, or None rather than a guess.

        Either argument on its own is a question this can answer, so a caller
        holding only a name asks with only a name.

        A ticker is exact and is tried first. A ticker that names no
        registrant in the index is not an answer, though - it is a symbol we
        were handed that the SEC does not list - so the name is tried after
        it rather than instead of it.

        A company name is not exact: the SEC title carries punctuation and a
        corporate suffix that a caller rarely reproduces, so both sides are
        normalized before comparing. What this must never do is return the
        nearest match - an unanchored substring search resolves a one-word
        query to whichever registrant happens to contain it, and every figure
        taken from that company's filings would then be attributed to the
        company that was asked for, with nothing downstream able to notice.
        Several matches means the question was ambiguous, and the honest
        answer to an ambiguous question is no answer.
        """
        if not ticker and not company_name:
            return None
        async with httpx.AsyncClient(headers=self.headers, timeout=30) as client:
            resp = await self._get_with_retry(client, self.TICKER_MAP)
            data = resp.json()

        needle_t = (ticker or "").upper().strip()
        if needle_t:
            for row in data.values():
                if str(row.get("ticker", "")).upper() == needle_t:
                    return str(row["cik_str"]).zfill(10)

        needle_n = normalize_registrant(company_name or "")
        if not needle_n:
            return None
        matches = {
            str(row["cik_str"]).zfill(10)
            for row in data.values()
            if normalize_registrant(str(row.get("title", ""))) == needle_n
        }
        return matches.pop() if len(matches) == 1 else None

    def _cache_key(self, accession: str, doc: str) -> str:
        safe_doc = doc.replace("/", "_")
        return f"cache/sec/{accession.replace('-', '')}/{safe_doc}"

    async def _get_with_retry(
        self, client: httpx.AsyncClient, url: str, *, budget_s: float | None = None
    ) -> httpx.Response:
        """One EDGAR read. The waiting and the pace are `get_with_backoff`'s,
        which every other read of a page in this module also goes through."""
        return await get_with_backoff(
            client, url, budget_s=RETRY_BUDGET_S if budget_s is None else budget_s
        )

    async def _list_filing_documents(
        self, client: httpx.AsyncClient, cik_int: str, acc_nodash: str
    ) -> list[str]:
        """Document filenames inside one filing, via the EDGAR directory listing."""
        url = f"{self.ARCHIVES}/{cik_int}/{acc_nodash}/index.json"
        try:
            resp = await self._get_with_retry(client, url)
            items = resp.json().get("directory", {}).get("item", [])
        except Exception as exc:
            logger.warning("sec_index_failed accession=%s error=%s", acc_nodash, exc)
            return []
        return [item.get("name", "") for item in items if item.get("name")]

    async def _declared_documents(
        self, client: httpx.AsyncClient, cik_int: str, accession: str
    ) -> list[tuple[str, str]]:
        """(declared type, filename) for every document one filing contains.

        The filing states what each of its documents is - the type the filer
        submitted it under, beside the name the filer's agent happened to give
        it. The directory listing does not: ``index.json`` carries a display
        icon where a type would be, so a reader of the listing alone can only
        guess an exhibit from its filename, and filing agents name exhibits
        however they like.

        Read from the filing's own header page, which costs the same single
        request the directory listing costs. The page serves the submission's
        SGML with its angle brackets escaped, one ``<DOCUMENT>`` block per
        document; the block is the unit, so a document missing a description
        or a sequence still yields its type and its name.
        """
        acc_nodash = accession.replace("-", "")
        url = f"{self.ARCHIVES}/{cik_int}/{acc_nodash}/{accession}-index-headers.html"
        try:
            resp = await self._get_with_retry(client, url)
            page = html.unescape(resp.text)
        except Exception as exc:
            logger.warning("sec_header_failed accession=%s error=%s", accession, exc)
            return []
        declared: list[tuple[str, str]] = []
        for block in page.split("<DOCUMENT>")[1:]:
            kind = re.search(r"<TYPE>([^\n<]*)", block)
            name = re.search(r"<FILENAME>([^\n<]*)", block)
            if kind and name and name.group(1).strip():
                declared.append((kind.group(1).strip(), name.group(1).strip()))
        return declared

    async def _fetch_document(
        self,
        client: httpx.AsyncClient,
        *,
        url: str,
        accession: str,
        doc: str,
        run_id: str,
        job_id: str,
        source_id: str,
    ) -> tuple[bytes, bool, str]:
        """Return (bytes, from_cache, storage key) for one filing document.

        One document, one stored object. It used to be written twice - once
        under the accession it belongs to, and once more per job under the
        source's own id - with identical bytes both times, so a sweep stored
        every filing as many times as there were products citing it. The
        accession and the document name are what identify a filing, and two
        jobs reading the same filing are reading the same bytes.

        The key keeps the document's own name, so what is stored says what it
        is: the per-job copy was always written as ``.html``, and an exhibit
        filed as a PDF was then parsed as though it were markup.
        """
        cache_key = self._cache_key(accession, doc)
        cached = await self._read_cache(cache_key)
        from_cache = cached is not None
        if cached is None:
            resp = await self._get_with_retry(client, url)
            raw = resp.content
            await self.file_store.put(cache_key, raw, _content_type(doc))
        else:
            raw = cached
        return raw, from_cache, cache_key

    async def _filings_covering(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        cik: str,
        since: date | None,
        until: date | None,
    ) -> dict[str, Any]:
        """The submissions index over a date window, not just the recent page.

        ``filings.recent`` holds roughly the last thousand filings and nothing
        older; everything before that lives in the shards named by
        ``filings.files``, each with the range it covers. Reading only the
        recent page makes a company's earlier years invisible - a quarterly
        series from 2005 retrieves nothing at all and reports it as "no
        relevant filings", which looks like the company never filed.

        Only shards whose range overlaps the window are fetched, so a query
        about last quarter still costs a single request.
        """
        recent = payload.get("filings", {}).get("recent", {}) or {}
        shards = payload.get("filings", {}).get("files", []) or []
        if not shards or (since is None and until is None):
            return recent

        merged: dict[str, Any] = {
            key: list(value) for key, value in recent.items() if isinstance(value, list)
        }
        fetched = 0
        for shard in shards:
            if fetched >= self.MAX_SUBMISSION_SHARDS:
                break
            covers_from, covers_to = shard.get("filingFrom"), shard.get("filingTo")
            if since and covers_to and covers_to < since.isoformat():
                continue
            if until and covers_from and covers_from > until.isoformat():
                continue
            name = shard.get("name")
            if not name:
                continue
            try:
                extra = await self._get_with_retry(client, f"https://data.sec.gov/submissions/{name}")
                block = extra.json()
            except Exception as exc:
                logger.info("sec_submissions_shard_failed name=%s error=%s", name, exc)
                continue
            fetched += 1
            for key, value in block.items():
                if isinstance(value, list) and key in merged:
                    merged[key].extend(value)
        return merged or recent

    async def _retrieve_earnings_exhibits(
        self,
        client: httpx.AsyncClient,
        *,
        run_id: str,
        job_id: str,
        cik: str,
        recent: dict[str, Any],
        max_exhibits: int,
        since: date | None = None,
        until: date | None = None,
    ) -> list[RetrievedSource]:
        """Fetch exhibit 99.x earnings releases from 8-K item 2.02 filings.

        Quarterly product-level net sales are disclosed in these exhibits; the 8-K
        primary document is only a cover page, so retrieving it yields no revenue.
        Without a date bound this takes the most recent filings; ``since``/``until``
        target a historical window instead, which keeps a backfill bounded.
        """
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        items = recent.get("items", [])
        cik_int = str(int(cik))

        # The budget counts filings, not exhibits, because a filing is a
        # quarter and its exhibits are one disclosure split across documents.
        # Counting exhibits truncates mid-filing: where a filer attaches two
        # EX-99s to each 8-K, a budget of six exhibits buys three quarters and
        # spends its last on a press release while leaving behind the
        # product-sales schedule that belongs with it.
        sources: list[RetrievedSource] = []
        filings_read = 0
        # Inside a window, every earnings filing the window holds: a
        # thirteen-month window has four or five of them, and a fixed budget
        # taken newest-first dropped its oldest quarter whenever the issuer
        # furnished other item 2.02 filings in between. Outside a window the
        # cap is what bounds a request for "the recent releases".
        bounded = since is not None or until is not None
        for i, form in enumerate(forms):
            if not bounded and filings_read >= max_exhibits:
                break
            # The 8-K family, not the string "8-K": item 2.02 belongs to the
            # family, and an amendment furnishing it is furnishing the same
            # results the original did. Whether that second reading agrees
            # with the first is a question for the reader, and it cannot be
            # asked of a document retrieval never fetched.
            if form_family(form) != self.EARNINGS_FORM:
                continue
            filing_items = items[i] if i < len(items) else ""
            if not states_item(filing_items, self.EARNINGS_ITEM):
                continue
            accession = accessions[i]
            fdate = filing_dates[i] if i < len(filing_dates) else None
            filed_on = parse_filing_date(fdate)
            if (since and (filed_on is None or filed_on < since)) or (
                until and (filed_on is None or filed_on > until)
            ):
                continue
            acc_nodash = accession.replace("-", "")
            # What the filing says its documents are, not what they are called.
            # A filing agent names the release `ex_100200.htm` or
            # `q4-2025xearningsrelease.htm` or `acme-20260211xex991.htm`, and
            # only the third of those states the exhibit in its name. All three
            # are declared EX-99.1 by the filing that carries them.
            declared = await self._declared_documents(client, cik_int, accession)
            exhibits = [
                name
                for kind, name in declared
                if exhibit_number(kind) == self.EARNINGS_EXHIBIT
            ]
            if not exhibits:
                logger.info("sec_no_earnings_exhibit accession=%s date=%s", accession, fdate)
                continue
            # Every exhibit, not the first one. An issuer that separates its
            # press release from its schedules puts the prose in EX-99.1 and the
            # product-level sales in EX-99.2, and taking one exhibit per filing
            # takes the wrong one: such a release carries no table at all while
            # the schedule beside it carries every product. Nothing in the
            # numbering says which is which, so the way to not choose wrongly is
            # not to choose - reading an exhibit that holds no product table
            # costs a parse, and skipping the one that does costs the quarter.
            filings_read += 1
            for doc in exhibits:
                sid = new_id()
                url = f"{self.ARCHIVES}/{cik_int}/{acc_nodash}/{doc}"
                try:
                    _raw, from_cache, stored_key = await self._fetch_document(
                        client,
                        url=url,
                        accession=accession,
                        doc=doc,
                        run_id=run_id,
                        job_id=job_id,
                        source_id=sid,
                    )
                    sources.append(
                        RetrievedSource(
                            source_id=sid,
                            source_type=SourceType.EARNINGS_RELEASE,
                            url=url,
                            title=f"{form} EX-99 earnings release {fdate or ''}".strip(),
                            source_date=date.fromisoformat(fdate) if fdate else None,
                            filing_type=form,
                            accession_number=accession,
                            storage_key=stored_key,
                            retrieval_status=RetrievalStatus.SUCCESS,
                            metadata={
                                "cik": cik,
                                "from_cache": from_cache,
                                "exhibit_document": doc,
                                "filing_items": filing_items,
                            },
                            notes="sec_cache_hit" if from_cache else None,
                        )
                    )
                except Exception as exc:
                    sources.append(
                        RetrievedSource(
                            source_id=sid,
                            source_type=SourceType.EARNINGS_RELEASE,
                            url=url,
                            title=f"{form} EX-99 earnings release {fdate or ''}".strip(),
                            filing_type=form,
                            accession_number=accession,
                            retrieval_status=RetrievalStatus.FAILED,
                            notes=str(exc),
                            metadata={"cik": cik},
                        )
                    )
        logger.info(
            "sec_earnings_exhibits cik=%s retrieved=%s max=%s",
            cik,
            len(sources),
            max_exhibits,
        )
        return sources

    async def _retrieve_xbrl_instances(
        self,
        client: httpx.AsyncClient,
        *,
        run_id: str,
        job_id: str,
        cik: str,
        recent: dict[str, Any],
        max_filings: int,
        since: date | None,
        until: date | None,
    ) -> list[RetrievedSource]:
        """The tagged instance from each filing in this window that carries one.

        A report states its product revenue in XBRL - the period, the unit and
        the product as declared facts rather than as a table to read - and
        which filings do that is not a property of the form. This asked only
        10-Q and 10-K, which is a domestic filer's shape; a foreign private
        issuer reports its quarter on a 6-K, and one of them tags the whole
        product schedule inline. Asking by form read those filings as untagged
        when they carry hundreds of product facts.

        So the form is not consulted. EDGAR states per filing whether it has
        XBRL, which is the same question without the guess, and turns a
        thousand filings into a few dozen; `_instance_document` then confirms
        it from the filing's own directory. A filing the index says nothing
        about is inspected anyway, up to a budget, so a missing flag costs
        requests rather than coverage.

        It reaches back only as far as the filer's own tagging does, and that
        is a per-filer fact rather than a date. Nothing here needs to know when
        each filer started - the reader simply finds nothing, which is the
        correct answer for a filing that has nothing.
        """
        cik_int = str(int(cik))
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        tagged = recent.get("isXBRL", []) or []
        sources: list[RetrievedSource] = []
        # Directory listings for filings the index does not classify. A bound on
        # requests, not a claim about which filings are worth reading.
        unclassified_budget = 25
        # Inside a window the window is the bound: it holds one periodic report
        # per fiscal period it covers, and nothing here knows a better number.
        # A fixed budget taken newest-first was filled by the cover-page
        # instances every 8-K and proxy carries, and the 10-Qs were never
        # reached. Outside a window the caller's cap still applies.
        bounded = since is not None or until is not None
        for index, form in enumerate(forms):
            if not bounded and len(sources) >= max_filings:
                break
            if index < len(tagged):
                if not tagged[index]:
                    continue
            elif unclassified_budget <= 0:
                continue
            else:
                unclassified_budget -= 1
            if not reports_a_period(form):
                continue
            filed_on = parse_filing_date(filing_dates[index] if index < len(filing_dates) else None)
            if (since and (filed_on is None or filed_on < since)) or (
                until and (filed_on is None or filed_on > until)
            ):
                continue
            accession = accessions[index]
            acc_nodash = accession.replace("-", "")
            documents = await self._list_filing_documents(client, cik_int, acc_nodash)
            instance = _instance_document(documents)
            if not instance:
                logger.info("sec_no_xbrl_instance accession=%s form=%s", accession, form)
                continue
            sid = new_id()
            url = f"{self.ARCHIVES}/{cik_int}/{acc_nodash}/{instance}"
            try:
                raw, from_cache, stored_key = await self._fetch_document(
                    client, url=url, accession=accession, doc=instance,
                    run_id=run_id, job_id=job_id, source_id=sid,
                )
            except Exception as exc:
                logger.info("sec_xbrl_fetch_failed accession=%s error=%s", accession, exc)
                continue
            if not _holds_financial_facts(raw):
                logger.info("sec_xbrl_cover_page_only accession=%s form=%s", accession, form)
                continue
            # The filing's arithmetic, so the reader can tell a sale from a
            # cost of one without counting or guessing. Its absence is not a
            # failure; the reader then asks about what it cannot place.
            calculation_key = None
            calculation = _calculation_linkbase(documents)
            if calculation:
                try:
                    _raw, _cached, calculation_key = await self._fetch_document(
                        client, url=f"{self.ARCHIVES}/{cik_int}/{acc_nodash}/{calculation}",
                        accession=accession, doc=calculation,
                        run_id=run_id, job_id=job_id, source_id=f"{sid}-cal",
                    )
                except Exception as exc:
                    logger.info("sec_calculation_fetch_failed accession=%s error=%s", accession, exc)
            sources.append(
                RetrievedSource(
                    source_id=sid,
                    source_type=(SourceType.ANNUAL_REPORT if is_annual(form)
                                 else SourceType.QUARTERLY_REPORT),
                    url=url,
                    title=f"{form} XBRL instance {filing_dates[index] if index < len(filing_dates) else ''}".strip(),
                    source_date=filed_on,
                    filing_type=form,
                    accession_number=accession,
                    storage_key=stored_key,
                    retrieval_status=RetrievalStatus.SUCCESS,
                    metadata={"cik": cik, "from_cache": from_cache, "xbrl_instance": True,
                              "calculation_key": calculation_key},
                )
            )
        logger.info("sec_xbrl_instances cik=%s retrieved=%s", cik, len(sources))
        return sources

    async def retrieve(
        self,
        *,
        run_id: str,
        job_id: str,
        cik: str | None,
        ticker: str | None,
        company_name: str | None,
        max_filings: int | None = None,
        include_primary: bool = True,
        include_earnings: bool | None = None,
        include_xbrl: bool = False,
        earnings_since: date | None = None,
        earnings_until: date | None = None,
    ) -> list[RetrievedSource]:
        """Retrieve primary filings and/or 8-K earnings-release exhibits.

        Primary filings (10-K/10-Q) are large inline-XBRL documents that carry annual
        totals; earnings exhibits are small and carry quarterly product breakouts. A
        caller after quarterly revenue can skip the primary filings entirely.
        """
        settings = self.settings
        max_filings = max_filings if max_filings is not None else settings.sec_max_filings
        include_8k = settings.sec_include_8k
        include_earnings = settings.sec_earnings_exhibits if include_earnings is None else include_earnings
        allowed = set(self.PRIMARY) | (self.SECONDARY if include_8k else set())

        sources: list[RetrievedSource] = []
        resolved = cik.zfill(10) if cik else await self.resolve_cik(ticker, company_name)
        if not resolved:
            sources.append(
                RetrievedSource(
                    source_type=SourceType.SEC_FILING,
                    url="https://www.sec.gov/edgar/searchedgar/companysearch",
                    title="SEC CIK resolution",
                    retrieval_status=RetrievalStatus.FAILED,
                    notes="Could not resolve CIK from ticker/company name",
                )
            )
            return sources

        async with httpx.AsyncClient(headers=self.headers, timeout=60, follow_redirects=True) as client:
            sub_url = self.SUBMISSIONS.format(cik=resolved)
            try:
                sub = await self._get_with_retry(client, sub_url)
                payload = sub.json()
            except Exception as exc:
                # The index is the whole of what EDGAR knows about the issuer;
                # without it there is no filing to read, and a job that goes on
                # from here is answering from whatever the web search finds.
                logger.warning("sec_submissions_failed cik=%s error=%s: %s", resolved, type(exc).__name__, exc)
                sources.append(
                    RetrievedSource(
                        source_type=SourceType.SEC_FILING,
                        url=sub_url,
                        title="SEC submissions",
                        retrieval_status=RetrievalStatus.FAILED,
                        notes=f"{type(exc).__name__}: {exc}",
                        metadata={"cik": resolved},
                    )
                )
                return sources

            recent = await self._filings_covering(
                client, payload, resolved, earnings_since, earnings_until
            )
            forms = recent.get("form", [])
            accessions = recent.get("accessionNumber", [])
            primary = recent.get("primaryDocument", [])
            filing_dates = recent.get("filingDate", [])

            # A filing reports a period that ended before it, so the filings
            # that report a window's periods are not the filings inside it:
            # the window is widened by one reporting lag at each end. Forward,
            # because a period ending just inside the window is reported after
            # it closes; backward, because a window may open after the report
            # of a period it asks for was already filed.
            #
            # The same lag both ways, because it is the same lag: a filing
            # reports a period that ended one lag before it, whichever end of
            # the window that period sits at. A longer reach backward buys
            # filings that can only report periods older than anything asked
            # for.
            since_bound = earnings_since - REPORTING_LAG if earnings_since else None
            until_bound = earnings_until + REPORTING_LAG if earnings_until else None

            # Both the gate and the order read the form as its family, so an
            # amendment is read where the form it amends is read and in the
            # same place in the queue. Compared raw, `10-K/A` is in neither
            # the allowed set nor the order, so a restatement would be dropped
            # by the first test and would sort behind everything by the second.
            indexed: list[tuple[int, int, str]] = []
            for i, form in enumerate(forms):
                if form_family(form) not in allowed:
                    continue
                indexed.append((reading_order(form), i, form))
            indexed.sort(key=lambda t: (t[0], t[1]))

            picked = 0
            for _pri, i, form in indexed if include_primary else []:
                if picked >= max_filings:
                    break
                accession = accessions[i]
                doc = primary[i]
                fdate = filing_dates[i] if i < len(filing_dates) else None
                # The window applies here too. `_filings_covering` goes to the
                # trouble of merging the archive shards so a 2005 quarter can
                # be reached at all, and then this loop took the newest 10-K
                # and 10-Q on the list regardless: a job for 2005 was handed
                # the 2026 annual report, which says nothing about 2005. Every
                # pre-2010 quarter was being asked of the wrong documents, and
                # the era looked unreachable when it was unqueried.
                filed_on = parse_filing_date(fdate)
                if since_bound and (filed_on is None or filed_on < since_bound):
                    continue
                if until_bound and (filed_on is None or filed_on > until_bound):
                    continue
                acc_nodash = accession.replace("-", "")
                cik_int = str(int(resolved))
                url = f"{self.ARCHIVES}/{cik_int}/{acc_nodash}/{doc}"
                sid = new_id()
                cache_key = self._cache_key(accession, doc)

                try:
                    # Per-job copy for audit trail (cheap local copy; S3 would be multipart later)
                    _raw, from_cache, stored_key = await self._fetch_document(
                        client,
                        url=url,
                        accession=accession,
                        doc=doc,
                        run_id=run_id,
                        job_id=job_id,
                        source_id=sid,
                    )
                    sources.append(
                        RetrievedSource(
                            source_id=sid,
                            source_type=SourceType.SEC_FILING,
                            url=url,
                            title=f"{form} {fdate or ''}".strip(),
                            source_date=date.fromisoformat(fdate) if fdate else None,
                            filing_type=form,
                            accession_number=accession,
                            raw_text=None,
                            storage_key=stored_key,
                            retrieval_status=RetrievalStatus.SUCCESS,
                            metadata={"cik": resolved, "from_cache": from_cache, "cache_key": cache_key},
                            notes="sec_cache_hit" if from_cache else None,
                        )
                    )
                except Exception as exc:
                    sources.append(
                        RetrievedSource(
                            source_id=sid,
                            source_type=SourceType.SEC_FILING,
                            url=url,
                            title=f"{form} {fdate or ''}".strip(),
                            filing_type=form,
                            accession_number=accession,
                            retrieval_status=RetrievalStatus.FAILED,
                            notes=str(exc),
                            metadata={"cik": resolved},
                        )
                    )
                picked += 1

            if include_xbrl:
                sources.extend(
                    await self._retrieve_xbrl_instances(
                        client,
                        run_id=run_id,
                        job_id=job_id,
                        cik=resolved,
                        recent=recent,
                        max_filings=settings.sec_max_earnings_exhibits,
                        since=earnings_since,
                        until=earnings_until,
                    )
                )

            if include_earnings:
                sources.extend(
                    await self._retrieve_earnings_exhibits(
                        client,
                        run_id=run_id,
                        job_id=job_id,
                        cik=resolved,
                        recent=recent,
                        max_exhibits=settings.sec_max_earnings_exhibits,
                        # The same widened bounds the primary pass uses, and
                        # for the same reason: an earnings release reports a
                        # quarter that ended before it, so the release that
                        # states the window's last quarter is filed after the
                        # window closes. Handed the raw window, the two passes
                        # disagreed about which filings report a period the
                        # run asked for, and nothing said which was right.
                        since=since_bound,
                        until=until_bound,
                    )
                )

        if not sources:
            sources.append(
                RetrievedSource(
                    source_type=SourceType.SEC_FILING,
                    url=self.SUBMISSIONS.format(cik=resolved),
                    title="SEC filings search",
                    retrieval_status=RetrievalStatus.PARTIAL,
                    notes="No relevant filings found in recent submissions",
                    metadata={"cik": resolved},
                )
            )
        return sources

    async def _read_cache(self, key: str) -> bytes | None:
        try:
            if not await self.file_store.exists(key):
                return None
            return await self.file_store.get(key)
        except Exception:
            return None


# A page we were pointed at, rather than one EDGAR's index led us to. The
# pointer may come from a person or from a model, and either way the bytes
# have to be ours: a citation is only checkable against a document we hold.
PAGE_BYTES_LIMIT = 16_000_000
_PRIVATE_HOSTS = ("localhost", "127.", "0.", "10.", "192.168.", "169.254.", "[::1]")


def _is_fetchable(url: str) -> bool:
    """Whether a URL is one we are willing to ask for.

    The URL can come from a model, so the scheme is checked and an address
    inside this machine or its network is refused: a fetch is a request made
    on our own behalf, and the model does not decide where.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host.endswith((".internal", ".local")):
        return False
    return not host.startswith(_PRIVATE_HOSTS)


async def fetch_page(
    file_store: FileStore, *, run_id: str, job_id: str, source_id: str, url: str,
    user_agent: str,
) -> tuple[bytes, str, str]:
    """Fetch one page and store it. Returns (content, content type, storage key).

    Raises on anything that leaves us without the document, because a source
    we could not fetch is not a source: its quote would be checkable only
    against whatever handed us the link.

    The read goes through `get_with_backoff`, the same fetcher EDGAR's own
    walk uses, so a page that happens to be on sec.gov is asked for at the
    pace this process is keeping with the SEC. A link handed to us by a person
    or a model is as often an EDGAR archive URL as anything else, and a second
    fetcher against one host at its own pace is what a rate limit counts.
    """
    if not _is_fetchable(url):
        raise ValueError(f"refusing to fetch {url!r}")
    headers: dict[str, str] = {"User-Agent": user_agent}
    if is_sec_host(url):
        headers["Accept-Encoding"] = "gzip, deflate"
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers=headers) as client:
        resp = await get_with_backoff(client, url)
        content_type = resp.headers.get("content-type", "text/html")
        content = resp.content[:PAGE_BYTES_LIMIT]
    ext = "pdf" if "pdf" in content_type or url.lower().endswith(".pdf") else "html"
    key = f"sources/{run_id}/{job_id}/{source_id}.{ext}"
    await file_store.put(key, content, content_type)
    return content, content_type, key


class ManualURLConnector:
    def __init__(self, file_store: FileStore) -> None:
        self.file_store = file_store
        self.settings = get_settings()

    async def retrieve(self, *, run_id: str, job_id: str, url: str) -> list[RetrievedSource]:
        sid = new_id()
        if not url:
            return []
        try:
            _content, content_type, key = await fetch_page(
                self.file_store, run_id=run_id, job_id=job_id, source_id=sid, url=url,
                user_agent=self.settings.sec_user_agent,
            )
            return [
                RetrievedSource(
                    source_id=sid,
                    source_type=SourceType.USER_URL,
                    url=url,
                    title=url,
                    storage_key=key,
                    retrieval_status=RetrievalStatus.SUCCESS,
                    metadata={"content_type": content_type},
                )
            ]
        except Exception as exc:
            return [
                RetrievedSource(
                    source_id=sid,
                    source_type=SourceType.USER_URL,
                    url=url,
                    title=url,
                    retrieval_status=RetrievalStatus.FAILED,
                    notes=str(exc),
                )
            ]


class TranscriptConnectorStub:
    async def retrieve(self, **_: Any) -> list[RetrievedSource]:
        return [
            RetrievedSource(
                source_type=SourceType.TRANSCRIPT,
                url="stub://transcripts",
                title="Earnings call transcripts",
                retrieval_status=RetrievalStatus.NOT_CONFIGURED,
                notes="Transcript connector stubbed in v1",
            )
        ]
