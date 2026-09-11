"""Finding the filings that report a product's quarterly sales.

This is the step every extraction score is conditional on, and the easiest one
to leave out of a measurement. Handing the pipeline a URL and asking whether it
can read the document tests the reader; only walking EDGAR from an issuer name
and a quarter tests the pipeline.

The walk is: resolve the issuer to a CIK, list its 8-K filings that carry item
2.02 (results of operations) in the window around the quarter, and take the
EX-99 exhibits attached to them. The primary 8-K document is a cover page and
holds no figures.

Two rules here were bought with wrong answers:

* An issuer is resolved by ticker first, then by an exact match on its
  normalised name, and an ambiguous name resolves to nothing. Matching on a
  prefix once resolved "United" to a company that was not United Therapeutics,
  and a filing from the wrong company is worse than no filing.
* Every EX-99 exhibit of an earnings 8-K is read, not the first. Johnson &
  Johnson puts its press release in EX-99.1 and its product sales schedules in
  EX-99.2, so taking one exhibit per filing took the one with no table in it -
  which read as "this issuer does not disclose product sales" for 424 rows.
"""

from __future__ import annotations

# ruff: noqa: BLE001, RUF012
import asyncio
import logging
import re
from datetime import date, timedelta
from typing import Any

import httpx

from app.config import get_settings
from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
from app.storage.filestore import FileStore

logger = logging.getLogger(__name__)


# Shared across connector instances so concurrent jobs don't stampede EDGAR
_SEC_LOCK = asyncio.Lock()
_SEC_MIN_INTERVAL_S = 0.12  # ~8 req/s max, under SEC 10/s guidance
_last_sec_request = 0.0


async def _sec_throttle() -> None:
    global _last_sec_request
    async with _SEC_LOCK:
        now = asyncio.get_event_loop().time()
        wait = _SEC_MIN_INTERVAL_S - (now - _last_sec_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_sec_request = asyncio.get_event_loop().time()


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


# Corporate suffixes carry no identity: "Gilead Sciences, Inc." and "Gilead
# Sciences Inc" are the same registrant, and the SEC title uses whichever the
# filer registered with.
_REGISTRANT_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "plc", "llc", "lp", "sa", "nv", "ag", "holdings", "group",
}


def normalize_registrant(name: str) -> str:
    """A company name reduced to what identifies it, for exact comparison."""
    cleaned = re.sub(r"[^a-z0-9&\s]", " ", (name or "").lower())
    words = [
        word
        for word in cleaned.split()
        if word not in _REGISTRANT_SUFFIXES and word != "&"
    ]
    return " ".join(words)


def is_earnings_exhibit(filename: str) -> bool:
    """True for exhibit 99.x documents, which carry the product revenue tables.

    Issuers name these inconsistently (``uthrq12024-ex991.htm``,
    ``exhibit991uthr12312024.htm``, ``tm2620809d1_ex99-1.htm``), so match on the
    alphanumeric-only form of the name rather than a fixed pattern.
    """
    name = (filename or "").rsplit("/", 1)[-1].lower()
    if not name.endswith((".htm", ".html", ".txt")):
        return False
    squashed = re.sub(r"[^a-z0-9]", "", name)
    # Written "ex991", "exx991" (a doubled x survives in UTHR's names),
    # "exh991" as Gilead abbreviates it, or "exhibit991" in full.
    return bool(re.search(r"ex+(?:h(?:ibit)?)?9{2}", squashed))


