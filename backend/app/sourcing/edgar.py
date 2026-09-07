"""Sourcing: every filing an issuer made that can state product revenue.

Product-level revenue lives in three kinds of EDGAR filing: the earnings
release an issuer attaches to its 8-K (item 2.02, exhibit 99.x - the press
release, and often a supplemental sales schedule as a second exhibit), the
quarterly report (10-Q) and the annual report (10-K). This module enumerates
all of them for an issuer over a date range from EDGAR's submissions index,
including the older pages the index splits off, and resolves the exhibit
documents inside each 8-K from the filing's own directory listing.

It is deliberately dumb about content: it returns candidate documents, and
the fingerprinter decides which of them state revenue for which product.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import httpx

from app.config import get_settings
from app.domain.models import RetrievalStatus, RetrievedSource, SourceType

logger = logging.getLogger(__name__)

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_PAGE = "https://data.sec.gov/submissions/{name}"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"

REVENUE_FORMS = ("8-K", "10-Q", "10-K", "10-K405", "6-K", "20-F", "40-F")
_INDEX_NOISE_RE = re.compile(
    r"\.(?:xml|xsd|jpg|jpeg|png|gif|zip|json|txt)$|-index(?:-headers)?\.html?$|^R\d+\.htm$", re.I
)

_MIN_INTERVAL = 0.12  # EDGAR fair-access: no more than ten requests per second


@dataclass(frozen=True)
class SourceCandidate:
    url: str
    form: str
    filing_date: date
    accession: str
    document: str
    kind: str          # "earnings_exhibit" | "primary"
    description: str = ""


class EdgarIndex:
    def __init__(self, *, cache_dir: Path | None = None) -> None:
        self.settings = get_settings()
        self.headers = {"User-Agent": self.settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}
        self.cache_dir = cache_dir or Path(self.settings.local_storage_root) / "cache" / "edgar_index"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def _get_json(self, client: httpx.AsyncClient, url: str, *, ttl_days: int = 1) -> dict | list | None:
        key = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("//", 1)[-1])
        path = self.cache_dir / f"{key}.json"
        if path.exists() and (time.time() - path.stat().st_mtime) < ttl_days * 86400:
            return json.loads(path.read_text())
        async with self._lock:
            wait = _MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
        try:
            response = await client.get(url)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("edgar_index_failed url=%s error=%s", url, exc)
            return None
        data = response.json()
        path.write_text(json.dumps(data))
        return data

    async def resolve_cik(self, client: httpx.AsyncClient, *, ticker: str | None = None, name: str | None = None) -> str | None:
        data = await self._get_json(client, TICKER_MAP, ttl_days=7)
        if not data:
            return None
        wanted_ticker = (ticker or "").upper().strip()
        wanted_name = (name or "").lower().strip()
        for row in data.values():
            if wanted_ticker and str(row.get("ticker", "")).upper() == wanted_ticker:
                return str(row["cik_str"]).zfill(10)
        for row in data.values():
            if wanted_name and wanted_name in str(row.get("title", "")).lower():
                return str(row["cik_str"]).zfill(10)
        return None

    async def filings(self, client: httpx.AsyncClient, cik: str) -> list[dict]:
        """Every filing in the submissions index, newest first, across all pages."""
        cik = cik.zfill(10)
        root = await self._get_json(client, SUBMISSIONS.format(cik=cik))
        if not root:
            return []
        pages = [root.get("filings", {}).get("recent", {})]
        for extra in root.get("filings", {}).get("files", []) or []:
            page = await self._get_json(client, SUBMISSIONS_PAGE.format(name=extra["name"]), ttl_days=30)
            if page:
                pages.append(page)
        rows: list[dict] = []
        for page in pages:
            columns = {k: v for k, v in page.items() if isinstance(v, list)}
            if not columns:
                continue
            length = min(len(v) for v in columns.values())
            for index in range(length):
                rows.append({k: v[index] for k, v in columns.items()})
        return rows

    async def documents_in(self, client: httpx.AsyncClient, cik: str, accession: str) -> list[str]:
        cik_int = str(int(cik))
        acc = accession.replace("-", "")
        data = await self._get_json(client, f"{ARCHIVES}/{cik_int}/{acc}/index.json", ttl_days=365)
        if not data:
            return []
        return [item.get("name", "") for item in data.get("directory", {}).get("item", []) if item.get("name")]

    async def _throttled_get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        async with self._lock:
            wait = _MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
        return await client.get(url)

    async def resolve_cik_for(self, *, ticker: str | None = None, name: str | None = None) -> str | None:
        async with httpx.AsyncClient(headers=self.headers, timeout=60, follow_redirects=True) as client:
            return await self.resolve_cik(client, ticker=ticker, name=name)

    async def retrieve(
        self,
        *,
        run_id: str,
        job_id: str,
        cik: str,
        file_store: Any,
        since: date | None = None,
        until: date | None = None,
        forms: Iterable[str] = REVENUE_FORMS,
    ) -> list[RetrievedSource]:
        """Every document of every filing in the window, fetched and stored.

        Documents are cached under ``cache/sec/{accession}/{document}`` and
        copied per job under ``sources/{run}/{job}/``. Which of them state
        product revenue is nobody's decision here.
        """
        candidates = await self.candidates(cik, since=since, until=until, forms=forms)
        sources: list[RetrievedSource] = []
        async with httpx.AsyncClient(headers=self.headers, timeout=90, follow_redirects=True) as client:
            for candidate in candidates:
                acc = candidate.accession.replace("-", "")
                suffix = ".pdf" if candidate.document.lower().endswith(".pdf") else ".htm"
                cache_key = f"cache/sec/{acc}/{candidate.document.replace('/', '_')}"
                source = RetrievedSource(
                    source_type=SourceType.EARNINGS_RELEASE if candidate.form.split("/")[0] in {"8-K", "6-K"} else SourceType.SEC_FILING,
                    url=candidate.url,
                    title=f"{candidate.form} {candidate.document} {candidate.filing_date.isoformat()}",
                    source_date=candidate.filing_date,
                    filing_type=candidate.form,
                    accession_number=candidate.accession,
                    retrieval_status=RetrievalStatus.SUCCESS,
                    metadata={"cik": cik, "kind": candidate.kind, "document": candidate.document,
                              "description": candidate.description},
                )
                try:
                    from_cache = await file_store.exists(cache_key)
                    if from_cache:
                        raw = await file_store.get(cache_key)
                    else:
                        response = await self._throttled_get(client, candidate.url)
                        response.raise_for_status()
                        raw = response.content
                        await file_store.put(cache_key, raw, "application/pdf" if suffix == ".pdf" else "text/html")
                    job_key = f"sources/{run_id}/{job_id}/{source.source_id}{suffix}"
                    await file_store.put(job_key, raw, "application/pdf" if suffix == ".pdf" else "text/html")
                    source.storage_key = job_key
                    source.metadata["from_cache"] = from_cache
                    source.notes = "sec_cache_hit" if from_cache else None
                except Exception as exc:  # noqa: BLE001
                    source.retrieval_status = RetrievalStatus.FAILED
                    source.notes = f"fetch failed: {exc}"[:300]
                    logger.warning("edgar_fetch_failed url=%s error=%s", candidate.url, exc)
                sources.append(source)
        return sources

    async def candidates(
        self,
        cik: str,
        *,
        since: date | None = None,
        until: date | None = None,
        forms: Iterable[str] = REVENUE_FORMS,
        earnings_exhibits: bool = True,  # kept for callers; every filing's documents are candidates
    ) -> list[SourceCandidate]:
        """Documents that can state product revenue, newest first."""
        wanted = set(forms)
        out: list[SourceCandidate] = []
        async with httpx.AsyncClient(headers=self.headers, timeout=60, follow_redirects=True) as client:
            rows = await self.filings(client, cik)
            cik_int = str(int(cik))
            for row in rows:
                form = str(row.get("form", ""))
                base_form = form.split("/")[0]
                if base_form not in wanted:
                    continue
                filed = date.fromisoformat(row["filingDate"])
                if since and filed < since:
                    continue
                if until and filed > until:
                    continue
                accession = str(row["accessionNumber"])
                acc = accession.replace("-", "")
                names = await self.documents_in(client, cik, accession)
                primary = str(row.get("primaryDocument", ""))
                for name in names:
                    # Every document of the filing that is a document (not
                    # an EDGAR viewer artefact) is a candidate. Which of them
                    # states product revenue is not decided here: the
                    # fingerprinter's sketch looks for the products in each
                    # and nothing is asked of a document that names none.
                    if _INDEX_NOISE_RE.search(name) or not name.lower().endswith((".htm", ".html", ".pdf")):
                        continue
                    if base_form in {"8-K", "6-K"}:
                        # An 8-K cover page carries no figures of its own; a
                        # 6-K's primary document is the report itself.
                        if name == primary and base_form == "8-K":
                            continue
                        kind = "exhibit"
                    else:
                        kind = "primary" if name == primary else "exhibit"
                    out.append(
                        SourceCandidate(
                            url=f"{ARCHIVES}/{cik_int}/{acc}/{name}",
                            form=form,
                            filing_date=filed,
                            accession=accession,
                            document=name,
                            kind=kind,
                            description=str(row.get("primaryDocDescription", "")),
                        )
                    )
                continue
        return out
