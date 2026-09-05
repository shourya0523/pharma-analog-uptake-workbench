"""How much of what the gold rows cite would EDGAR sourcing have found?

For every issuer in the catalog, enumerate the filings the sourcing module
returns over the products' commercial span and check them against the
documents the gold rows actually cite. Recall on sec.gov citations is the
direct measure; issuer-site citations (press releases and schedules the
issuer also filed as 8-K exhibits) are reported separately, because the
same document reached through EDGAR carries a different URL.

Usage:
    cd backend && uv run python ../scripts/audit_sourcing.py [--issuer NAME]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.sourcing.edgar import EdgarIndex  # noqa: E402

GOLD = REPO_ROOT / "seed" / "gold"


def load(name: str) -> list[dict]:
    return [json.loads(l) for l in (GOLD / name).read_text().splitlines() if l.strip()]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issuer", action="append")
    args = parser.parse_args()
    quarterly = load("quarterly_revenue.jsonl")
    annual = load("annual_revenue.jsonl")
    tickers: dict[str, str] = {}
    with (REPO_ROOT / "seed" / "example_drugs.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("ticker"):
                tickers[row["manufacturer"]] = row["ticker"]
    # Comparator issuers share tickers with catalog issuers where possible.
    tickers.setdefault("Johnson & Johnson", "JNJ")
    tickers.setdefault("Gilead", "GILD")
    tickers.setdefault("Merck", "MRK")
    tickers.setdefault("United Therapeutics", "UTHR")
    tickers.setdefault("Liquidia", "LQDA")

    cited: dict[str, set[str]] = defaultdict(set)
    spans: dict[str, list[int]] = defaultdict(lambda: [9999, 0])
    for row in quarterly + annual:
        issuer = row["manufacturer"]
        for source in row.get("sources") or [{"source_url": row["source_url"]}]:
            cited[issuer].add(source["source_url"])
        year = int(str(row["period"])[:4])
        spans[issuer][0] = min(spans[issuer][0], year)
        spans[issuer][1] = max(spans[issuer][1], year)

    index = EdgarIndex()
    issuers = args.issuer or sorted(cited)
    total_sec = total_found = 0
    async with httpx.AsyncClient(headers=index.headers, timeout=60, follow_redirects=True) as client:
        for issuer in issuers:
            ticker = tickers.get(issuer) or tickers.get(issuer.split("/")[-1].strip())
            if not ticker:
                print(f"{issuer:24} no ticker; skipped")
                continue
            cik = await index.resolve_cik(client, ticker=ticker)
            if not cik:
                print(f"{issuer:24} CIK not resolved for {ticker}")
                continue
            since, until = date(spans[issuer][0], 1, 1), date(spans[issuer][1] + 1, 12, 31)
            candidates = await index.candidates(cik, since=since, until=until)
            urls = {c.url for c in candidates}
            sec_cited = {u for u in cited[issuer] if "sec.gov" in u}
            found = sec_cited & urls
            total_sec += len(sec_cited)
            total_found += len(found)
            forms = defaultdict(int)
            for c in candidates:
                forms[c.form.split("/")[0]] += 1
            print(
                f"{issuer:24} cik={cik} {since.year}-{until.year} candidates={len(candidates)} "
                f"{dict(forms)} sec.gov cited={len(sec_cited)} found={len(found)} "
                f"issuer-site cited={len(cited[issuer]) - len(sec_cited)}"
            )
            for url in sorted(sec_cited - urls)[:5]:
                print(f"    not enumerated: {url}")
    print(f"\nsec.gov citation recall: {total_found}/{total_sec}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
