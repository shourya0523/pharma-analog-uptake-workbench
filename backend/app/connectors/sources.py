from __future__ import annotations

# ruff: noqa: BLE001, RUF012, SIM113
import asyncio
import logging
import re
from datetime import date
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
    return "ex99" in squashed or "exhibit99" in squashed


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

    def __init__(self, file_store: FileStore) -> None:
        self.file_store = file_store
        self.settings = get_settings()
        self.headers = {
            "User-Agent": self.settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
        }

    async def resolve_cik(self, ticker: str | None, company_name: str | None) -> str | None:
        if not ticker and not company_name:
            return None
        await _sec_throttle()
        async with httpx.AsyncClient(headers=self.headers, timeout=30) as client:
            resp = await client.get(self.TICKER_MAP)
            resp.raise_for_status()
            data = resp.json()
        needle_t = (ticker or "").upper().strip()
        needle_n = (company_name or "").lower().strip()
        for row in data.values():
            if needle_t and str(row.get("ticker", "")).upper() == needle_t:
                return str(row["cik_str"]).zfill(10)
            if needle_n and needle_n in str(row.get("title", "")).lower():
                return str(row["cik_str"]).zfill(10)
        return None

    def _cache_key(self, accession: str, doc: str) -> str:
        safe_doc = doc.replace("/", "_")
        return f"cache/sec/{accession.replace('-', '')}/{safe_doc}"

    async def _list_filing_documents(
        self, client: httpx.AsyncClient, cik_int: str, acc_nodash: str
    ) -> list[str]:
        """Document filenames inside one filing, via the EDGAR directory listing."""
        url = f"{self.ARCHIVES}/{cik_int}/{acc_nodash}/index.json"
        try:
            await _sec_throttle()
            resp = await client.get(url)
            resp.raise_for_status()
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
            await _sec_throttle()
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.content
            await self.file_store.put(cache_key, raw, "text/html")
        else:
            raw = cached
        job_key = f"sources/{run_id}/{job_id}/{source_id}.html"
        await self.file_store.put(job_key, raw, "text/html")
        return raw, from_cache, job_key

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

        sources: list[RetrievedSource] = []
        for i, form in enumerate(forms):
            if len(sources) >= max_exhibits:
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
            for doc in exhibits[:1]:  # one exhibit 99.1 per earnings 8-K
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
        earnings_since: date | None = None,
        earnings_until: date | None = None,
        max_earnings_exhibits: int | None = None,
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

            recent = payload.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            accessions = recent.get("accessionNumber", [])
            primary = recent.get("primaryDocument", [])
            filing_dates = recent.get("filingDate", [])

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

            if include_earnings:
                sources.extend(
                    await self._retrieve_earnings_exhibits(
                        client,
                        run_id=run_id,
                        job_id=job_id,
                        cik=resolved,
                        recent=recent,
                        max_exhibits=max_earnings_exhibits
                        if max_earnings_exhibits is not None
                        else settings.sec_max_earnings_exhibits,
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
