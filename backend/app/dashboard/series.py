from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session, joinedload

from app.db.models import (
    CanonicalProductORM,
    CompetitiveSnapshotORM,
    DrugJobORM,
    MoAComponentORM,
    PeakSalesEstimateORM,
    ProductIndicationORM,
    UptakeMetricORM,
)
from app.observability import dedupe_jobs_by_analog, normalize_analog_key


def _unique_sorted(values: list[Any]) -> list[str]:
    by_key: dict[str, str] = {}
    for value in values:
        text = str(value or "").strip()
        if text:
            by_key.setdefault(text.casefold(), text)
    return sorted(by_key.values(), key=str.casefold)


def _approval_period(value: Any) -> str | None:
    if not value:
        return None
    year = value.year if hasattr(value, "year") else int(str(value)[:4])
    start = (year // 5) * 5
    return f"{start}-{start + 4}"


def _peak_bucket(value: float | None) -> str | None:
    if value is None:
        return None
    if value < 500:
        return "<$500M"
    if value <= 1000:
        return "$500M-$1B"
    return ">$1B"


def _selected_profile(job: DrugJobORM) -> dict[str, str | None]:
    status_rank = {"confirmed": 0, "auto_pass": 1, "needs_review": 2, "pending": 3}
    ordered = sorted(
        job.profile_fields,
        key=lambda row: (status_rank.get(row.validation_status, 4), row.id),
    )
    values: dict[str, str | None] = {}
    for row in ordered:
        values.setdefault(row.field, row.value)
    return values


def build_dashboard_preview(db: Session, run_id: str | None = None) -> dict[str, Any]:
    query = db.query(DrugJobORM).options(
        joinedload(DrugJobORM.profile_fields),
        joinedload(DrugJobORM.datapoints),
    )
    if run_id:
        query = query.filter_by(run_id=run_id)
    jobs = dedupe_jobs_by_analog(query.all())
    canonical_by_name = {
        normalize_analog_key(product.canonical_name): product
        for product in db.query(CanonicalProductORM).all()
    }

    products: list[dict[str, Any]] = []
    series: list[dict[str, Any]] = []
    launch_series: list[dict[str, Any]] = []
    for job in jobs:
        fields = _selected_profile(job)
        canonical = canonical_by_name.get(normalize_analog_key(job.drug_name))
        indications = (
            db.query(ProductIndicationORM).filter_by(product_id=canonical.id).all()
            if canonical
            else []
        )
        mechanisms = (
            db.query(MoAComponentORM).filter_by(product_id=canonical.id).all()
            if canonical
            else []
        )
        peak = (
            db.query(PeakSalesEstimateORM)
            .filter_by(product_id=canonical.id, selected=True)
            .order_by(PeakSalesEstimateORM.as_of_date.desc())
            .first()
            if canonical
            else None
        )
        indication_ids = [item.id for item in indications]
        snapshots = (
            db.query(CompetitiveSnapshotORM)
            .filter(CompetitiveSnapshotORM.indication_id.in_(indication_ids))
            .all()
            if indication_ids
            else []
        )
        category_rank = {"high": 3, "medium": 2, "low": 1}
        competition = max(
            snapshots,
            key=lambda item: (category_rank.get(item.category, 0), item.as_of_date),
            default=None,
        )
        selected_peak = (
            {
                "id": peak.id,
                "type": peak.estimate_type,
                "value": peak.value,
                "currency": peak.currency,
                "geography": peak.geography,
                "revenue_scope": peak.revenue_scope,
                "as_of_date": peak.as_of_date.isoformat(),
                "selection_reason": peak.selection_reason,
                "input_ids": peak.input_ids_json,
            }
            if peak
            else None
        )
        moa = "; ".join(sorted({item.moa_term for item in mechanisms})) or fields.get("moa")
        approved_lots = sorted({item.approved_lot for item in indications})
        approval_date = canonical.initial_approval_date if canonical else fields.get("fda_approval_date")
        product = {
            "job_id": job.id,
            "canonical_product_id": canonical.id if canonical else None,
            "product_name": canonical.canonical_name if canonical else job.drug_name,
            "therapeutic_area": (
                "; ".join(sorted({item.therapeutic_area for item in indications if item.therapeutic_area}))
                or fields.get("therapeutic_area")
            ),
            "manufacturer": (
                canonical.current_commercial_owner if canonical else None
            ) or job.manufacturer or fields.get("manufacturer"),
            "company": (
                canonical.current_commercial_owner if canonical else None
            ) or job.manufacturer or fields.get("manufacturer"),
            "fda_approval_date": (
                approval_date.isoformat() if hasattr(approval_date, "isoformat") else approval_date
            ),
            "approval_period": _approval_period(approval_date),
            "approved_indications": "; ".join(item.disease for item in indications)
            or fields.get("indication")
            or job.indication,
            "indication_count": len(indications) if indications else (1 if job.indication else 0),
            "indications": [
                {
                    "id": item.id,
                    "disease": item.disease,
                    "setting": item.setting,
                    "population": item.population,
                    "biomarker": item.biomarker,
                    "approved_lot": item.approved_lot,
                    "approval_date": item.approval_date.isoformat() if item.approval_date else None,
                    "launch_anchor_type": item.launch_anchor_type,
                }
                for item in indications
            ],
            "moa": moa or None,
            "pharmacologic_class": fields.get("pharmacologic_class"),
            "roa": fields.get("roa"),
            "treatment_type": fields.get("treatment_type"),
            "approved_lot": "; ".join(approved_lots) or fields.get("approved_lot"),
            "competitive_intensity": competition.category if competition else None,
            "competitive_snapshot": (
                {
                    "raw_score": competition.raw_score,
                    "formula_version": competition.formula_version,
                    "cohort_size": competition.cohort_size,
                    "low_coverage": competition.low_coverage,
                    "peer_ids": competition.peer_ids_json,
                }
                if competition
                else None
            ),
            "selected_peak": selected_peak,
            "estimated_peak_revenue": peak.value if peak else fields.get("estimated_peak_revenue"),
            "peak_sales_bucket": _peak_bucket(peak.value if peak else None),
            "peak_type": peak.estimate_type if peak else None,
            "reached_peak_yet": fields.get("reached_peak_yet"),
            "time_to_peak": fields.get("time_to_peak"),
            "source_link": next((item.source_url for item in job.datapoints if item.source_url), None),
            "completeness_score": job.completeness_pct,
            "validation_status": job.status,
            "uptake_ready": False,
        }
        if indication_ids:
            uptake_rows = (
                db.query(UptakeMetricORM)
                .filter(UptakeMetricORM.indication_id.in_(indication_ids))
                .order_by(UptakeMetricORM.months_since_launch)
                .all()
            )
            product["uptake_ready"] = any(item.value is not None for item in uptake_rows)
            for item in uptake_rows:
                launch_series.append(
                    {
                        "product": product["product_name"],
                        "indication_id": item.indication_id,
                        "period": item.period,
                        "months_since_launch": item.months_since_launch,
                        "metric_type": item.metric_type,
                        "value": item.value,
                        "missing_reason": item.missing_reason,
                        "citation": {"input_ids": item.input_ids_json},
                    }
                )
        products.append(product)
        for datapoint in job.datapoints:
            series.append(
                {
                    "product": product["product_name"],
                    "period": datapoint.period,
                    "period_type": datapoint.period_type,
                    "value": datapoint.value_normalized_usd_millions,
                    "validation_status": datapoint.validation_status,
                    "source_url": datapoint.source_url,
                    "source_quote": datapoint.source_quote,
                    "citation": datapoint.citation_json,
                    "issue_flags": datapoint.issue_flags,
                    "reviewer_notes": datapoint.reviewer_notes,
                }
            )

    filter_keys = [
        "product_name",
        "therapeutic_area",
        "company",
        "approval_period",
        "competitive_intensity",
        "roa",
        "moa",
        "peak_sales_bucket",
        "indication_count",
        "validation_status",
    ]
    filter_options = {
        key: _unique_sorted([product.get(key) for product in products])
        for key in filter_keys
    }
    peak_products = [product for product in products if product["selected_peak"]]
    return {
        "products": products,
        "series": series,
        "launch_series": launch_series,
        "filter_options": filter_options,
        "analog_count": len(products),
        "kpis": {
            "products_tracked": len(products),
            "companies_represented": len({product["company"] for product in products if product["company"]}),
            "aggregate_selected_peak": {
                "value": sum(product["selected_peak"]["value"] for product in peak_products),
                "currency": "USD",
                "covered_products": len(peak_products),
                "total_products": len(products),
            },
            "uptake_ready_products": sum(bool(product["uptake_ready"]) for product in products),
        },
    }

