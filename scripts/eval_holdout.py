"""Held-out check: products the gold dataset never saw, from EDGAR sourcing alone.

The gold benchmark (``eval_pipeline.py``) hands the pipeline the documents
gold cites. This script hands it nothing but an issuer's CIK: the sourcing
module enumerates the filings, the pipeline fetches them itself, and the
readers and the series stage do the rest. The result is compared with an
independently built reference in ``seed/holdout/quarterly_revenue.jsonl``
(same row shape as the gold rows), or simply printed when no reference
exists yet.

Usage (from ``backend/``):

    uv run python ../scripts/eval_holdout.py [--drug NAME]... [--since YYYY-MM-DD]
        [--dump-series] [--show-failures] [--json PATH] [--fingerprinter grammar|llm] [--refetch]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from app.benchmark.corpus import CorpusDocument, CorpusFileStore, _filing_type_for, _source_type_for  # noqa: E402
from app.benchmark.schema import Comparison, compare, from_gold, from_series  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.domain.models import RetrievalStatus, RetrievedSource  # noqa: E402
from app.extraction.series import assemble_series  # noqa: E402
from app.fingerprint.llm import LLMFingerprinter  # noqa: E402
from app.sourcing.edgar import EdgarIndex, SourceCandidate  # noqa: E402
from eval_pipeline import Runner  # noqa: E402

HOLDOUT = REPO_ROOT / "seed" / "holdout"
RAW_DIR = REPO_ROOT / "backend" / "storage" / "holdout" / "raw"


def load_products() -> list[dict]:
    with (HOLDOUT / "products.csv").open(newline="") as handle:
        return list(csv.DictReader(handle))


def load_reference() -> list[dict]:
    path = HOLDOUT / "quarterly_revenue.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def key_for(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def suffix_for(content_type: str, url: str) -> str:
    lowered = url.lower()
    if "pdf" in content_type or lowered.endswith(".pdf"):
        return ".pdf"
    return ".htm"


class HoldoutCorpus:
    """The documents the pipeline fetched for itself, served like the benchmark corpus."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        manifest = json.loads((directory / "manifest.json").read_text()) if (directory / "manifest.json").exists() else {"documents": []}
        self.documents = [
            CorpusDocument(
                url=entry["url"], file=directory / entry["file"], content_type=entry.get("content_type", ""),
                sha256=entry.get("sha256", ""), chars=entry.get("bytes", 0), fetched_via="pipeline_http", fetched_from=entry["url"],
            )
            for entry in manifest["documents"]
            if entry.get("status") == 200 and (directory / entry["file"]).exists()
        ]
        self.by_url = {d.url: d for d in self.documents}
        self.by_key = {d.key: d for d in self.documents}

    def read(self, document: CorpusDocument) -> bytes:
        return document.file.read_bytes()

    def file_store(self) -> CorpusFileStore:
        return CorpusFileStore(self)  # type: ignore[arg-type]

    def source_for(self, url: str) -> RetrievedSource | None:
        document = self.by_url.get(url)
        if document is None:
            return None
        return RetrievedSource(
            source_id=document.key, source_type=_source_type_for(url), url=url, title=None,
            filing_type=_filing_type_for(url), storage_key=f"holdout/{document.key}{document.file.suffix}",
            retrieval_status=RetrievalStatus.SUCCESS, metadata={"fetched_via": "pipeline_http"},
        )


