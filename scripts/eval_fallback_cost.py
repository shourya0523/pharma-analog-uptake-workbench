"""What the model is asked, and what it contributes, on unseen filings.

The revenue stage used to call the model on every in-budget filing and read the
tables beside it. `CLAIM_STRENGTH` ranks `llm` below every deterministic
producer, so a model row for a quarter a table had already answered could not
win a conflict - it could only agree, or become a `needs_review` row that had
already lost. The stage now runs the deterministic readers first and asks the
model only about filings they leave unanswered.

This measures both halves of that claim on the held-out corpus:

  * how many model calls the ordering saves, and
  * whether any row is lost by not asking - a period the model would have
    supplied that nothing deterministic did.

The second number is the one that matters. Saving calls is worthless if the
model was quietly carrying coverage, and this eval is how that stays honest.

Run it against both orderings to compare. The old ordering is not a flag,
because a flag would be a second implementation of it that could drift from
what actually shipped; check out the commit instead and run the same script:

    OPENROUTER_API_KEY=... python scripts/eval_fallback_cost.py --label shipped
    git worktree add /tmp/eager HEAD~1
    OPENROUTER_API_KEY=... python /tmp/eager/scripts/eval_fallback_cost.py --label eager

The corpus is the held-out one: four large filers with no product in seed/gold,
built by scripts/sourcing/fetch_holdout.py. Nothing about their conventions is
encoded anywhere in this repo, which is the point.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.db.models import Base, DrugJobORM, ExtractionRunORM
from app.domain.models import (
    ParsingStatus,
    RetrievalStatus,
    RetrievedSource,
    SourceType,
    new_id,
)
from app.parsing.documents import DocumentParser
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import LocalFileStore
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

WORK = pathlib.Path(os.environ.get("HOLDOUT_DIR", "/tmp/holdout"))

# One product per held-out issuer, chosen because it appears in that issuer's
# own earnings exhibits. None of them is in seed/gold.
PRODUCTS = {
    "Pfizer": ("Eliquis", "apixaban"),
    "AbbVie": ("Skyrizi", "risankizumab"),
    "Amgen": ("Repatha", "evolocumab"),
    "EliLilly": ("Mounjaro", "tirzepatide"),
}


class CountingLLM:
    """The real client, wrapped so every extraction request is recorded."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.asked: list[str] = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def extract_revenue(self, **kwargs):
        self.asked.append((kwargs.get("source_meta") or {}).get("url") or "?")
        return await self.inner.extract_revenue(**kwargs)


async def _parse(path: pathlib.Path, source_id: str, url: str):
    source = RetrievedSource(
        source_id=source_id,
        source_type=SourceType.EARNINGS_RELEASE,
        url=url,
        filing_type="8-K",
        retrieval_status=RetrievalStatus.SUCCESS,
    )
    parser = DocumentParser(file_store=LocalFileStore("/tmp/eval-fallback-fs"))
    doc = parser._parse_html(source, path.read_text(errors="ignore"))
    return source, doc


async def run_one(entry: dict) -> dict:
    issuer = entry["issuer"]
    product, generic = PRODUCTS[issuer]
    path = pathlib.Path(entry["path"])
    if not path.exists():
        return {"issuer": issuer, "skipped": "missing"}

    source, doc = await _parse(path, "s1", entry["url"])
    if doc.parsing_status != ParsingStatus.SUCCESS:
        return {"issuer": issuer, "skipped": "unparsed"}

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    job = DrugJobORM(
        id=new_id(), run_id=run.id, drug_name=product, generic_name=generic,
        manufacturer=issuer, status="running", quality_flags=[],
    )
    db.add(job)
    db.commit()

    orch = PipelineOrchestrator(db, file_store=LocalFileStore("/tmp/eval-fallback-fs"))
    counter = CountingLLM(orch.llm)
    orch.llm = counter

    rows = await orch._extract_revenue(
        job, [source], {"s1": doc}, {"quarterly_revenue": True, "llm_evidence_judge": False}
    )
    by_method = collections.Counter(r.extraction_method for r in rows)
    return {
        "issuer": issuer,
        "product": product,
        "filed": entry["filed"],
        "expected": entry["expected"],
        "llm_calls": len(counter.asked),
        "rows": len(rows),
        "periods": sorted({r.period for r in rows}),
        "by_method": dict(by_method),
        "llm_periods": sorted({r.period for r in rows if r.extraction_method == "llm"}),
        "deterministic_periods": sorted(
            {r.period for r in rows if r.extraction_method != "llm"}
        ),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--label",
        default="shipped",
        help="names the output file; use it to tell two arms apart",
    )
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    manifest = json.loads((WORK / "manifest.json").read_text())
    if args.limit:
        manifest = manifest[: args.limit]

    results = []
    for entry in manifest:
        try:
            results.append(await run_one(entry))
        except Exception as exc:  # noqa: BLE001 - one bad filing is not the run
            results.append({"issuer": entry["issuer"], "skipped": f"{type(exc).__name__}: {exc}"})

    scored = [r for r in results if "skipped" not in r]
    calls = sum(r["llm_calls"] for r in scored)
    rows = sum(r["rows"] for r in scored)
    llm_only = sum(len(set(r["llm_periods"]) - set(r["deterministic_periods"])) for r in scored)

    print(f"\nmode: {args.label}")
    print(f"documents scored: {len(scored)} / {len(results)}")
    print(f"model calls:      {calls}")
    print(f"rows stored:      {rows}")
    print(f"periods only the model supplied: {llm_only}")
    print()
    for r in results:
        if "skipped" in r:
            print(f"  - {r['issuer']:9} SKIP {r['skipped']}")
            continue
        print(
            f"  - {r['issuer']:9} {r['filed']} {r['product']:9} "
            f"calls={r['llm_calls']} rows={r['rows']:2} {r['by_method']}"
        )
    out = WORK / f"fallback_{args.label}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
