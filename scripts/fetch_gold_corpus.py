"""Fetch every document the gold rows cite, through the pipeline's own HTTP path.

The result is a local cache of the raw documents (HTML, PDF, XBRL viewer
pages) under ``backend/storage/gold_corpus/raw`` with a manifest, so that
``scripts/eval_pipeline.py`` can run the production parser over the same
bytes a live retrieval returns. The cache is not committed; the committed
``seed/gold/corpus`` holds a text rendering of the same documents for
sessions without network access.

Usage:
    cd backend && uv run python ../scripts/fetch_gold_corpus.py [--force] [--only SUBSTRING]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import get_settings  # noqa: E402

GOLD = REPO_ROOT / "seed" / "gold"
RAW_DIR = REPO_ROOT / "backend" / "storage" / "gold_corpus" / "raw"

# Fair-access pacing per host: EDGAR asks for at most ten requests a second
# and a descriptive user agent; issuer sites get the same courtesy.
_MIN_INTERVAL = {"www.sec.gov": 0.6, "efts.sec.gov": 0.6}
_DEFAULT_INTERVAL = 0.4


def cited_urls() -> list[str]:
    urls: list[str] = []
    for name in ("quarterly_revenue.jsonl", "annual_revenue.jsonl"):
        for line in (GOLD / name).read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            for source in row.get("sources") or [{"source_url": row["source_url"]}]:
                if source["source_url"] not in urls:
                    urls.append(source["source_url"])
            for component in row.get("bridge_components") or []:
                if component.get("source_url") and component["source_url"] not in urls:
                    urls.append(component["source_url"])
    return urls


def key_for(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def suffix_for(content_type: str, url: str) -> str:
    kind = (content_type or "").split(";")[0].strip().lower()
    if kind == "application/pdf" or urlparse(url).path.lower().endswith(".pdf"):
        return ".pdf"
    if kind in {"text/html", "application/xhtml+xml"} or urlparse(url).path.lower().endswith((".htm", ".html")):
        return ".htm"
    if kind.startswith("text/"):
        return ".txt"
    return ".bin"


async def fetch_all(urls: list[str], *, force: bool) -> list[dict]:
    settings = get_settings()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = RAW_DIR / "manifest.json"
    existing = {}
    if manifest_path.exists():
        existing = {d["url"]: d for d in json.loads(manifest_path.read_text())["documents"]}
    headers = {
        "User-Agent": settings.sec_user_agent,
        "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    last_hit: dict[str, float] = {}
    documents: list[dict] = []
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=60.0) as client:
        for index, url in enumerate(urls, 1):
            if url in existing and not force and (RAW_DIR / existing[url]["file"]).exists():
                documents.append(existing[url])
                continue
            host = urlparse(url).netloc
            wait = _MIN_INTERVAL.get(host, _DEFAULT_INTERVAL) - (time.monotonic() - last_hit.get(host, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            last_hit[host] = time.monotonic()
            entry = {"url": url, "key": key_for(url), "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            try:
                response = await client.get(url)
                entry["status"] = response.status_code
                entry["content_type"] = response.headers.get("content-type", "")
                entry["final_url"] = str(response.url)
                if response.status_code == 200 and response.content:
                    suffix = suffix_for(entry["content_type"], str(response.url))
                    file = f"{entry['key']}{suffix}"
                    (RAW_DIR / file).write_bytes(response.content)
                    entry["file"] = file
                    entry["bytes"] = len(response.content)
                    entry["sha256"] = hashlib.sha256(response.content).hexdigest()
                else:
                    entry["error"] = f"http {response.status_code}"
            except Exception as exc:  # noqa: BLE001
                entry["error"] = repr(exc)
            documents.append(entry)
            status = entry.get("error") or f"{entry.get('bytes', 0)} bytes"
            print(f"[{index}/{len(urls)}] {status:24} {url}", flush=True)
            manifest_path.write_text(json.dumps({"documents": documents}, indent=1))
    manifest_path.write_text(json.dumps({"documents": documents}, indent=1))
    return documents


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", help="fetch only URLs containing this substring")
    args = parser.parse_args()
    urls = cited_urls()
    if args.only:
        urls = [u for u in urls if args.only in u]
    documents = asyncio.run(fetch_all(urls, force=args.force))
    failed = [d for d in documents if d.get("error")]
    print(f"\nfetched {len(documents) - len(failed)} of {len(documents)}; {len(failed)} failed")
    for d in failed:
        print(f"  {d['error']:30} {d['url']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
