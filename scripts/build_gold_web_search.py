"""Evaluate the extraction pipeline against gold. Do not write seed/gold.

Gold is independently researched ground truth. This script runs the pipeline so
its output can be compared with seed/gold. Default output is a scratch directory.

Usage:
    cd backend && uv run python ../scripts/build_gold_web_search.py --out-dir /tmp/pipeline-eval
"""

# ruff: noqa: BLE001, DTZ011

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.analytics.lifecycle import latest_completed_quarter
from app.config import get_settings
from app.db.models import (
    Base,
    DatapointORM,
    DrugJobORM,
    ExtractionRunORM,
    UnresolvedQuarterORM,
)
from app.domain.models import (
    DrugInput,
    ExtractionOptions,
    JobStatus,
    RevenueCandidate,
    ValidationStatus,
    new_id,
)
from app.pipeline.orchestrator import PipelineOrchestrator
from app.quality.candidate_filters import filter_revenue_candidates
from app.quality.checks import quote_contains_value, run_quality_checks
from app.storage.filestore import get_file_store

GOLD_DIR = REPO_ROOT / "seed" / "gold"
MANIFEST_PATH = GOLD_DIR / "manifest.json"


def pipeline_eval_targets_gold(out_dir: Path, gold_dir: Path = GOLD_DIR) -> bool:
    """True when pipeline eval would overwrite independent gold."""

    resolved = Path(out_dir).resolve()
    gold = Path(gold_dir).resolve()
    return resolved == gold or gold in resolved.parents

ACCEPT_STATUSES = {
    ValidationStatus.CONFIRMED.value,
    ValidationStatus.AUTO_PASS.value,
    ValidationStatus.NEEDS_REVIEW.value,
}


def _row_score(dp: DatapointORM) -> int:
    flags = set(dp.issue_flags or [])
    score = 0
    if dp.validation_status == ValidationStatus.AUTO_PASS.value:
        score += 100
    elif dp.validation_status == ValidationStatus.CONFIRMED.value:
        score += 90
    elif "deterministic:product_quote_value_ok" in flags:
        score += 80
    if "extracted_from_table" in flags:
        score += 25
    if "llm_search_validated" in flags:
        score += 10
    if "derived_comparative_column" in flags:
        score -= 35
    if "conflict_with_higher_priority_source" in flags:
        score -= 15
    if "period_normalized" in flags:
        score -= 5
    return score


