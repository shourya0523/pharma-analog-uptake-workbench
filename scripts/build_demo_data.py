"""Build the static demo's API responses from the gold dataset.

The demo is the frontend with no backend behind it: every GET it makes is
answered from a JSON file this script writes under frontend/demo-data/demo/. The
responses carry the field names the live API serves (backend/app/api/products.py
and backend/app/dashboard/series.py), so the pages render them unchanged.

This reads gold and writes only frontend files. Nothing under seed/ is written,
and the pipeline never reads frontend/, so the answer key does not flow back
into anything it scores (CLAUDE.md rule 3).

Launch-relative uptake is computed with the pipeline's own
``calculate_revenue_uptake``: trailing four quarters over gold's observed peak,
anchored at the series' commercial start quarter, because gold records the
approval year but not the date. A product whose peak gold has not observed gets
no launch curve, which is the same refusal the live dashboard makes.

    cd backend && ./.venv/bin/python ../scripts/build_demo_data.py
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.analytics.peak_sales import SalesObservation, SelectedPeak  # noqa: E402
from app.analytics.uptake import calculate_revenue_uptake  # noqa: E402
from app.dashboard.series import scope_rank  # noqa: E402

GOLD = REPO / "seed" / "gold"
OUT = REPO / "frontend" / "demo-data" / "demo"


def read_jsonl(name: str) -> list[dict]:
    path = GOLD / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def quarter_start(period: str) -> date | None:
    match = re.fullmatch(r"(\d{4})Q([1-4])", period or "")
    if not match:
        return None
    return date(int(match[1]), 3 * int(match[2]) - 2, 1)


def quarter_end(period: str) -> date | None:
    start = quarter_start(period)
    if start is None:
        return None
    month = start.month + 2
    return date(start.year, month, 31 if month in (3, 12) else 30)


def approval_period(year: int | None) -> str | None:
    if not year:
        return None
    start = (year // 5) * 5
    return f"{start}-{start + 4}"


def peak_bucket(value: float | None) -> str | None:
    if value is None:
        return None
    if value < 500:
        return "<$500M"
    if value <= 1000:
        return "$500M-$1B"
    return ">$1B"


def figure(row: dict) -> dict:
    """One gold row in the shape the product page's quarters table reads."""
    return {
        "id": row["gold_id"],
        "period": row["period"],
        "period_type": row["period_type"],
        "value_normalized_usd_millions": row.get("value_normalized_usd_millions"),
        "currency": row.get("currency"),
        "revenue_scope": row.get("revenue_scope"),
        "geography": row.get("geography"),
        "formulation": row.get("formulation"),
        "reported_as": None,
        "series_identity": row.get("benchmark_identity"),
        "series_selection": "selected",
        "issue_flags": [],
        "source_url": row.get("source_url"),
        "source_quote": row.get("source_quote"),
        "extraction_method": row.get("extraction_method"),
        "confidence_score": row.get("confidence_score"),
        "validation_status": row.get("validation_status"),
        "in_queue": False,
        "queue_reason": None,
    }


