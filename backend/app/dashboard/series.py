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
from app.domain.models import (
    FINISHED_JOB_STATUS_VALUES,
    PUBLISHED_STATUS_VALUES,
    PeriodType,
    RevenueScope,
    holds_the_series_figure,
)
from app.observability import dedupe_jobs_by_analog, normalize_analog_key
from app.parsing.labels import FLAG_PARTIAL
from app.pipeline.series_identity import (
    _scope_key,
    commercial_start_quarter,
    quarter_containing,
)

# Where each scope sits in the order the model declares them. Read from the
# enum so that adding a scope does not need a second list edited here.
_SCOPE_ORDER = {scope.value: index for index, scope in enumerate(RevenueScope)}


def scope_rank(revenue_scope: str | None) -> int:
    """Where a scope sits in the order a chart should prefer, widest first.

    The whole product comes first, and what counts as the whole product is
    `_scope_key`'s answer rather than a second one: a sentence saying
    Worldwide and a schedule's family line are one scope, and reconciliation
    already groups them as one. Everything narrower follows in the order
    `RevenueScope` declares. That order is not a claim about width - it is a
    stable one, so that two readings of a quarter at different scopes always
    resolve the same way rather than by whichever row was read last. A scope
    the enum does not know sorts behind every scope it does.
    """
    key = _scope_key(revenue_scope)
    if key == RevenueScope.PRODUCT_FAMILY.value:
        return 0
    return 1 + _SCOPE_ORDER.get(key, len(_SCOPE_ORDER))


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


# Which of a product's fields a reader narrows the library by. Not derivable:
# every field here is a string on the product record, and so are the source
# URL, the approval date and the free-text indication list, which are not
# things anyone filters by. It is a snapshot of the categorical fields the
# product record carries, and what makes it stale is a field added to that
# record that a reader would want to narrow by - or one renamed, which
# `_filter_keys` turns into an error rather than an empty menu.
_FILTER_KEYS = (
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
)


def _filter_keys(products: list[dict[str, Any]]) -> tuple[str, ...]:
    """The filter names, checked against the records they are meant to read.

    A name no product record carries would serve an empty menu that looks
    like a field nothing has a value for, so it is refused here instead.
    """
    if not products:
        return _FILTER_KEYS
    named = set().union(*(set(product) for product in products))
    unknown = sorted(set(_FILTER_KEYS) - named)
    if unknown:
        raise KeyError(f"filter keys name no field of the product record: {unknown}")
    return _FILTER_KEYS


def build_dashboard_preview(
    db: Session, run_id: str | None = None, *, include_held: bool = False
) -> dict[str, Any]:
    """The dashboard's products and series.

    ``include_held`` adds the datapoints the pipeline did not stand behind,
    each still carrying its status, for a viewer who asks to see them.
    """
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
        launch_quarter = quarter_containing(
            canonical.initial_approval_date if canonical else None
        )
        commercial_start = commercial_start_quarter(
            [
                (row.period, FLAG_PARTIAL not in set(row.issue_flags or []))
                for row in job.datapoints
                if row.period_type == PeriodType.QUARTERLY.value
                and row.validation_status in PUBLISHED_STATUS_VALUES
                and holds_the_series_figure(row.series_selection)
            ],
            launch_quarter=launch_quarter,
        )
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
            # Two quarters, not one. The launch quarter anchors the x-axis and
            # says nothing about what is citable; the commercial start says
            # where the series has a quarter of selling to plot. A reader who
            # mistakes either for the other reads a ramp that began somewhere
            # it did not.
            "launch_quarter": launch_quarter,
            "commercial_start_quarter": commercial_start,
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
        if job.status not in FINISHED_JOB_STATUS_VALUES:
            # A job that has not reached review holds rows reconciliation has
            # not seen - the same quarter twice, at the same scope, both
            # marked as passed - and a failed one stopped somewhere it did
            # not choose. Neither is a series; the product still appears,
            # with its status saying so.
            continue
        for datapoint in job.datapoints:
            if not include_held and (
                datapoint.validation_status not in PUBLISHED_STATUS_VALUES
                or not holds_the_series_figure(datapoint.series_selection)
            ):
                continue
            series.append(
                {
                    "product": product["product_name"],
                    "period": datapoint.period,
                    "period_type": datapoint.period_type,
                    "value": datapoint.value_normalized_usd_millions,
                    "validation_status": datapoint.validation_status,
                    # What the figure is a figure for. Without these the
                    # chart had no way to tell one quarter's worldwide
                    # figure from the same quarter's ex-U.S. one, and drew
                    # whichever row it read last.
                    "revenue_scope": datapoint.revenue_scope,
                    "scope_rank": scope_rank(datapoint.revenue_scope),
                    "geography": datapoint.geography,
                    "geography_normalized": datapoint.geography_normalized,
                    "formulation": datapoint.formulation,
                    "reported_as": datapoint.reported_as,
                    # Which series this point belongs to, and whether it is
                    # the figure that series holds for the quarter. Two
                    # points of one product can be figures for different
                    # things, and a line drawn through both is not a curve.
                    "series_identity": datapoint.series_identity,
                    "series_selection": datapoint.series_selection,
                    "partial_period": FLAG_PARTIAL in set(datapoint.issue_flags or []),
                    "source_url": datapoint.source_url,
                    "source_quote": datapoint.source_quote,
                    "citation": datapoint.citation_json,
                    "issue_flags": datapoint.issue_flags,
                    "reviewer_notes": datapoint.reviewer_notes,
                }
            )

    filter_options = {
        key: _unique_sorted([product.get(key) for product in products])
        for key in _filter_keys(products)
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
                # None, not 0, where nothing has a selected peak. A sum over
                # no products is arithmetically zero and reads as a measured
                # zero, which is the one thing it is not.
                "value": (
                    sum(product["selected_peak"]["value"] for product in peak_products)
                    if peak_products
                    else None
                ),
                "currency": "USD",
                "covered_products": len(peak_products),
                "total_products": len(products),
            },
            "uptake_ready_products": sum(bool(product["uptake_ready"]) for product in products),
        },
    }