def _acceptable(dp: DatapointORM) -> bool:
    if dp.validation_status in {ValidationStatus.CONFIRMED.value, ValidationStatus.AUTO_PASS.value}:
        return True
    flags = set(dp.issue_flags or [])
    return "deterministic:product_quote_value_ok" in flags or "extracted_from_table" in flags


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _load_drugs(limit_drug: str | None = None) -> list[DrugInput]:
    rows: list[DrugInput] = []
    with (REPO_ROOT / "seed" / "example_drugs.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            if limit_drug and row["drug_name"] != limit_drug:
                continue
            rows.append(
                DrugInput(
                    drug_name=row["drug_name"].strip(),
                    generic_name=(row.get("generic_name") or None),
                    manufacturer=(row.get("manufacturer") or None),
                    ticker=(row.get("ticker") or None),
                    indication=(row.get("indication") or None),
                )
            )
    return rows


def _extraction_method(dp: DatapointORM) -> str:
    flags = set(dp.issue_flags or [])
    cite = dp.citation_json or {}
    source_type = cite.get("source_type") or ""
    if "llm_search_validated" in flags or source_type == "llm_search":
        return "openrouter_web_search"
    if dp.extraction_method == "table" or "extracted_from_table" in flags:
        return "table_extraction_web_search"
    if source_type in {"earnings_release", "sec_filing"}:
        return "earnings_exhibit_web_search"
    return "pipeline_web_search"


def _datapoint_to_gold(dp: DatapointORM, job: DrugJobORM, manifest: dict) -> dict | None:
    if dp.period_type != "quarterly":
        return None
    as_of = date.fromisoformat(manifest["as_of_date"]) if manifest.get("as_of_date") else None
    if as_of:
        end = latest_completed_quarter(as_of)
        if str(dp.period) > end:
            return None
    elif manifest.get("start_year") is not None:
        try:
            year = int(str(dp.period)[:4])
        except ValueError:
            return None
        if year < manifest["start_year"] or year > manifest["end_year"]:
            return None
    if dp.validation_status not in ACCEPT_STATUSES:
        return None
    if not _acceptable(dp):
        return None
    if not dp.source_quote or not quote_contains_value(dp.source_quote, dp.value_reported):
        return None

    cite = dp.citation_json or {}
    scope = dp.revenue_scope or "Unknown"
    geo = dp.geography or "Unknown"
    form = dp.formulation or "aggregate"
    gold_id = _slug(f"{job.drug_name}-{dp.period}-{scope}-{geo}-{form}")

    # Coerce quarter fields that models sometimes emit as "Q4"
    def _qi(v: object) -> int | None:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        m = re.search(r"(\d)", str(v))
        return int(m.group(1)) if m else None

    m = re.match(r"(\d{4})Q([1-4])", str(dp.period))
    cal_y = dp.calendar_year or (int(m.group(1)) if m else None)
    cal_q = _qi(dp.calendar_quarter) or (int(m.group(2)) if m else None)
    fis_y = dp.fiscal_year or cal_y
    fis_q = _qi(dp.fiscal_quarter) or cal_q

    notes = []
    if dp.issue_flags:
        notes.append(f"issue_flags={','.join(dp.issue_flags)}")
    if job.quality_flags:
        notes.append(f"job_flags={','.join(job.quality_flags)}")
    notes.append("Generated by build_gold_web_search.py from pipeline output.")

    row = {
        "gold_id": gold_id,
        "drug_name": job.drug_name,
        "generic_name": job.generic_name,
        "manufacturer": job.manufacturer,
        "period": dp.period,
        "fiscal_year": fis_y,
        "fiscal_quarter": fis_q,
        "calendar_year": cal_y,
        "calendar_quarter": cal_q,
        "value_reported": dp.value_reported,
        "value_normalized_usd_millions": dp.value_normalized_usd_millions,
        "currency": dp.currency or "USD",
        "unit": dp.unit or "millions",
        "metric": dp.metric or "revenue",
        "period_type": dp.period_type,
        "revenue_scope": dp.revenue_scope,
        "geography": dp.geography,
        "formulation": dp.formulation,
        "route_of_administration": dp.route_of_administration,
        "source_type": cite.get("source_type") or "sec_filing",
        "source_url": dp.source_url or cite.get("source_url") or "",
        "source_title": cite.get("source_title"),
        "source_quote": dp.source_quote,
        "source_date": cite.get("source_date"),
        "filing_type": cite.get("filing_type"),
        "accession_number": cite.get("accession_number"),
        "page_or_section": cite.get("page_or_section"),
        "extraction_method": _extraction_method(dp),
        "confidence_score": dp.confidence_score,
        "validation_status": "confirmed",
        "gold_notes": " ".join(notes),
    }
    payload = {k: row[k] for k in RevenueCandidate.model_fields if k in row}
    payload["confidence"] = row["confidence_score"]
    try:
        candidate = RevenueCandidate.model_validate(payload)
    except Exception:
        return None
    kept, _dropped = filter_revenue_candidates(
        [candidate.model_dump()],
        product=job.drug_name,
        generic=job.generic_name,
    )
    if not kept:
        return None
    return row


def _normalize_sources(sources: list) -> list[dict]:
    out: list[dict] = []
    for item in sources or []:
        if isinstance(item, str) and item.startswith("https://"):
            out.append({"source_url": item, "observation": "Checked during web-search gold build"})
        elif isinstance(item, dict) and str(item.get("source_url") or "").startswith("https://"):
            out.append(item)
    return out


def _unresolved_to_gold(row: UnresolvedQuarterORM, job: DrugJobORM, manifest: dict) -> dict | None:
    if manifest.get("as_of_date"):
        end = latest_completed_quarter(date.fromisoformat(manifest["as_of_date"]))
        if str(row.period) > end:
            return None
    else:
        try:
            year = int(str(row.period)[:4])
        except ValueError:
            return None
        if year < manifest["start_year"] or year > manifest["end_year"]:
            return None
    gold_id = _slug(f"{job.drug_name}-{row.period}-not-separately-disclosed")
    sources = _normalize_sources(row.sources_checked or [])
    if not sources:
        return None
    notes = row.reviewer_notes or ""
    if "not a zero-revenue label" not in notes:
        notes = (notes + " This is a non-disclosure label, not a zero-revenue label.").strip()
    return {
        "gold_id": gold_id,
        "drug_name": job.drug_name,
        "period": row.period,
        "reason_unresolved": row.reason_unresolved,
        "sources_checked": sources,
        "recommended_next_step": row.recommended_next_step,
        "confidence_that_unavailable": row.confidence_that_unavailable,
        "gold_notes": notes,
    }


async def _run_drug(db: Session, drug: DrugInput, options: ExtractionOptions) -> DrugJobORM:
    run = ExtractionRunORM(
        id=new_id(),
        status="running",
        options_json=options.model_dump(mode="json"),
    )
    db.add(run)
    job = DrugJobORM(
        id=new_id(),
        run_id=run.id,
        drug_name=drug.drug_name,
        generic_name=drug.generic_name,
        manufacturer=drug.manufacturer,
        ticker=drug.ticker,
        cik=drug.cik,
        indication=drug.indication,
        status=JobStatus.QUEUED.value,
    )
    db.add(job)
    db.commit()

    orch = PipelineOrchestrator(db, file_store=get_file_store())
    await orch.run_job(job.id)
    db.refresh(job)
    run.status = "completed"
    db.commit()
    return job


def _gold_key(row: dict) -> tuple:
    return (
        row["drug_name"],
        row["period"],
        row["revenue_scope"],
        row.get("geography") or "",
        row.get("formulation") or "",
    )


def _backfill_manual_revenue(revenue_rows: list[dict], manual_path: Path) -> list[dict]:
    if not manual_path.is_file():
        return revenue_rows
    index = {_gold_key(r) for r in revenue_rows}
    out = list(revenue_rows)
    for line in manual_path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = _gold_key(row)
        if key in index:
            continue
        row = {
            **row,
            "extraction_method": "manual_verified_web_search",
            "gold_notes": (row.get("gold_notes") or "")
            + " Retained from manual gold; pipeline web-search path did not recover this period.",
        }
        out.append(row)
        index.add(key)
    return out


def _backfill_manual_unresolved(unresolved_rows: list[dict], manual_path: Path) -> list[dict]:
    if not manual_path.is_file():
        return unresolved_rows
    index = {(r["drug_name"], r["period"]) for r in unresolved_rows}
    out = list(unresolved_rows)
    for line in manual_path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = (row["drug_name"], row["period"])
        if key in index:
            continue
        row = {
            **row,
            "gold_notes": (row.get("gold_notes") or "")
            + " Retained from manual gold; pipeline web-search path did not mark this unresolved.",
        }
        out.append(row)
        index.add(key)
    return out


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drug", default=None, help="Single drug name (default: all seed drugs)")
    parser.add_argument(
        "--out-dir",
        default="/tmp/pipeline-eval",
        help="Scratch directory for pipeline output. Must not be seed/gold.",
    )
    parser.add_argument("--db", default=None, help="SQLite path (default: temp under storage/)")
    parser.add_argument("--no-backfill-manual", action="store_true")
    parser.add_argument(
        "--manual-revenue",
        default=None,
        help="Manual quarterly_revenue.jsonl for backfill (default: seed/gold/quarterly_revenue.jsonl)",
    )
    parser.add_argument(
        "--manual-unresolved",
        default=None,
        help="Manual unresolved_quarters.jsonl for backfill",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir).resolve()
    if pipeline_eval_targets_gold(out_dir):
        print("Refusing to write pipeline output into seed/gold. Gold is independent ground truth.", file=sys.stderr)
        return 1

    settings = get_settings()
    if not settings.openrouter_api_key:
        print("OPENROUTER_API_KEY required", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST_PATH.read_text())
    out_dir.mkdir(parents=True, exist_ok=True)

    db_path = Path(args.db) if args.db else REPO_ROOT / "backend" / "storage" / "gold_build.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    as_of = date.fromisoformat(manifest["as_of_date"]) if manifest.get("as_of_date") else date.today()
    options = ExtractionOptions(
        lifecycle_coverage=True,
        as_of_date=as_of,
        earnings_until=as_of,
        earnings_max_exhibits=80,
    )

    drugs = _load_drugs(args.drug)
    print(f"Evaluating pipeline for {len(drugs)} drug(s) -> {out_dir}")

    revenue_rows: list[dict] = []
    unresolved_rows: list[dict] = []
    summary: list[dict] = []

    for drug in drugs:
        print(f"\n=== {drug.drug_name} ===")
        try:
            job = await _run_drug(db, drug, options)
        except Exception as exc:
            print(f"FAILED {drug.drug_name}: {exc}")
            summary.append({"drug": drug.drug_name, "status": "failed", "error": str(exc)})
            continue

        dps = db.query(DatapointORM).filter_by(job_id=job.id).all()
        unresolved = db.query(UnresolvedQuarterORM).filter_by(job_id=job.id).all()
        candidates: dict[tuple, tuple[int, dict]] = {}
        for dp in dps:
            row = _datapoint_to_gold(dp, job, manifest)
            if not row:
                continue
            key = (
                row["drug_name"],
                row["period"],
                row["revenue_scope"],
                row.get("geography") or "",
                row.get("formulation") or "",
            )
            score = _row_score(dp)
            prev = candidates.get(key)
            if prev is None or score > prev[0]:
                candidates[key] = (score, row)
        accepted = [row for _, row in candidates.values()]
        labeled = [{**r, "id": r["gold_id"]} for r in accepted]
        issues = run_quality_checks(labeled)
        bad = {i.affected_datapoint for i in issues if i.severity == "high"}
        if bad:
            accepted = [r for r in accepted if r["gold_id"] not in bad]
        revenue_rows.extend(accepted)
        reported_periods = {(r["drug_name"], r["period"]) for r in accepted}
        for u in unresolved:
            row = _unresolved_to_gold(u, job, manifest)
            if row and (row["drug_name"], row["period"]) not in reported_periods:
                unresolved_rows.append(row)

        print(
            f"  job_status={job.status} datapoints={len(dps)} "
            f"gold_revenue={len(accepted)} unresolved={len(unresolved)} "
            f"flags={job.quality_flags}"
        )
        summary.append(
            {
                "drug": drug.drug_name,
                "status": job.status,
                "datapoints": len(dps),
                "gold_revenue": len(accepted),
                "unresolved": len(unresolved),
                "quality_flags": job.quality_flags,
            }
        )

    revenue_rows.sort(key=lambda r: (r["drug_name"], r["period"], r["revenue_scope"]))
    unresolved_rows.sort(key=lambda r: (r["drug_name"], r["period"]))

    manual_revenue = Path(args.manual_revenue or GOLD_DIR / "quarterly_revenue.jsonl")
    manual_unresolved = Path(args.manual_unresolved or GOLD_DIR / "unresolved_quarters.jsonl")
    if not args.no_backfill_manual:
        before_r, before_u = len(revenue_rows), len(unresolved_rows)
        revenue_rows = _backfill_manual_revenue(revenue_rows, manual_revenue)
        unresolved_rows = _backfill_manual_unresolved(unresolved_rows, manual_unresolved)
        revenue_rows.sort(key=lambda r: (r["drug_name"], r["period"], r["revenue_scope"]))
        unresolved_rows.sort(key=lambda r: (r["drug_name"], r["period"]))
        print(
            f"Manual backfill: +{len(revenue_rows)-before_r} revenue, "
            f"+{len(unresolved_rows)-before_u} unresolved"
        )

    _write_jsonl(out_dir / "quarterly_revenue.jsonl", revenue_rows)
    _write_jsonl(out_dir / "unresolved_quarters.jsonl", unresolved_rows)

    build_report = {
        "generated": date.today().isoformat(),
        "pipeline": "openrouter_web_search",
        "drugs": len(drugs),
        "revenue_rows": len(revenue_rows),
        "unresolved_rows": len(unresolved_rows),
        "summary": summary,
    }
    (out_dir / "build_report.json").write_text(json.dumps(build_report, indent=2))

    print(f"\nWrote {len(revenue_rows)} revenue rows and {len(unresolved_rows)} unresolved rows")
    print(f"Report: {out_dir / 'build_report.json'}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