async def fetch_candidates(candidates: list[SourceCandidate], *, refetch: bool) -> None:
    """Fetch every candidate the cache lacks, recording each in the manifest."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = RAW_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"documents": []}
    have = {entry["url"]: entry for entry in manifest["documents"]}
    settings = get_settings()
    headers = {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}
    wanted = [c for c in candidates if refetch or c.url not in have]
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=90.0) as client:
        for index, candidate in enumerate(wanted):
            entry = {"url": candidate.url, "key": key_for(candidate.url), "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "form": candidate.form, "kind": candidate.kind, "filing_date": candidate.filing_date.isoformat()}
            try:
                response = await client.get(candidate.url)
                entry["status"] = response.status_code
                entry["content_type"] = response.headers.get("content-type", "")
                entry["final_url"] = str(response.url)
                if response.status_code == 200:
                    name = f"{entry['key']}{suffix_for(entry['content_type'], candidate.url)}"
                    (RAW_DIR / name).write_bytes(response.content)
                    entry["file"] = name
                    entry["bytes"] = len(response.content)
                    entry["sha256"] = hashlib.sha256(response.content).hexdigest()
            except Exception as exc:  # noqa: BLE001
                entry["status"] = 0
                entry["error"] = str(exc)[:200]
            have[candidate.url] = entry
            if (index + 1) % 10 == 0:
                print(f"    fetched {index + 1}/{len(wanted)}", flush=True)
            await asyncio.sleep(0.15)  # EDGAR fair access
    manifest["documents"] = list(have.values())
    manifest_path.write_text(json.dumps(manifest, indent=1))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drug", action="append")
    parser.add_argument("--since", help="override the products' first filing date")
    parser.add_argument("--dump-series", action="store_true", help="print every series value, reference or not")
    parser.add_argument("--show-failures", action="store_true")
    parser.add_argument("--json")
    parser.add_argument("--fingerprinter", choices=("grammar", "llm"), default="grammar")
    parser.add_argument("--model")
    parser.add_argument("--refetch", action="store_true")
    args = parser.parse_args()

    products = load_products()
    if args.drug:
        products = [p for p in products if p["drug_name"] in set(args.drug)]
    reference = load_reference()
    by_issuer: dict[str, list[dict]] = defaultdict(list)
    for product in products:
        by_issuer[product["manufacturer"]].append(product)

    # 1. Sourcing and fetching, per issuer.
    index = EdgarIndex()
    urls_by_issuer: dict[str, list[str]] = {}
    for issuer, rows in by_issuer.items():
        since = date.fromisoformat(args.since or rows[0]["since"])
        candidates = await index.candidates(rows[0]["cik"], since=since)
        candidates = [c for c in candidates if c.url.lower().endswith((".htm", ".html", ".pdf"))]
        print(f"{issuer}: {len(candidates)} documents from EDGAR since {since} "
              f"({sum(c.kind == 'earnings_exhibit' for c in candidates)} earnings exhibits, "
              f"{sum(c.kind == 'primary' for c in candidates)} primary filings)")
        await fetch_candidates(candidates, refetch=args.refetch)
        urls_by_issuer[issuer] = [c.url for c in candidates]

    corpus = HoldoutCorpus(RAW_DIR)
    fetched = {u for urls in urls_by_issuer.values() for u in urls if u in corpus.by_url}
    print(f"fetched and readable: {len(fetched)} of {sum(len(u) for u in urls_by_issuer.values())}")

    # 2. Reading and assembly, per product.
    catalog = {issuer: [p["drug_name"] for p in rows] for issuer, rows in by_issuer.items()}
    generics = {p["drug_name"]: p["generic_name"] for p in products}
    fingerprinter = LLMFingerprinter(model=args.model) if args.fingerprinter == "llm" else None
    runner = Runner(corpus, fingerprinter=fingerprinter, catalog=catalog, generics=generics)  # type: ignore[arg-type]

    comparisons: dict[str, list[Comparison]] = {}
    pipeline_rows: dict[str, list] = {}
    skipped_by_product: dict[str, dict[str, list[str]]] = {}
    for product in products:
        name, issuer = product["drug_name"], product["manufacturer"]
        urls = [u for u in urls_by_issuer[issuer] if u in corpus.by_url]
        observations, skipped = await runner.observe(name, product["generic_name"], urls, issuer)
        series = assemble_series(observations, product=name)
        rows = [from_series(v) for v in series.values] + [from_series(v) for v in series.verdicts]
        pipeline_rows[name] = rows
        skipped_by_product[name] = skipped
        print(f"\n{name}: {len(observations)} observations from {len({o.source_url for o in observations})} documents, "
              f"{len(series.values)} series values, {len(series.verdicts)} verdicts")
        if args.dump_series:
            for value in sorted(series.values, key=lambda v: (v.period, v.geography or "")):
                print(f"   {value.period:8} {value.period_type:10} {value.geography or '-':14} "
                      f"{value.value_usd_millions:>10.3f}  {value.route:10} {value.detail or value.source_quote[:80]!r}")
            for verdict in series.verdicts:
                print(f"   VERDICT {verdict.period} {verdict.geography or '-'} {verdict.status} {verdict.detail[:120]}")
        gold = [from_gold(r) for r in reference if r["drug_name"] == name]
        if gold:
            comparisons[name] = [compare(g, rows) for g in gold]

    if comparisons:
        print("\npipeline delivery against the held-out reference, from EDGAR sourcing alone")
        print(f"{'product':12} {'ref':>5} {'match':>6} {'value':>6} {'geo':>5} {'review':>7} {'missing':>8} {'delivered':>10}")
        print("-" * 66)
        totals: dict[str, int] = defaultdict(int)
        failures: list[Comparison] = []
        for name, results in comparisons.items():
            counts: dict[str, int] = defaultdict(int)
            for result in results:
                counts[result.outcome] += 1
                totals[result.outcome] += 1
                if result.outcome != "match":
                    failures.append(result)
            n = len(results)
            flag = "" if counts["match"] == n else "  <--"
            print(f"{name:12} {n:>5} {counts['match']:>6} {counts['value_mismatch']:>6} {counts['geography_mismatch']:>5} "
                  f"{counts['needs_review']:>7} {counts['missing']:>8} {100 * counts['match'] / n if n else 0:>9.1f}%{flag}")
        n = sum(totals.values())
        print("-" * 66)
        print(f"{'ALL':12} {n:>5} {totals['match']:>6} {totals['value_mismatch']:>6} {totals['geography_mismatch']:>5} "
              f"{totals['needs_review']:>7} {totals['missing']:>8} {100 * totals['match'] / n if n else 0:>9.1f}%")
        if failures and args.show_failures:
            print(f"\n{len(failures)} rows not delivered:")
            for result in failures:
                g = result.gold
                print(f"  {g.product:12} {g.period:8} [{result.outcome}] ref={g.value_usd_millions:g} {g.geography}")
                print(f"      {result.detail[:200]}")
                if result.pipeline:
                    print(f"      pipeline quote: {result.pipeline.source_quote[:140]!r}")
                else:
                    same = [r for r in pipeline_rows[g.product] if r.period == g.period]
                    if same:
                        print(f"      pipeline has for this period: {[(r.geography, r.value_usd_millions, r.status) for r in same][:6]}")
                    doc_skips = skipped_by_product.get(g.product, {}).get(g.source_urls[0], [])
                    if doc_skips:
                        print(f"      reader skipped in cited document: {doc_skips[:4]}")
    if args.json:
        payload = {
            "pipeline_rows": {p: [r.as_dict() for r in rows] for p, rows in pipeline_rows.items()},
            "comparisons": {p: [{"gold": c.gold.as_dict(), "pipeline": c.pipeline.as_dict() if c.pipeline else None,
                                 "outcome": c.outcome, "detail": c.detail} for c in results] for p, results in comparisons.items()},
            "skipped": skipped_by_product,
        }
        Path(args.json).write_text(json.dumps(payload, indent=1))
    if comparisons:
        n = sum(len(r) for r in comparisons.values())
        return 0 if n and sum(1 for r in comparisons.values() for c in r if c.outcome == "match") == n else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