def _instance_document(documents: list[str]) -> str | None:
    """The XBRL instance in one filing's directory, in either era's spelling.

    A filing's facts live in its instance document, and how that document is
    named changed with inline XBRL. Before it, the instance was a plain
    ``uthr-20160930.xml`` beside the filing's HTML; after it, the HTML *is* the
    instance and the filer ships an extracted copy as ``uthr-20250930_htm.xml``.

    This selected on the ``_htm.xml`` spelling alone, so it saw the second and
    silently skipped the first - every filer's pre-2019 filings, discarded one
    at a time as "no instance". The comment here explained the resulting gap as
    the SEC's, saying a filing from before 2019 "yields an instance with no
    product facts in it", and that is not true: Gilead's 2013 Q3 instance tags
    twelve products on the ProductOrService axis and United Therapeutics' 2016
    Q3 instance tags five. Neither was ever fetched.

    The anchor that works in both eras is the filing's own extension schema:
    the instance shares the ``.xsd``'s stem and the linkbases beside it
    (``_cal``, ``_def``, ``_lab``, ``_pre``) do not, so matching on the stem
    picks the instance without knowing the filer's ticker, the period, or which
    era the filing belongs to. Every filing checked here carries one ``.xsd``;
    the loop below does not rely on that, and takes the first stem that has an
    instance beside it.
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

    # Revenue MD&A density: skip 8-K by default (noise + volume)
    PRIMARY = {"10-K", "10-Q", "20-F", "40-F"}
    SECONDARY = {"6-K", "8-K"}
    # "Results of Operations and Financial Condition" — the earnings-release 8-K item
    EARNINGS_ITEM = "2.02"
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

    async def resolve_cik(self, ticker: str | None, company_name: str | None) -> str | None:
        """The registrant's CIK, or None rather than a guess.

        A ticker is exact and is tried first. A company name is not: the SEC
        title carries punctuation and a corporate suffix that a caller rarely
        reproduces, so both sides are normalized before comparing. What this
        must never do is return the nearest match - an unanchored substring
        search made "United" resolve to an unrelated registrant, and every
        figure taken from that company's filings would then have been attributed
        to United Therapeutics with nothing downstream able to notice. Several
        matches means the question was ambiguous, and the honest answer to an
        ambiguous question is no answer.
        """
        if not ticker and not company_name:
            return None
        await _sec_throttle()
        async with httpx.AsyncClient(headers=self.headers, timeout=30) as client:
            resp = await client.get(self.TICKER_MAP)
            resp.raise_for_status()
            data = resp.json()

        needle_t = (ticker or "").upper().strip()
        if needle_t:
            for row in data.values():
                if str(row.get("ticker", "")).upper() == needle_t:
                    return str(row["cik_str"]).zfill(10)
            return None

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
        self, client: httpx.AsyncClient, url: str, *, attempts: int = 4
    ) -> httpx.Response:
        """Fetch, retrying the refusals EDGAR gives when asked too quickly.

        SEC returns 503 or 429 under load rather than a permanent error, and a
        single one costs a whole filing. It is worth distinguishing from a real
        failure: a document silently missing because of a rate limit reads
        downstream as an issuer that discloses nothing, and moved three rows
        between two runs of the same code while this had no retry at all.
        """
        delay = 1.0
        for attempt in range(attempts):
            await _sec_throttle()
            response = await client.get(url)
            if response.status_code not in (429, 503) or attempt == attempts - 1:
                response.raise_for_status()
                return response
            logger.info(
                "sec_backoff status=%s attempt=%s url=%s",
                response.status_code, attempt + 1, url,
            )
            await asyncio.sleep(delay)
            delay *= 2
        raise RuntimeError("unreachable")

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
        """Return (bytes, from_cache, per-job storage key) for one filing document."""
        cache_key = self._cache_key(accession, doc)
        cached = await self._read_cache(cache_key)
        from_cache = cached is not None
        if cached is None:
            resp = await self._get_with_retry(client, url)
            raw = resp.content
            await self.file_store.put(cache_key, raw, "text/html")
        else:
            raw = cached
        job_key = f"sources/{run_id}/{job_id}/{source_id}.html"
        await self.file_store.put(job_key, raw, "text/html")
        return raw, from_cache, job_key

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
                await _sec_throttle()
                extra = await client.get(f"https://data.sec.gov/submissions/{name}")
                extra.raise_for_status()
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
        # Counting exhibits truncated mid-filing: Johnson & Johnson files two
        # EX-99s per 8-K, so six exhibits bought three quarters, and the sixth
        # took a press release while leaving behind the product-sales schedule
        # it belongs to. Measured over Uptravi, Stelara and Xarelto in 2018 and
        # 2019, that lost 9 of 24 quarters - every Q2, and the one Q3 whose
        # schedule fell the wrong side of the cut.
        sources: list[RetrievedSource] = []
        filings_read = 0
        for i, form in enumerate(forms):
            if filings_read >= max_exhibits:
                break
            if form != "8-K":
                continue
            filing_items = items[i] if i < len(items) else ""
            if self.EARNINGS_ITEM not in (filing_items or ""):
                continue
            accession = accessions[i]
            fdate = filing_dates[i] if i < len(filing_dates) else None
            filed_on = parse_filing_date(fdate)
            if (since and (filed_on is None or filed_on < since)) or (
                until and (filed_on is None or filed_on > until)
            ):
                continue
            acc_nodash = accession.replace("-", "")
            documents = await self._list_filing_documents(client, cik_int, acc_nodash)
            exhibits = [name for name in documents if is_earnings_exhibit(name)]
            if not exhibits:
                logger.info("sec_no_earnings_exhibit accession=%s date=%s", accession, fdate)
                continue
            # Every exhibit, not the first one. An issuer that separates its
            # press release from its schedules puts the prose in EX-99.1 and the
            # product-level sales in EX-99.2, and taking one exhibit per filing
            # takes the wrong one: Johnson & Johnson's EX-99.1 carries no table
            # at all while its EX-99.2 carries twenty-two. Nothing in the
            # numbering says which is which, so the way to not choose wrongly is
            # not to choose - reading an exhibit that holds no product table
            # costs a parse, and skipping the one that does costs the quarter.
            filings_read += 1
            for doc in exhibits:
                sid = new_id()
                url = f"{self.ARCHIVES}/{cik_int}/{acc_nodash}/{doc}"
                try:
                    _raw, from_cache, job_key = await self._fetch_document(
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
                            title=f"8-K EX-99 earnings release {fdate or ''}".strip(),
                            source_date=date.fromisoformat(fdate) if fdate else None,
                            filing_type="8-K",
                            accession_number=accession,
                            storage_key=job_key,
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
                            title=f"8-K EX-99 earnings release {fdate or ''}".strip(),
                            filing_type="8-K",
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
        """The tagged instance from each 10-Q or 10-K covering this window.

        A quarterly report states its product revenue in XBRL - the period, the
        unit and the product as declared facts rather than as a table to read.
        The 8-K exhibits fetched beside these carry no tagging at all, so this
        is the only route to a figure the filer has stated rather than printed.

        It reaches back only as far as the filer's own tagging does, and that is
        a per-filer fact rather than a date: Gilead tags twelve products on the
        ProductOrService axis in its 2013 Q3 instance and United Therapeutics
        five in its 2016 Q3 one, while United Therapeutics' 2010 Q3 instance
        carries no product axis at all. Nothing here needs to know when each
        filer started - the reader simply finds nothing, which is the correct
        answer for a filing that has nothing.

        What did need fixing was reaching the instance in the first place; see
        `_instance_document`.
        """
        cik_int = str(int(cik))
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        sources: list[RetrievedSource] = []
        for index, form in enumerate(forms):
            if len(sources) >= max_filings:
                break
            if form not in {"10-Q", "10-K"}:
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
                _raw, from_cache, job_key = await self._fetch_document(
                    client, url=url, accession=accession, doc=instance,
                    run_id=run_id, job_id=job_id, source_id=sid,
                )
            except Exception as exc:
                logger.info("sec_xbrl_fetch_failed accession=%s error=%s", accession, exc)
                continue
            sources.append(
                RetrievedSource(
                    source_id=sid,
                    source_type=(SourceType.ANNUAL_REPORT if form == "10-K"
                                 else SourceType.QUARTERLY_REPORT),
                    url=url,
                    title=f"{form} XBRL instance {filing_dates[index] if index < len(filing_dates) else ''}".strip(),
                    source_date=filed_on,
                    filing_type=form,
                    accession_number=accession,
                    storage_key=job_key,
                    retrieval_status=RetrievalStatus.SUCCESS,
                    metadata={"cik": cik, "from_cache": from_cache, "xbrl_instance": True},
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
                await _sec_throttle()
                sub = await client.get(sub_url)
                sub.raise_for_status()
                payload = sub.json()
            except Exception as exc:
                sources.append(
                    RetrievedSource(
                        source_type=SourceType.SEC_FILING,
                        url=sub_url,
                        title="SEC submissions",
                        retrieval_status=RetrievalStatus.FAILED,
                        notes=str(exc),
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

            # A primary filing reports the period it covers, so it is useful for
            # a window that ends a little after the window closes: a 10-K filed
            # in February reports the year before it.
            since_bound = earnings_since - timedelta(days=400) if earnings_since else None
            until_bound = earnings_until + timedelta(days=120) if earnings_until else None

            indexed: list[tuple[int, int, str]] = []
            for i, form in enumerate(forms):
                if form not in allowed:
                    continue
                pri = {"10-K": 0, "20-F": 0, "40-F": 0, "10-Q": 1, "6-K": 2, "8-K": 3}.get(form, 5)
                indexed.append((pri, i, form))
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
                    _raw, from_cache, job_key = await self._fetch_document(
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
                            storage_key=job_key,
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
                        since=earnings_since,
                        until=earnings_until,
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


class ManualURLConnector:
    def __init__(self, file_store: FileStore) -> None:
        self.file_store = file_store
        self.settings = get_settings()

    async def retrieve(self, *, run_id: str, job_id: str, url: str) -> list[RetrievedSource]:
        sid = new_id()
        if not url:
            return []
        headers: dict[str, str] = {"User-Agent": self.settings.sec_user_agent}
        if "sec.gov" in url.lower():
            headers["Accept-Encoding"] = "gzip, deflate"
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers=headers) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "text/html")
                ext = "pdf" if "pdf" in content_type or url.lower().endswith(".pdf") else "html"
                key = f"sources/{run_id}/{job_id}/{sid}.{ext}"
                await self.file_store.put(key, resp.content, content_type)
                text = resp.text if ext == "html" else None
                return [
                    RetrievedSource(
                        source_id=sid,
                        source_type=SourceType.USER_URL,
                        url=url,
                        title=url,
                        raw_text=None if key else (text[:500_000] if text else None),
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
