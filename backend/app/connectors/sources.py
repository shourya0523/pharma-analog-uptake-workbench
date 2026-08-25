from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import httpx

from app.config import get_settings
from app.domain.models import RetrievedSource, RetrievalStatus, SourceType, new_id
from app.storage.filestore import FileStore


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


class SECConnector:
    """SEC EDGAR submissions + filing retrieval. Always stores audit row status."""

    SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
    TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
    ARCHIVES = "https://www.sec.gov/Archives/edgar/data"

    # Revenue MD&A density: skip 8-K by default (noise + volume)
    PRIMARY = {"10-K", "10-Q", "20-F", "40-F"}
    SECONDARY = {"6-K", "8-K"}

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

    async def retrieve(
        self,
        *,
        run_id: str,
        job_id: str,
        cik: str | None,
        ticker: str | None,
        company_name: str | None,
        max_filings: int | None = None,
    ) -> list[RetrievedSource]:
        settings = self.settings
        max_filings = max_filings if max_filings is not None else settings.sec_max_filings
        include_8k = settings.sec_include_8k
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
            for _pri, i, form in indexed:
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
                job_key = f"sources/{run_id}/{job_id}/{sid}.html"

                try:
                    cached = await self._read_cache(cache_key)
                    from_cache = cached is not None
                    if cached is None:
                        await _sec_throttle()
                        doc_resp = await client.get(url)
                        doc_resp.raise_for_status()
                        raw = doc_resp.content
                        await self.file_store.put(cache_key, raw, "text/html")
                    else:
                        raw = cached

                    # Per-job copy for audit trail (cheap local copy; S3 would be multipart later)
                    await self.file_store.put(job_key, raw, "text/html")
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

    async def retrieve(self, *, run_id: str, job_id: str, url: str) -> list[RetrievedSource]:
        sid = new_id()
        if not url:
            return []
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
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