def main() -> None:
    quarterly = read_jsonl("quarterly_revenue")
    annual = read_jsonl("annual_revenue")
    profiles = {row["drug_name"]: row for row in read_jsonl("product_profiles")}
    peaks = {row["drug_name"]: row for row in read_jsonl("peak_sales")}
    coverage: dict[str, list[dict]] = defaultdict(list)
    for row in read_jsonl("series_coverage"):
        coverage[row["drug_name"]].append(row)

    rows_by_product: dict[str, list[dict]] = defaultdict(list)
    for row in quarterly + annual:
        rows_by_product[row["drug_name"]].append(row)

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "products").mkdir(parents=True)

    library: list[dict] = []
    dash_products: list[dict] = []
    series: list[dict] = []
    launch_series: list[dict] = []

    for name in sorted(rows_by_product, key=str.lower):
        rows = sorted(rows_by_product[name], key=lambda r: (r["period"], r.get("benchmark_identity") or ""))
        latest = max(rows, key=lambda r: r["period"])
        profile = profiles.get(name, {})
        peak = peaks.get(name)
        cov = coverage.get(name, [])
        product_id = slug(name)
        quarter_periods = {r["period"] for r in rows if r["period_type"] == "quarterly"}
        missing = sorted({q for c in cov for q in c.get("missing_quarters") or []})
        completeness = (
            round(sum(c["coverage_pct"] for c in cov) / len(cov), 1) if cov else (100.0 if quarter_periods else 0.0)
        )
        starts = sorted(c["commercial_start_quarter"] for c in cov if c.get("commercial_start_quarter"))
        commercial_start = starts[0] if starts else (min(quarter_periods) if quarter_periods else None)
        ends = sorted(c["series_end_quarter"] for c in cov if c.get("series_end_quarter"))
        peak_value = peak.get("peak_value") if peak else None

        library.append(
            {
                "id": product_id,
                "name": name,
                "generic": latest.get("generic_name"),
                "company": latest.get("manufacturer"),
                "indication": profile.get("indication_area") or latest.get("therapeutic_area"),
                "moa": profile.get("moa"),
                "roa": profile.get("route_of_administration") or latest.get("route_of_administration"),
                "cadence": None,
                "has_profile": bool(profile),
                "completeness_pct": completeness,
                "quarters": len(quarter_periods),
                "flagged": 0,
                "missing": len(missing),
                "last_job_id": None,
                "last_run_at": None,
                "last_run_status": "gold",
            }
        )

        profile_fields = [
            {"id": f"{product_id}-{key}", "field": key, "value": str(profile[key]),
             "citation": None, "validation_status": profile.get("attribute_provenance") or "curated_reference"}
            for key in ("moa", "moa_class", "route_of_administration", "indication_area",
                        "first_approval_year", "approval_era", "competitive_intensity_at_launch")
            if profile.get(key) is not None
        ]
        detail = {
            "id": product_id,
            "name": name,
            "generic": latest.get("generic_name"),
            "company": latest.get("manufacturer"),
            "regulatory_sponsor": None,
            "application_number": None,
            "initial_approval_date": str(profile["first_approval_year"]) if profile.get("first_approval_year") else None,
            "cadence": None,
            "has_profile": bool(profile),
            "indication": profile.get("indication_area"),
            "therapeutic_area": latest.get("therapeutic_area"),
            "approved_lot": None,
            "approved_lot_quote": None,
            "moa": profile.get("moa"),
            "epc": [],
            "dosage_form": latest.get("formulation"),
            "route": profile.get("route_of_administration"),
            "completeness_pct": completeness,
            "latest_job_id": None,
            "profile": profile_fields,
            "quarters": [figure(r) for r in rows],
            "missing_quarters": [
                {"id": f"{product_id}-{q}", "period": q, "reason_unresolved": "not_found_in_gold_sources",
                 "recommended_next_step": None, "confidence_that_unavailable": None}
                for q in missing
            ],
            "timeline": [],
        }
        (OUT / "products" / f"{product_id}.json").write_text(json.dumps(detail))

        selected_peak = (
            {"id": peak["gold_id"], "type": "observed", "value": peak_value, "currency": peak.get("currency"),
             "geography": peak.get("geography"), "revenue_scope": peak.get("revenue_scope"),
             "as_of_date": None, "selection_reason": peak.get("selection_method"), "input_ids": peak.get("input_ids")}
            if peak_value
            else None
        )
        product = {
            "job_id": product_id,
            "canonical_product_id": product_id,
            "product_name": name,
            "therapeutic_area": profile.get("indication_area") or latest.get("therapeutic_area"),
            "manufacturer": latest.get("manufacturer"),
            "company": latest.get("manufacturer"),
            "fda_approval_date": str(profile["first_approval_year"]) if profile.get("first_approval_year") else None,
            "approval_period": approval_period(profile.get("first_approval_year")),
            "launch_quarter": commercial_start,
            "commercial_start_quarter": commercial_start,
            "series_end_quarter": ends[-1] if ends else None,
            "series_end_reason": None,
            "approved_indications": profile.get("indication_area"),
            "indication_count": 1 if profile.get("indication_area") else 0,
            "indications": [],
            "moa": profile.get("moa"),
            "pharmacologic_class": profile.get("moa_class"),
            "roa": profile.get("route_of_administration"),
            "treatment_type": None,
            "approved_lot": None,
            "competitive_intensity": profile.get("competitive_intensity_at_launch"),
            "competitive_snapshot": None,
            "selected_peak": selected_peak,
            "estimated_peak_revenue": peak_value,
            "peak_sales_bucket": peak_bucket(peak_value),
            "peak_type": "observed" if peak_value else None,
            "reached_peak_yet": None if not peak else peak.get("peak_status") == "observed",
            "time_to_peak": None,
            "source_link": next((r.get("source_url") for r in rows if r.get("source_url")), None),
            "completeness_score": completeness,
            "validation_status": "completed",
            "uptake_ready": False,
        }

        for r in rows:
            series.append(
                {
                    "product": name,
                    "period": r["period"],
                    "period_type": r["period_type"],
                    "value": r.get("value_normalized_usd_millions"),
                    "validation_status": r.get("validation_status"),
                    "revenue_scope": r.get("revenue_scope"),
                    "scope_rank": scope_rank(r.get("revenue_scope")),
                    "geography": r.get("geography"),
                    "geography_normalized": None,
                    "formulation": r.get("formulation"),
                    "reported_as": None,
                    "series_identity": r.get("benchmark_identity"),
                    "series_selection": "selected",
                    "partial_period": False,
                    "source_url": r.get("source_url"),
                }
            )

        if peak_value and commercial_start:
            observations = [
                SalesObservation(
                    id=r["gold_id"], period=r["period"], value=r["value_normalized_usd_millions"],
                    currency="USD", geography=r.get("geography") or "", revenue_scope=r.get("revenue_scope") or "",
                    period_type="quarterly", period_basis=r.get("period_basis") or "calendar",
                    period_end=quarter_end(r["period"]),
                )
                for r in rows
                if r["period_type"] == "quarterly" and r.get("value_normalized_usd_millions") is not None
            ]
            chosen = SelectedPeak(
                estimate_type="observed", value=peak_value, currency=peak.get("currency"),
                geography=peak.get("geography"), revenue_scope=peak.get("revenue_scope"),
                as_of_date=date.today(), selection_method=peak.get("selection_method"),
                input_ids=peak.get("input_ids") or [],
            )
            for point in calculate_revenue_uptake(
                observations=observations, selected_peak=chosen, launch_date=quarter_start(commercial_start)
            ):
                if point.value is None:
                    continue
                product["uptake_ready"] = True
                launch_series.append(
                    {
                        "product": name,
                        "indication_id": None,
                        "period": point.period,
                        "months_since_launch": point.months_since_launch,
                        "metric_type": point.metric_type,
                        "value": round(point.value, 4),
                        "missing_reason": None,
                        "citation": {"input_ids": point.input_ids},
                    }
                )
        dash_products.append(product)

    keys = ("product_name", "therapeutic_area", "company", "approval_period", "competitive_intensity",
            "roa", "moa", "peak_sales_bucket", "indication_count")
    filter_options = {
        key: sorted({str(p[key]) for p in dash_products if p.get(key) not in (None, "")}) for key in keys
    }
    excluded = read_jsonl("excluded_products")
    (OUT / "products.json").write_text(json.dumps({"products": library, "total": len(library)}))
    (OUT / "dashboard.json").write_text(
        json.dumps({"products": dash_products, "series": series, "launch_series": launch_series,
                    "filter_options": filter_options, "analog_count": len(dash_products)})
    )
    (OUT / "about.json").write_text(
        json.dumps({"products": len(library), "quarterly_rows": len(quarterly), "annual_rows": len(annual),
                    "excluded_products": len(excluded),
                    "uptake_products": sum(p["uptake_ready"] for p in dash_products)})
    )
    print(f"wrote {len(library)} products, {len(series)} series points, "
          f"{len(launch_series)} launch points to {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
