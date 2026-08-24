from __future__ import annotations

import re
from datetime import date
from typing import Any

import httpx

from app.config import get_settings
from app.domain.models import RetrievedSource, RetrievalStatus, SourceType, new_id
from app.storage.filestore import FileStore


class SECConnector:
    """SEC EDGAR submissions + filing retrieval. Always stores audit row status."""

    SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
    TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
    ARCHIVES = "https://www.sec.gov/Archives/edgar/data"

    RELEVANT = {"10-K", "10-Q", "8-K", "20-F", "40-F", "6-K"}

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

    async def retrieve(
        self,
        *,
        run_id: str,
        job_id: str,
        cik: str | None,
        ticker: str | None,
        company_name: str | None,
        max_filings: int = 12,
    ) -> list[RetrievedSource]:
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

            picked = 0
            for i, form in enumerate(forms):
                if form not in self.RELEVANT:
                    continue
                if picked >= max_filings:
                    break
                accession = accessions[i]
                doc = primary[i]
                fdate = filing_dates[i] if i < len(filing_dates) else None
                acc_nodash = accession.replace("-", "")
                cik_int = str(int(resolved))
                url = f"{self.ARCHIVES}/{cik_int}/{acc_nodash}/{doc}"
                sid = new_id()
                try:
                    doc_resp = await client.get(url)
                    doc_resp.raise_for_status()
                    text = doc_resp.text
                    key = f"sources/{run_id}/{job_id}/{sid}.html"
                    await self.file_store.put(key, text.encode("utf-8", errors="ignore"), "text/html")
                    sources.append(
                        RetrievedSource(
                            source_id=sid,
                            source_type=SourceType.SEC_FILING,
                            url=url,
                            title=f"{form} {fdate or ''}".strip(),
                            source_date=date.fromisoformat(fdate) if fdate else None,
                            filing_type=form,
                            accession_number=accession,
                            raw_text=text[:500_000],
                            storage_key=key,
                            retrieval_status=RetrievalStatus.SUCCESS,
                            metadata={"cik": resolved},
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
                        raw_text=text[:500_000] if text else None,
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
