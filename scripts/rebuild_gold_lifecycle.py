"""Assemble lifecycle and peak files from independently researched gold revenue.

Does not extract revenue. Reads cited quarterly_revenue.jsonl and writes
lifecycle.jsonl / peak_sales.jsonl / unresolved gaps. Prefer
`research_gold_from_filings.py` when refreshing gold from filings.

Usage:
    cd backend && uv run python ../scripts/rebuild_gold_lifecycle.py
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.analytics.gold_dataset import (
    fill_lifecycle_unresolved,
    lifecycle_record,
    peak_record,
    promote_lifecycle_history,
    reported_periods,
)
from app.analytics.lifecycle import latest_completed_quarter
from app.connectors.openfda import search_queries
from app.connectors.openfda_fields import selected_approval_date
from app.llm.aliases import merge_aliases

GOLD = REPO_ROOT / "seed" / "gold"
SEED = REPO_ROOT / "seed" / "example_drugs.csv"
ARCHIVE = GOLD / "archive" / "window-2022-2026"
AS_OF = date(2026, 8, 28)


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def dump_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""))


def load_seed() -> list[dict]:
    with SEED.open(newline="") as handle:
        return list(csv.DictReader(handle))


def archive_window_gold() -> None:
    if ARCHIVE.exists():
        return
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    for name in (
        "manifest.json",
        "quarterly_revenue.jsonl",
        "unresolved_quarters.jsonl",
        "edge_cases.jsonl",
        "build_report.json",
        "audit_report.json",
        "README.md",
    ):
        src = GOLD / name
        if src.is_file():
            shutil.copy2(src, ARCHIVE / name)


def fetch_approval(brand: str, generic: str | None, aliases: list[str]) -> tuple[date | None, str | None]:
    queries = search_queries(brand, generic)
    for alias in aliases:
        if alias.lower() == brand.lower():
            continue
        extra = search_queries(alias, None)
        for item in extra:
            if item not in queries:
                queries.append(item)
    with httpx.Client(timeout=30) as client:
        for _scope, search in queries:
            url = f"https://api.fda.gov/drug/drugsfda.json?search={search}&limit=10"
            try:
                resp = client.get(url)
            except httpx.HTTPError:
                continue
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            results = resp.json().get("results") or []
            found = selected_approval_date(results, product=brand, generic=generic, aliases=aliases)
            if found:
                return found, url
    return None, None


def rewrite_edge_cases(edges: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in edges:
        if row.get("case_type") != "old_record":
            out.append(row)
            continue
        candidate = row.get("candidate") or {}
        if (candidate.get("currency") or "USD").upper() != "USD":
            out.append(
                {
                    **row,
                    "case_type": "cross_currency_unresolved",
                    "expected_disposition": "keep_unresolved_without_fx",
                    "expected_reason": "cross_currency_no_cited_fx",
                    "edge_notes": (
                        (row.get("edge_notes") or "")
                        + " In-lifecycle history, but cross-currency without a cited FX observation."
                    ).strip(),
                }
            )
        # USD old_record rows are promoted into quarterly_revenue instead of remaining edges
    return out


def main() -> int:
    archive_window_gold()
    seed = load_seed()
    revenue = load_jsonl(GOLD / "quarterly_revenue.jsonl")
    for row in revenue:
        row.setdefault("metric", "revenue")
    unresolved = load_jsonl(GOLD / "unresolved_quarters.jsonl")
    edges = load_jsonl(GOLD / "edge_cases.jsonl")

    promoted = []
    for edge in edges:
        row = promote_lifecycle_history(edge)
        if not row:
            continue
        seed_row = next((item for item in seed if item["drug_name"] == row["drug_name"]), {})
        row.setdefault("manufacturer", seed_row.get("manufacturer"))
        promoted.append(row)
    existing_keys = {
        (row["drug_name"], row["period"], row.get("revenue_scope"), row.get("geography"), row.get("formulation"))
        for row in revenue
    }
    for row in promoted:
        key = (
            row["drug_name"],
            row["period"],
            row.get("revenue_scope"),
            row.get("geography"),
            row.get("formulation"),
        )
        if key not in existing_keys:
            revenue.append(row)
            existing_keys.add(key)

    approvals: dict[str, tuple[date | None, str | None]] = {}
    for item in seed:
        aliases = merge_aliases(item["drug_name"], item.get("generic_name"))
        approval, url = fetch_approval(item["drug_name"], item.get("generic_name"), aliases)
        approvals[item["drug_name"]] = (approval, url)
        print(f"approval {item['drug_name']}={approval} source={url}")

    expanded_unresolved: list[dict] = []
    lifecycle_rows: list[dict] = []
    peak_rows: list[dict] = []
    for item in seed:
        drug = item["drug_name"]
        approval, url = approvals[drug]
        reported = reported_periods(revenue, drug)
        drug_unresolved = fill_lifecycle_unresolved(
            drug_name=drug,
            approval_date=approval,
            as_of=AS_OF,
            reported=reported,
            existing_unresolved=unresolved,
            source_rows=revenue,
        )
        expanded_unresolved.extend(drug_unresolved)
        life = lifecycle_record(
            drug_name=drug,
            approval_date=approval,
            as_of=AS_OF,
            reported=reported,
            unresolved={row["period"] for row in drug_unresolved},
            approval_source_url=url,
        )
        drug_revenue = [row for row in revenue if row["drug_name"] == drug]
        peak = peak_record(
            drug_name=drug,
            rows=drug_revenue,
            as_of=AS_OF,
            expected_count=life["expected_quarter_count"],
        )
        life["peak_eligible"] = peak["peak_eligible"]
        lifecycle_rows.append(life)
        peak_rows.append(peak)

    revenue.sort(key=lambda row: (row["drug_name"], row["period"], row.get("revenue_scope") or ""))
    expanded_unresolved.sort(key=lambda row: (row["drug_name"], row["period"]))
    lifecycle_rows.sort(key=lambda row: row["drug_name"])
    peak_rows.sort(key=lambda row: row["drug_name"])
    new_edges = rewrite_edge_cases(edges)

    dump_jsonl(GOLD / "quarterly_revenue.jsonl", revenue)
    dump_jsonl(GOLD / "unresolved_quarters.jsonl", expanded_unresolved)
    dump_jsonl(GOLD / "lifecycle.jsonl", lifecycle_rows)
    dump_jsonl(GOLD / "peak_sales.jsonl", peak_rows)
    dump_jsonl(GOLD / "edge_cases.jsonl", new_edges)

    manifest = {
        "coverage_mode": "full_lifecycle",
        "as_of_date": AS_OF.isoformat(),
        "as_of_quarter": latest_completed_quarter(AS_OF),
        "target_drug_count": len(seed),
        "purpose": (
            "Independent source of truth assembled from cited issuer filings/IR. "
            "Does not run the extraction pipeline."
        ),
        "generation": "independent_filing_research",
        "reported_rows_file": "quarterly_revenue.jsonl",
        "unresolved_rows_file": "unresolved_quarters.jsonl",
        "lifecycle_file": "lifecycle.jsonl",
        "peak_sales_file": "peak_sales.jsonl",
        "edge_cases_file": "edge_cases.jsonl",
        "metadata_file": "metadata.jsonl",
    }
    (GOLD / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    report = {
        "generated": AS_OF.isoformat(),
        "pipeline": "independent_filing_research",
        "as_of_quarter": manifest["as_of_quarter"],
        "drugs": len(seed),
        "revenue_rows": len(revenue),
        "unresolved_rows": len(expanded_unresolved),
        "promoted_lifecycle_history": len(promoted),
        "peak_eligible": sum(1 for row in peak_rows if row["peak_eligible"]),
        "revenue_by_drug": dict(Counter(row["drug_name"] for row in revenue)),
        "unresolved_by_drug": dict(Counter(row["drug_name"] for row in expanded_unresolved)),
        "coverage_pct_by_drug": {row["drug_name"]: row["coverage_pct"] for row in lifecycle_rows},
    }
    (GOLD / "build_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
