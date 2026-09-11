"""Run the pipeline itself against filers no answer key contains.

`eval_foreign_filer_xbrl.py` calls the tagged reader directly on an instance
that has already been located. This walks EDGAR from an issuer name, the way a
job does: resolve the CIK, find the filings, pick the instances, read them,
derive, check, judge and publish. It is the same `run_job` the gold end-to-end
eval uses, pointed at `seed/holdout_foreign_xbrl.json`.

What is scored is what the pipeline **published** - `auto_pass`, the only
status not waiting on a reviewer. A case expecting nothing is scored too, and
is the more interesting half: when the deterministic readers find nothing the
model is asked, and a name like "Biopharma" or "Product Revenue" is exactly
where it might answer anyway.

The expectation is the figure printed in the filing the case cites, not gold.
"""

# ruff: noqa: BLE001 - a job that raises is an outcome to record, not a crash
from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import json
import logging
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "backend"))

import eval_extraction_documents as E  # noqa: E402
from app.db.models import (  # noqa: E402
    Base,
    DatapointORM,
    DrugJobORM,
    ExtractionRunORM,
    SourceDocumentORM,
)
from app.domain.models import PeriodType, ValidationStatus, new_id  # noqa: E402
from app.pipeline.orchestrator import PipelineOrchestrator  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

HOLDOUT = REPO / "seed" / "holdout_foreign_xbrl.json"
PUBLISHED = {ValidationStatus.AUTO_PASS.value, ValidationStatus.CONFIRMED.value}


def window(filed: str) -> tuple[dt.date, dt.date]:
    """Filing dates bracketing the report this case cites.

    Wide enough to catch the filing itself and the one either side of it, so a
    miss is the reader's rather than the window's.
    """
    on = dt.date.fromisoformat(filed)
    return on - dt.timedelta(days=200), on + dt.timedelta(days=40)


async def run_one(orch, db, case: dict, options: dict) -> dict:
    since, until = window(case["filed"])
    run = ExtractionRunORM(id=new_id(), status="running", options_json={
        **options,
        "earnings_since": since.isoformat(),
        "earnings_until": until.isoformat(),
    })
    db.add(run)
    job = DrugJobORM(
        id=new_id(), run_id=run.id,
        drug_name=case["product"],
        generic_name=None,
        manufacturer=case["issuer"],
        ticker=case["ticker"],
        status="queued", quality_flags=[],
    )
    db.add(job)
    db.commit()

    started = time.time()
    error = None
    try:
        await orch.run_job(job.id)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.time() - started

    points = db.query(DatapointORM).filter_by(job_id=job.id).all()
    sources = db.query(SourceDocumentORM).filter_by(job_id=job.id).all()
    quarterly = [
        {"period": str(p.period), "value": p.value_normalized_usd_millions,
         "status": p.validation_status, "method": p.extraction_method,
         "scope": p.revenue_scope, "flags": list(p.issue_flags or [])}
        for p in points
        if p.period_type == PeriodType.QUARTERLY.value
        and p.value_normalized_usd_millions is not None
    ]
    published = [q for q in quarterly if q["status"] in PUBLISHED]

    if case["expect"] == "a figure":
        hit = next((q for q in published if q["period"] == case["period"]), None)
        ok = hit is not None and abs(hit["value"] - case["printed_in_the_filing"]) < 0.5
        detail = (f"published {hit['value']:,.1f}m via {hit['method']}"
                  if hit else "nothing published for that quarter")
    else:
        ok = not published
        detail = ("nothing published" if ok else
                  "published " + ", ".join(f"{q['period']}={q['value']:,.1f}m "
                                           f"({q['method']})" for q in published[:3]))
    return {
        "ticker": case["ticker"], "issuer": case["issuer"], "product": case["product"],
        "expect": case["expect"], "why": case["why"], "ok": ok, "detail": detail,
        "printed": case.get("printed_in_the_filing"), "period": case.get("period"),
        "sources": len(sources),
        "sources_ok": sum(1 for s in sources if s.retrieval_status == "success"),
        "datapoints": len(points), "quarterly": len(quarterly),
        "published": len(published), "held": len(quarterly) - len(published),
        "error": error, "elapsed": round(elapsed, 1), "job_id": job.id,
        "candidates": quarterly[:8],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", action="append", default=[],
                    help="TICKER:PRODUCT, repeatable; default is every case")
    ap.add_argument("--expect", default="", choices=["", "a figure", "nothing"],
                    help="only cases with this expectation")
    ap.add_argument("--out", default="/tmp/e2e_foreign.json")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")

    cases = json.loads(HOLDOUT.read_text())
    # A negative whose label the filer prints as a line item tests the tagged
    # reader and nothing else: the table reader may read that line off the page
    # and be right to. `eval_foreign_filer_xbrl.py` scores those.
    reader_only = [c for c in cases if "pipeline" not in c.get("scored", ["pipeline"])]
    cases = [c for c in cases if "pipeline" in c.get("scored", ["pipeline"])]
    if args.expect:
        cases = [c for c in cases if c["expect"] == args.expect]
    if args.case:
        wanted = {(t.strip(), p.strip()) for t, _, p in
                  (spec.partition(":") for spec in args.case)}
        cases = [c for c in cases if (c["ticker"], c["product"]) in wanted]
    if not cases:
        print("nothing selected")
        return 1

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    store = E.LocalCacheStore(pathlib.Path(os.environ.get("DISCOVER_CACHE", "/tmp/discovered")))
    orch = PipelineOrchestrator(db, file_store=store)
    options = {
        "sec_filings": True, "earnings_releases": True, "quarterly_revenue": True,
        "openfda": False, "product_metadata": False, "llm_evidence_judge": True,
    }

    async def go() -> list[dict]:
        out = []
        for index, case in enumerate(cases, 1):
            print(f"  [{index}/{len(cases)}] {case['product']} ({case['ticker']}) "
                  f"expect {case['expect']}", flush=True)
            out.append(await run_one(orch, db, case, options))
        return out

    results = asyncio.run(go())

    print(f"\n  {'':4} {'tick':5} {'product':28} {'expect':9} detail")
    for r in results:
        print(f"  {'ok  ' if r['ok'] else 'MISS'} {r['ticker']:5} {r['product'][:28]:28} "
              f"{r['expect']:9} {r['detail']}"
              + (f"   [{r['error']}]" if r["error"] else ""))
    correct = sum(1 for r in results if r["ok"])
    by_expect = collections.Counter(
        (r["expect"], r["ok"]) for r in results)
    print(f"\n  {correct}/{len(results)} correct end to end")
    if reader_only:
        print(f"  {len(reader_only)} case(s) not scored here, being reader-level only: "
              + ", ".join(f"{c['ticker']}:{c['product']}" for c in reader_only[:4])
              + (" ..." if len(reader_only) > 4 else ""))
    for expect in ("a figure", "nothing"):
        good = by_expect[(expect, True)]
        total = good + by_expect[(expect, False)]
        if total:
            print(f"    expect {expect:9}: {good}/{total}")
    reached = sum(r["sources_ok"] for r in results)
    print(f"  {reached} sources retrieved across {len(results)} jobs; "
          f"{sum(r['held'] for r in results)} quarterly datapoints held for review")
    pathlib.Path(args.out).write_text(json.dumps(results, indent=1, default=str))
    print(f"  detail written to {args.out}")
    return 0 if correct == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
