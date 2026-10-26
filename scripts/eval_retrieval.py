"""Score SEC retrieval alone: does it fetch the documents the answer key cites?

Calls the same connector the pipeline uses (`SECConnector.retrieve` with
the same include_* flags the orchestrator sets). Speaks no HTTP API and runs
no extraction — only whether the expected filing/exhibit landed in the set
retrieval returned.

    cd backend && uv run python ../scripts/eval_retrieval.py
    cd backend && uv run python ../scripts/eval_retrieval.py --out /tmp/retrieval_eval.json

Answer keys are derived from seed/, not named: every case file under
seed/cases/ that carries source_url expectations, gold_all joined through
gold_id to quarterly_revenue.jsonl, and seed/holdout{,2}/quarterly_revenue
joined to their products.csv. Keys with no source_url (members, labels,
shapes, unseen, foreign XBRL) are reported as unscorable for this stage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse, unquote

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

from app.connectors.sources import SECConnector, parse_filing_date  # noqa: E402
from app.domain.models import RetrievalStatus  # noqa: E402
from app.storage.filestore import LocalFileStore  # noqa: E402

SEED = REPO / "seed"


def _cases(path: Path) -> list[dict]:
    text = path.read_text()
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        payload = json.loads(text)
        rows = payload.get("cases") if isinstance(payload, dict) else payload
    return [row for row in (rows or ()) if isinstance(row, dict)]


def _doc_key(url: str) -> tuple[str, str] | None:
    """Accession + document basename from an EDGAR archives URL.

    Path shape: /Archives/edgar/data/{cik}/{18-digit accession}/{filename}.
    Matching on the path segments, not a free digit search — a free search
    eats the CIK and invents a false accession.
    """
    if not url:
        return None
    parts = [p for p in unquote(urlparse(url).path).split("/") if p]
    try:
        i = parts.index("data")
        acc18 = parts[i + 2]
        name = parts[i + 3].lower()
    except (ValueError, IndexError):
        return None
    if not re.fullmatch(r"\d{18}", acc18) or not name:
        return None
    accession = f"{acc18[:10]}-{acc18[10:12]}-{acc18[12:]}"
    return accession, name


def _is_sec(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host.endswith("sec.gov")


def _gold_index() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in (SEED / "gold" / "quarterly_revenue.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out[row["gold_id"]] = row
    return out


def _expected_urls_from_expect(expect: list[dict], gold_by_id: dict[str, dict]) -> list[str]:
    urls: list[str] = []
    for figure in expect or ():
        if figure.get("source_url"):
            urls.append(figure["source_url"])
        for source in figure.get("sources") or ():
            if source.get("source_url"):
                urls.append(source["source_url"])
        gid = figure.get("gold_id")
        if gid and gid in gold_by_id:
            row = gold_by_id[gid]
            if row.get("source_url"):
                urls.append(row["source_url"])
            for source in row.get("sources") or ():
                if source.get("source_url"):
                    urls.append(source["source_url"])
    # Preserve order, drop empties/duplicates.
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _load_jobs() -> tuple[list[dict], list[dict]]:
    """Every scored retrieval job, and every answer key that has nothing to
    score at this stage."""
    gold_by_id = _gold_index()
    jobs: list[dict] = []
    unscorable: list[dict] = []

    # gold_all is the product×window view of gold; sample is a subset of it.
    gold_all = SEED / "cases" / "gold_all.json"
    for case in _cases(gold_all):
        urls = _expected_urls_from_expect(case.get("expect") or [], gold_by_id)
        if not urls:
            # Excluded products and empty expects: nothing to fetch.
            continue
        options = case.get("options") or {}
        jobs.append(
            {
                "set": "gold",
                "drug_name": case["drug_name"],
                "ticker": case.get("ticker"),
                "manufacturer": case.get("manufacturer"),
                "cik": case.get("cik"),
                "earnings_since": options.get("earnings_since"),
                "earnings_until": options.get("earnings_until"),
                "expected_urls": urls,
            }
        )

    # Case files under seed/cases that carry their own source_url expectations.
    # gold_all / gold_sample are handled above; skip them here.
    skip = {"gold_all.json", "gold_sample.json"}
    for path in sorted((SEED / "cases").glob("*.json")):
        if path.name in skip:
            continue
        cases = _cases(path)
        scored = 0
        for case in cases:
            urls = _expected_urls_from_expect(case.get("expect") or [], {})
            if not urls:
                continue
            scored += 1
            options = case.get("options") or {}
            jobs.append(
                {
                    "set": path.stem,
                    "drug_name": case["drug_name"],
                    "ticker": case.get("ticker"),
                    "manufacturer": case.get("manufacturer"),
                    "cik": case.get("cik"),
                    "earnings_since": options.get("earnings_since"),
                    "earnings_until": options.get("earnings_until"),
                    "expected_urls": urls,
                }
            )
        if scored == 0 and cases:
            unscorable.append(
                {
                    "set": path.stem,
                    "path": str(path.relative_to(REPO)),
                    "cases": len(cases),
                    "reason": "no source_url expectations",
                }
            )

    # seed/holdout and seed/holdout2: products.csv + quarterly_revenue.jsonl
    for name in ("holdout", "holdout2"):
        root = SEED / name
        products_path = root / "products.csv"
        revenue_path = root / "quarterly_revenue.jsonl"
        if not products_path.exists() or not revenue_path.exists():
            continue
        import csv

        products = {row["drug_name"]: row for row in csv.DictReader(products_path.open())}
        by_drug: dict[str, list[str]] = defaultdict(list)
        for line in revenue_path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            url = row.get("source_url")
            if url:
                by_drug[row["drug_name"]].append(url)
        for drug, urls in by_drug.items():
            product = products.get(drug)
            if not product:
                continue
            # Window: products.csv states since; until is today, which is what
            # a caller who only typed a start date gets after filing_window.
            jobs.append(
                {
                    "set": name,
                    "drug_name": drug,
                    "ticker": product.get("ticker"),
                    "manufacturer": product.get("manufacturer"),
                    "cik": product.get("cik"),
                    "earnings_since": product.get("since"),
                    "earnings_until": date.today().isoformat(),
                    "expected_urls": list(dict.fromkeys(urls)),
                }
            )

    # Member / label / foreign keys live outside cases/ and name no filing URL.
    for path in (
        SEED / "holdout_members" / "combined_name_members.json",
        SEED / "holdout_members_jev" / "members.json",
        SEED / "holdout_labels" / "product_labels.json",
        SEED / "holdout_foreign_xbrl.json",
    ):
        if path.exists():
            unscorable.append(
                {
                    "set": path.stem,
                    "path": str(path.relative_to(REPO)),
                    "cases": len(_cases(path)),
                    "reason": "not a document-retrieval key",
                }
            )

    return jobs, unscorable


def _retrieve_key(job: dict) -> tuple:
    return (
        (job.get("cik") or "").zfill(10) if job.get("cik") else "",
        (job.get("ticker") or "").upper(),
        job.get("earnings_since") or "",
        job.get("earnings_until") or "",
    )


async def _retrieve_once(
    sec: SECConnector, job: dict, cache: dict
) -> list:
    key = _retrieve_key(job)
    if key in cache:
        return cache[key]
    sources = await sec.retrieve(
        run_id="retrieval-eval",
        job_id=f"{job['set']}:{job['drug_name']}",
        cik=job.get("cik"),
        ticker=job.get("ticker"),
        company_name=job.get("manufacturer"),
        include_primary=True,
        include_earnings=True,
        include_xbrl=True,
        earnings_since=parse_filing_date(job.get("earnings_since")),
        earnings_until=parse_filing_date(job.get("earnings_until")),
    )
    cache[key] = sources
    return sources


def _score_job(job: dict, sources: list) -> dict:
    fetched_keys: set[tuple[str, str]] = set()
    success_keys: set[tuple[str, str]] = set()
    fetched_urls: list[str] = []
    for source in sources:
        key = _doc_key(source.url)
        if key is None:
            continue
        fetched_keys.add(key)
        fetched_urls.append(source.url)
        if source.retrieval_status == RetrievalStatus.SUCCESS:
            success_keys.add(key)

    expected = job["expected_urls"]
    sec_expected = [u for u in expected if _is_sec(u)]
    non_sec = [u for u in expected if not _is_sec(u)]

    hits = []
    misses = []
    for url in sec_expected:
        key = _doc_key(url)
        if key is None:
            misses.append({"url": url, "reason": "unparseable_expected_url"})
            continue
        if key in success_keys:
            hits.append(url)
        elif key in fetched_keys:
            hits.append(url)  # selected; fetch may have failed — still "found"
            # Distinguish below via success count.
        else:
            misses.append({"url": url, "reason": "not_retrieved", "key": list(key)})

    success_hits = []
    for url in sec_expected:
        key = _doc_key(url)
        if key and key in success_keys:
            success_hits.append(url)

    return {
        "set": job["set"],
        "drug_name": job["drug_name"],
        "ticker": job.get("ticker"),
        "window": [job.get("earnings_since"), job.get("earnings_until")],
        "expected_total": len(expected),
        "expected_sec": len(sec_expected),
        "expected_non_sec": len(non_sec),
        "retrieved_count": len(sources),
        "sec_hits": len(hits),
        "sec_success_hits": len(success_hits),
        "sec_misses": len(misses),
        "sec_hit_rate": (len(hits) / len(sec_expected)) if sec_expected else None,
        "sec_success_hit_rate": (
            len(success_hits) / len(sec_expected) if sec_expected else None
        ),
        # None when the answer key cites no SEC doc for this job — IR-only
        # rows are outside this connector and must not count as incomplete.
        "complete": (len(misses) == 0) if sec_expected else None,
        "misses": misses[:20],
        "non_sec_urls": non_sec[:5],
    }


async def run(out: Path | None, *, concurrency: int = 1) -> dict:
    jobs, unscorable = _load_jobs()
    store = LocalFileStore(str(BACKEND / "storage" / "retrieval_eval"))
    sec = SECConnector(store)
    cache: dict = {}
    concurrency = max(1, concurrency)
    gate = asyncio.Semaphore(concurrency)

    # Unique retrieve keys first so progress is about EDGAR calls, not drugs.
    unique: dict[tuple, dict] = {}
    for job in jobs:
        unique.setdefault(_retrieve_key(job), job)
    print(
        f"jobs={len(jobs)} unique_retrieve_calls={len(unique)} "
        f"concurrency={concurrency} unscorable_sets={len(unscorable)}",
        flush=True,
    )

    done = 0
    total = len(unique)
    lock = asyncio.Lock()

    async def _fill(key: tuple, sample: dict) -> None:
        nonlocal done
        async with gate:
            try:
                await _retrieve_once(sec, sample, cache)
            except Exception as exc:  # noqa: BLE001 — scored per job below
                cache[key] = exc
            async with lock:
                done += 1
                n = done
            print(
                f"[{n}/{total}] retrieve {sample['set']} "
                f"{sample.get('ticker') or sample['drug_name']} "
                f"{sample.get('earnings_since')}..{sample.get('earnings_until')}",
                flush=True,
            )

    await asyncio.gather(*(_fill(key, sample) for key, sample in unique.items()))

    results = []
    for i, job in enumerate(jobs, 1):
        print(
            f"score [{i}/{len(jobs)}] {job['set']} {job['drug_name']}",
            flush=True,
        )
        cached = cache.get(_retrieve_key(job))
        if isinstance(cached, Exception):
            results.append(
                {
                    "set": job["set"],
                    "drug_name": job["drug_name"],
                    "ticker": job.get("ticker"),
                    "window": [job.get("earnings_since"), job.get("earnings_until")],
                    "error": f"{type(cached).__name__}: {cached}",
                    "expected_sec": len([u for u in job["expected_urls"] if _is_sec(u)]),
                    "sec_hits": 0,
                    "sec_misses": len([u for u in job["expected_urls"] if _is_sec(u)]),
                    "complete": False,
                    "sec_hit_rate": 0.0,
                }
            )
            continue
        try:
            sources = await _retrieve_once(sec, job, cache)
            results.append(_score_job(job, sources))
        except Exception as exc:  # noqa: BLE001 — surface per-job, keep going
            results.append(
                {
                    "set": job["set"],
                    "drug_name": job["drug_name"],
                    "ticker": job.get("ticker"),
                    "window": [job.get("earnings_since"), job.get("earnings_until")],
                    "error": f"{type(exc).__name__}: {exc}",
                    "expected_sec": len([u for u in job["expected_urls"] if _is_sec(u)]),
                    "sec_hits": 0,
                    "sec_misses": len([u for u in job["expected_urls"] if _is_sec(u)]),
                    "complete": False,
                    "sec_hit_rate": 0.0,
                }
            )

    by_set: dict[str, list] = defaultdict(list)
    for row in results:
        by_set[row["set"]].append(row)

    summary = {}
    for name, rows in sorted(by_set.items()):
        sec_exp = sum(r.get("expected_sec") or 0 for r in rows)
        sec_hits = sum(r.get("sec_hits") or 0 for r in rows)
        sec_success = sum(r.get("sec_success_hits") or 0 for r in rows)
        non_sec = sum(r.get("expected_non_sec") or 0 for r in rows)
        scorable = [r for r in rows if (r.get("expected_sec") or 0) > 0]
        complete = sum(1 for r in scorable if r.get("complete"))
        errored = sum(1 for r in rows if r.get("error"))
        summary[name] = {
            "jobs": len(rows),
            "jobs_with_sec_expect": len(scorable),
            "jobs_complete": complete,
            "job_complete_rate": (complete / len(scorable)) if scorable else None,
            "jobs_errored": errored,
            "expected_sec_docs": sec_exp,
            "sec_docs_hit": sec_hits,
            "sec_docs_fetched_ok": sec_success,
            "sec_doc_hit_rate": (sec_hits / sec_exp) if sec_exp else None,
            "expected_non_sec_docs": non_sec,
            "non_sec_note": (
                "IR/company pages are outside SECConnector; "
                "counted here but not scored as SEC misses"
                if non_sec
                else None
            ),
        }

    # Overall across every scored set (SEC docs only).
    all_sec = sum(s["expected_sec_docs"] for s in summary.values())
    all_hits = sum(s["sec_docs_hit"] for s in summary.values())
    all_jobs = sum(s["jobs"] for s in summary.values())
    all_scorable = sum(s["jobs_with_sec_expect"] for s in summary.values())
    all_complete = sum(s["jobs_complete"] for s in summary.values())

    report = {
        "measured_at": datetime.utcnow().isoformat() + "Z",
        "method": (
            "SECConnector.retrieve(include_primary=True, include_earnings=True, "
            "include_xbrl=True) per job window; match on accession+basename; "
            f"unique retrieves run with concurrency={concurrency}"
        ),
        "overall": {
            "jobs": all_jobs,
            "jobs_with_sec_expect": all_scorable,
            "jobs_with_every_sec_doc": all_complete,
            "job_complete_rate": (all_complete / all_scorable) if all_scorable else None,
            "expected_sec_docs": all_sec,
            "sec_docs_hit": all_hits,
            "sec_doc_hit_rate": (all_hits / all_sec) if all_sec else None,
        },
        "by_set": summary,
        "unscorable": unscorable,
        "jobs": results,
    }

    text = json.dumps(report, indent=2)
    if out:
        out.write_text(text)
        print(f"wrote {out}", flush=True)

    print("\n=== RETRIEVAL EVAL ===", flush=True)
    print(json.dumps(report["overall"], indent=2), flush=True)
    print("\n=== BY SET ===", flush=True)
    print(json.dumps(report["by_set"], indent=2), flush=True)
    print("\n=== UNSCORABLE ===", flush=True)
    print(json.dumps(report["unscorable"], indent=2), flush=True)
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("/tmp/retrieval_eval.json"))
    ap.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Parallel unique SECConnector.retrieve calls (default 8).",
    )
    args = ap.parse_args()
    asyncio.run(run(args.out, concurrency=args.concurrency))


if __name__ == "__main__":
    main()
