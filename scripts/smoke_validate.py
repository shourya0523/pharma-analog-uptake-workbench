"""Smoke-test the extraction API and validate stored values against the gold dataset.

Usage:
    uv run python ../scripts/smoke_validate.py --drug Tyvaso --known-source-url <url>

Reads results back from the API and from SQLite so extracted values can be compared
field by field against seed/gold/quarterly_revenue.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.domain.models import JobStatus  # noqa: E402
from app.identity.resolver import resolve_product_identity  # noqa: E402
from app.parsing.fda_label import parse_label_record  # noqa: E402
from app.parsing.indications import parse_indications  # noqa: E402

TERMINAL = {
    JobStatus.READY_FOR_REVIEW.value,
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
}
COMPARE_FIELDS = [
    "period",
    "period_type",
    "value_reported",
    "value_normalized_usd_millions",
    "revenue_scope",
    "validation_status",
    "source_url",
    "source_quote",
]


def _post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _get(base: str, path: str, timeout: int = 120) -> dict:
    with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def sqlite_path() -> Path:
    url = get_settings().database_url
    return Path(url.split("///", 1)[1]) if "///" in url else Path("storage/workbench.db")


def gold_rows(drug: str) -> list[dict]:
    path = REPO_ROOT / "seed" / "gold" / "quarterly_revenue.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if r["drug_name"] == drug]


def wait_for_run(base: str, run_id: str, timeout_s: int) -> dict:
    deadline = time.time() + timeout_s
    last = None
    data: dict = {}
    while time.time() < deadline:
        data = _get(base, f"/runs/{run_id}", timeout=60)
        job = data["jobs"][0]
        snapshot = (data["status"], job["status"], job["current_step"], job.get("candidates_extracted"))
        if snapshot != last:
            print(
                f"{time.strftime('%H:%M:%S')} run={data['status']} job={job['status']} "
                f"step={job['current_step']} sources={job.get('sources_found')} "
                f"candidates={job.get('candidates_extracted')} error={job.get('error')}"
            )
            last = snapshot
        if job["status"] in TERMINAL and data["status"] not in {"queued", "running"}:
            return data
        time.sleep(4)
    print("TIMEOUT waiting for terminal run status")
    return data


def compare_api_to_db(api_datapoints: list[dict], db_datapoints: list[dict]) -> list[dict]:
    api_by_id = {d["id"]: d for d in api_datapoints}
    mismatches = []
    for row in db_datapoints:
        api_row = api_by_id.get(row["id"])
        if api_row is None:
            mismatches.append({"id": row["id"], "field": "*", "db": "present", "api": "missing"})
            continue
        for field in COMPARE_FIELDS:
            if row[field] != api_row.get(field):
                mismatches.append(
                    {"id": row["id"], "field": field, "db": row[field], "api": api_row.get(field)}
                )
    return mismatches


def compare_to_gold(datapoints: list[dict], gold: list[dict], tolerance: float = 0.05) -> dict:
    details = []
    for row in sorted(gold, key=lambda r: r["period"]):
        candidates = [d for d in datapoints if d["period"] == row["period"]]
        match = any(
            d["value_reported"] is not None
            and abs(float(d["value_reported"]) - float(row["value_reported"])) < tolerance
            for d in candidates
        )
        details.append(
            {
                "period": row["period"],
                "gold_value": row["value_reported"],
                "extracted_values": [d["value_reported"] for d in candidates],
                "validation_statuses": [d["validation_status"] for d in candidates],
                "match": match,
            }
        )
    return {"matched": sum(1 for d in details if d["match"]), "total": len(details), "details": details}


def validate_metadata_gold() -> int:
    path = REPO_ROOT / "seed" / "gold" / "metadata.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for row in rows:
        if row["case_type"] == "label":
            parsed = parse_label_record(row["record"])
            if "epc_terms" in row["expected"]:
                assert parsed.epc_terms == row["expected"]["epc_terms"]
                assert parsed.moa_terms == row["expected"]["moa_terms"]
            else:
                assert len(parsed.active_ingredients) == row["expected"]["ingredient_count"]
                assert len(parsed.moa_terms) == row["expected"]["moa_count"]
        elif row["case_type"] == "indication":
            parsed = parse_indications(row["text"])[0]
            assert parsed.approved_lot.value.value == row["expected"]["approved_lot"]
        elif row["case_type"] == "identity":
            identities = [resolve_product_identity(**item) for item in row["products"]]
            assert identities[0].analog_family_key == identities[1].analog_family_key
            assert identities[0].identity_key != identities[1].identity_key
    print(f"metadata_gold_validated={len(rows)} production_parsers=true")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--drug", default="Tyvaso")
    parser.add_argument("--generic", default="treprostinil")
    parser.add_argument("--manufacturer", default="United Therapeutics")
    parser.add_argument("--ticker", default="UTHR")
    parser.add_argument("--known-source-url", default=None)
    parser.add_argument("--sec-filings", action="store_true")
    parser.add_argument("--openfda", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--out", default=None)
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()
    if args.metadata_only:
        validate_metadata_gold()
        return 0

    drug: dict[str, object] = {
        "drug_name": args.drug,
        "generic_name": args.generic,
        "manufacturer": args.manufacturer,
        "ticker": args.ticker,
    }
    if args.known_source_url:
        drug["known_source_url"] = args.known_source_url

    created = _post(
        args.base_url,
        "/runs",
        {
            "drugs": [drug],
            "options": {
                "sec_filings": args.sec_filings,
                "openfda": args.openfda,
                "company_ir": bool(args.known_source_url),
                "quarterly_revenue": True,
                "product_metadata": False,
                "llm_evidence_judge": True,
            },
        },
    )
    print("created", created)
    run = wait_for_run(args.base_url, created["run_id"], args.timeout)
    job_id = run["jobs"][0]["id"]

    api_job = _get(args.base_url, f"/jobs/{job_id}", timeout=180)

    conn = sqlite3.connect(str(sqlite_path()))
    conn.row_factory = sqlite3.Row
    try:
        db_job = dict(conn.execute("select * from drug_jobs where id=?", (job_id,)).fetchone())
        db_run = dict(
            conn.execute("select * from extraction_runs where id=?", (created["run_id"],)).fetchone()
        )
        db_datapoints = [
            dict(r)
            for r in conn.execute(
                "select id, period, period_type, value_reported, value_normalized_usd_millions, "
                "revenue_scope, validation_status, source_support, confidence_score, source_url, "
                "source_quote from datapoints where job_id=? order by period, period_type",
                (job_id,),
            )
        ]
        db_sources = [
            dict(r)
            for r in conn.execute(
                "select source_type, retrieval_status, parsing_status, relevant_datapoints_found, "
                "source_url, notes from source_documents where job_id=?",
                (job_id,),
            )
        ]
    finally:
        conn.close()

    mismatches = compare_api_to_db(api_job["datapoints"], db_datapoints)
    gold = compare_to_gold(db_datapoints, gold_rows(args.drug))

    print(f"\nrun_status={db_run['status']} job_status={db_job['status']} error={db_job['error']}")
    print(f"api_datapoints={len(api_job['datapoints'])} db_datapoints={len(db_datapoints)}")
    print(f"api_vs_db_field_mismatches={len(mismatches)}")
    print("\nextracted values (from sqlite):")
    for row in db_datapoints:
        quote = (row["source_quote"] or "").replace("\n", " ")[:70]
        print(
            f"  {row['period']:8} {row['period_type'] or '':10} {row['value_reported']!s:>8} "
            f"{row['revenue_scope']:18} {row['validation_status']:14} {quote}"
        )
    print(f"\ngold comparison: matched {gold['matched']}/{gold['total']}")
    for detail in gold["details"]:
        flag = "MATCH" if detail["match"] else "MISS"
        print(
            f"  {detail['period']:8} gold={detail['gold_value']:>8} "
            f"extracted={detail['extracted_values']} {flag}"
        )
    print("\nsources:")
    for source in db_sources:
        print(
            f"  {source['source_type']:14} {source['retrieval_status']:10} {source['parsing_status']:10} "
            f"datapoints={source['relevant_datapoints_found']} {(source['source_url'] or '')[:80]}"
        )

    report = {
        "run_id": created["run_id"],
        "job_id": job_id,
        "run_status": db_run["status"],
        "job": {
            key: db_job[key]
            for key in [
                "drug_name",
                "status",
                "current_step",
                "cik",
                "sources_found",
                "candidates_extracted",
                "auto_pass_count",
                "needs_review_count",
                "unresolved_count",
                "completeness_pct",
                "error",
            ]
        },
        "api_datapoint_count": len(api_job["datapoints"]),
        "db_datapoint_count": len(db_datapoints),
        "api_vs_db_mismatches": mismatches,
        "datapoints": db_datapoints,
        "sources": db_sources,
        "gold_comparison": gold,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
        print(f"\nwrote {args.out}")
    return 0 if db_job["status"] == JobStatus.READY_FOR_REVIEW.value and not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
