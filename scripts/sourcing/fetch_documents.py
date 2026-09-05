"""Cache every document the gold dataset cites, so the extraction eval can be
run against the filings themselves rather than against gold's own quotes.

A gold row's ``source_quote`` exists to let a human confirm the figure without
reopening the filing. It is evidence, not a test fixture: scoring the pipeline
on it measures how the quote was written as much as how the pipeline reads, so
the eval reads the document instead and the quote goes back to being a receipt.

    SEC_CONTACT='pharma-analog-uptake-workbench you@example.com'
    DOCUMENT_CACHE=/tmp/gold-documents
    python scripts/sourcing/fetch_documents.py
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = REPO / "seed" / "gold"
CACHE = pathlib.Path(os.environ.get("DOCUMENT_CACHE", "/tmp/gold-documents"))
CONTACT = os.environ.get("SEC_CONTACT", "")


def cache_name(url: str) -> str:
    """A stable filename per URL, keeping the suffix so PDFs stay readable."""
    digest = hashlib.sha256(url.encode()).hexdigest()[:20]
    suffix = ".pdf" if url.lower().split("?")[0].endswith(".pdf") else ".html"
    return digest + suffix


def cited_urls() -> list[str]:
    urls: set[str] = set()
    for name in ("quarterly_revenue.jsonl", "annual_revenue.jsonl"):
        for line in (GOLD / name).read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            urls.add(row["source_url"])
            for extra in row.get("sources") or []:
                urls.add(extra["source_url"])
    return sorted(urls)


def fetch(url: str) -> bytes | None:
    # sec.gov requires a contact address; other hosts want a plain browser UA.
    agent = CONTACT if "sec.gov" in url and CONTACT else "Mozilla/5.0 (research)"
    request = urllib.request.Request(url, headers={"User-Agent": agent})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code in {404, 403, 410}:
                return None
            time.sleep(2**attempt)
        except Exception:
            time.sleep(2**attempt)
    return None


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    urls = cited_urls()
    have, got, missing = 0, 0, []
    for url in urls:
        path = CACHE / cache_name(url)
        if path.exists() and path.stat().st_size > 512:
            have += 1
            continue
        body = fetch(url)
        if not body:
            missing.append(url)
            continue
        path.write_bytes(body)
        got += 1
        time.sleep(0.15)
    index = {url: cache_name(url) for url in urls if (CACHE / cache_name(url)).exists()}
    (CACHE / "index.json").write_text(json.dumps(index, indent=1))
    print(f"{len(urls)} cited documents: {have} cached already, {got} fetched, "
          f"{len(missing)} unreachable")
    for url in missing[:20]:
        print("   unreachable:", url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
