"""Product-centric reads and reviewer writes.

The pipeline stores everything against the job that produced it, which answers
"what did this run find" but not "what do we know about this product". These
routes read the same rows the other way round: a product's current state is its
most recent job, and its history is every job that ever named it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import (
    CanonicalProductORM,
    DatapointORM,
    DrugJobORM,
    DrugProfileFieldORM,
    MoAComponentORM,
    ProductFormulationORM,
    ProductIndicationORM,
    ReviewEventORM,
    SessionLocal,
    UnresolvedQuarterORM,
    ValidationTaskORM,
)
from app.domain.models import (
    NO_FILER_OF_RECORD,
    PUBLISHED_STATUS_VALUES,
    REPORTED_WITH_ANOTHER_PRODUCT,
    Cadence,
    PeriodType,
    UnresolvedResolution,
    ValidationStatus,
    new_id,
)
from app.observability import normalize_analog_key
from app.quality.completeness import (
    names_a_quarter,
    quarter_labels,
    refresh_completeness,
)
from app.validation.sampling import REASON_HELP as FLAGGED_REASON_HELP

router = APIRouter(tags=["products"])

# Taken from the enums rather than restated, so adding a cadence or a
# resolution does not need this module edited to accept it.
CADENCES = {cadence.value for cadence in Cadence}
RESOLUTION_BY_ACTION = {
    "enter_value": UnresolvedResolution.VALUE_ENTERED.value,
    "not_disclosed": UnresolvedResolution.NOT_DISCLOSED.value,
    "re_queue": UnresolvedResolution.RE_QUEUED.value,
}

# A quarter the pipeline could not fill is one of two things, told apart by
# the period the completeness stage recorded: the whole product, or one
# quarter between quarters it did fill.
WHOLE_PRODUCT_PERIOD = "product_revenue"
MISSING_REASON_HELP: dict[str, str] = {
    "not_disclosed": "No product-level figure was found for this product at all.",
    "interior_gap": "A quarter between quarters that were extracted, so a value is expected.",
    REPORTED_WITH_ANOTHER_PRODUCT: (
        "The issuer reports this product only together with another one. The pair's "
        "figure is published for this quarter; a figure for this product alone is not "
        "something anybody discloses."
    ),
    NO_FILER_OF_RECORD: (
        "No filing of the named issuer covers this quarter, and a search for who "
        "reported the product then found no figure. Someone else may have been the filer."
    ),
}
REASON_HELP: dict[str, str] = {**FLAGGED_REASON_HELP, **MISSING_REASON_HELP}


def _missing_reason(period: str | None, reason_unresolved: str | None = None) -> str:
    for code in (NO_FILER_OF_RECORD, REPORTED_WITH_ANOTHER_PRODUCT):
        if (reason_unresolved or "").startswith(f"[{code}]"):
            return code
    return "not_disclosed" if period == WHOLE_PRODUCT_PERIOD else "interior_gap"


# A product exists here because the pipeline ran for it. Its identity is the
# canonical product the job resolved, or the one whose name the job was given,
# or - where no profile was ever built - the name itself, keyed so the
# Library, the detail page and the queue all agree on which rows are one
# product. The prefix marks a key that is a name, not a row id.
NAME_KEY_PREFIX = "name:"



class _Identities:
    """Which product each job is, resolved once per request."""

    def __init__(self, db: Session) -> None:
        self.by_id: dict[str, CanonicalProductORM] = {
            product.id: product for product in db.query(CanonicalProductORM).all()
        }
        self.by_name: dict[str, CanonicalProductORM] = {
            normalize_analog_key(product.canonical_name): product
            for product in self.by_id.values()
        }

    def key(self, job: DrugJobORM) -> str:
        if job.product_id:
            return job.product_id
        canonical = self.by_name.get(normalize_analog_key(job.drug_name))
        if canonical:
            return canonical.id
        return NAME_KEY_PREFIX + normalize_analog_key(job.drug_name)

    def canonical(self, key: str) -> CanonicalProductORM | None:
        return self.by_id.get(key)

    def name(self, key: str, job: DrugJobORM) -> str:
        canonical = self.canonical(key)
        return canonical.canonical_name if canonical else job.drug_name


def _jobs_by_product(db: Session, identities: _Identities) -> dict[str, list[DrugJobORM]]:
    """Every job, grouped by the product it is, each group newest first."""

    grouped: dict[str, list[DrugJobORM]] = {}
    for job in db.query(DrugJobORM).order_by(DrugJobORM.created_at.desc()).all():
        grouped.setdefault(identities.key(job), []).append(job)
    return grouped


def _jobs_for(db: Session, product_key: str) -> list[DrugJobORM]:
    """Every job for a product, newest first."""

    return _jobs_by_product(db, _Identities(db)).get(product_key, [])


def _published_quarters(db: Session, job_id: str) -> int:
    """Distinct quarters with a figure the pipeline stands behind.

    Counted over quarterly rows only. Counting every period type under a
    heading that says "qtrs" let an annual figure, and an annual figure
    labelled with a quarter, each be read as a quarter of coverage.
    """

    periods = (
        db.query(DatapointORM.period)
        .filter(
            DatapointORM.job_id == job_id,
            DatapointORM.period_type == PeriodType.QUARTERLY.value,
            DatapointORM.validation_status.in_(PUBLISHED_STATUS_VALUES),
        )
        .all()
    )
    return len(quarter_labels(period for (period,) in periods))


def _open_queue_counts(db: Session, job_id: str) -> tuple[int, int]:
    """(flagged, missing) still awaiting a person for one job."""

    flagged = (
        db.query(ValidationTaskORM)
        .filter(ValidationTaskORM.job_id == job_id, ValidationTaskORM.status == "open")
        .count()
    )
    missing = (
        db.query(UnresolvedQuarterORM)
        .filter(
            UnresolvedQuarterORM.job_id == job_id,
            UnresolvedQuarterORM.resolution.is_(None),
        )
        .count()
    )
    return flagged, missing


def _indication_text(db: Session, product_id: str) -> str | None:
    indication = (
        db.query(ProductIndicationORM)
        .filter(ProductIndicationORM.product_id == product_id)
        .first()
    )
    return indication.disease if indication else None


def _moa_text(db: Session, product_id: str) -> str | None:
    moa = (
        db.query(MoAComponentORM).filter(MoAComponentORM.product_id == product_id).first()
    )
    return moa.moa_term if moa else None


def _formulation(db: Session, product_id: str) -> ProductFormulationORM | None:
    return (
        db.query(ProductFormulationORM)
        .filter(ProductFormulationORM.product_id == product_id)
        .first()
    )


@router.get("/products")
def list_products(
    cadence: str | None = Query(default=None),
    q: str | None = Query(default=None),
) -> dict[str, Any]:
    """The Library: one row per product the pipeline has run for.

    A product is here because a job produced figures for it; the canonical
    profile, where one was built, is attached to that row rather than being
    the condition for it. A job that never resolved an identity is listed
    under the name it was given.
    """

    db = SessionLocal()
    try:
        identities = _Identities(db)
        rows: list[dict[str, Any]] = []
        for key, jobs in _jobs_by_product(db, identities).items():
            job = jobs[0]
            product = identities.canonical(key)
            if cadence and (product.cadence if product else None) != cadence:
                continue
            flagged, missing = _open_queue_counts(db, job.id)
            formulation = _formulation(db, product.id) if product else None
            row = {
                "id": key,
                "name": identities.name(key, job),
                "generic": (
                    (", ".join(product.active_moieties_json or []) or None)
                    if product
                    else job.generic_name
                ),
                "company": (
                    (product.current_commercial_owner or product.regulatory_sponsor)
                    if product
                    else None
                ) or job.manufacturer,
                "indication": (_indication_text(db, product.id) if product else None)
                or job.indication,
                "moa": _moa_text(db, product.id) if product else None,
                "roa": formulation.route_category if formulation else None,
                "cadence": product.cadence if product else None,
                "has_profile": product is not None,
                "completeness_pct": job.completeness_pct,
                "quarters": _published_quarters(db, job.id),
                "flagged": flagged,
                "missing": missing,
                "last_job_id": job.id,
                "last_run_at": job.created_at.isoformat(),
                "last_run_status": job.status,
            }
            if q:
                needle = q.strip().lower()
                haystack = " ".join(
                    str(row[key] or "") for key in ("name", "generic", "company", "moa")
                ).lower()
                if needle not in haystack:
                    continue
            rows.append(row)
        rows.sort(key=lambda row: (row["name"] or "").casefold())
        return {"products": rows, "total": len(rows)}
    finally:
        db.close()


@router.get("/products/{product_id}")
def get_product(product_id: str) -> dict[str, Any]:
    """One product: its profile, its quarters, and the runs that touched it."""

    db = SessionLocal()
    try:
        identities = _Identities(db)
        product = identities.canonical(product_id)
        jobs = _jobs_by_product(db, identities).get(product_id, [])
        if not product and not jobs:
            raise HTTPException(404, "product not found")

        latest = jobs[0] if jobs else None
        formulation = _formulation(db, product_id) if product else None
        indication = (
            db.query(ProductIndicationORM)
            .filter(ProductIndicationORM.product_id == product_id)
            .first()
            if product
            else None
        )
        moa = (
            db.query(MoAComponentORM)
            .filter(MoAComponentORM.product_id == product_id)
            .first()
            if product
            else None
        )

        # Profile fields live on the job that extracted them; the product's
        # profile is the newest job's, which is what a reviewer is looking at.
        profile = (
            db.query(DrugProfileFieldORM)
            .filter(DrugProfileFieldORM.job_id == latest.id)
            .all()
            if latest
            else []
        )
        datapoints = (
            db.query(DatapointORM)
            .filter(DatapointORM.job_id == latest.id)
            .order_by(DatapointORM.period.desc())
            .all()
            if latest
            else []
        )
        open_tasks = (
            {
                task.datapoint_id: task
                for task in db.query(ValidationTaskORM)
                .filter(
                    ValidationTaskORM.job_id == latest.id,
                    ValidationTaskORM.status == "open",
                )
                .all()
            }
            if latest
            else {}
        )
        unresolved = (
            db.query(UnresolvedQuarterORM)
            .filter(
                UnresolvedQuarterORM.job_id == latest.id,
                UnresolvedQuarterORM.resolution.is_(None),
            )
            .all()
            if latest
            else []
        )

        return {
            "id": product_id,
            "name": product.canonical_name if product else latest.drug_name,
            "generic": (
                (", ".join(product.active_moieties_json or []) or None)
                if product
                else latest.generic_name
            ),
            "company": (
                (product.current_commercial_owner or product.regulatory_sponsor)
                if product
                else None
            ) or (latest.manufacturer if latest else None),
            "regulatory_sponsor": product.regulatory_sponsor if product else None,
            "application_number": product.application_number if product else None,
            "initial_approval_date": (
                product.initial_approval_date.isoformat()
                if product and product.initial_approval_date
                else None
            ),
            "cadence": product.cadence if product else None,
            "has_profile": product is not None,
            "indication": (indication.disease if indication else None)
            or (latest.indication if latest else None),
            "therapeutic_area": indication.therapeutic_area if indication else None,
            "approved_lot": indication.approved_lot if indication else None,
            "approved_lot_quote": indication.approved_lot_quote if indication else None,
            "moa": moa.moa_term if moa else None,
            "epc": (moa.fda_epc_terms_json or []) if moa else [],
            "dosage_form": formulation.dosage_form if formulation else None,
            "route": formulation.route_category if formulation else None,
            "completeness_pct": latest.completeness_pct if latest else 0.0,
            "latest_job_id": latest.id if latest else None,
            "profile": [
                {
                    "id": field.id,
                    "field": field.field,
                    "value": field.value,
                    "citation": field.citation_json,
                    "validation_status": field.validation_status,
                }
                for field in profile
            ],
            "quarters": [
                {
                    "id": dp.id,
                    "period": dp.period,
                    "value_normalized_usd_millions": dp.value_normalized_usd_millions,
                    "currency": dp.currency,
                    "revenue_scope": dp.revenue_scope,
                    "reported_as": dp.reported_as,
                    "source_url": dp.source_url,
                    "source_quote": dp.source_quote,
                    "extraction_method": dp.extraction_method,
                    "confidence_score": dp.confidence_score,
                    "validation_status": dp.validation_status,
                    "in_queue": dp.id in open_tasks,
                    "queue_reason": (
                        open_tasks[dp.id].reason if dp.id in open_tasks else None
                    ),
                }
                for dp in datapoints
            ],
            "missing_quarters": [
                {
                    "id": row.id,
                    "period": row.period,
                    "reason_unresolved": row.reason_unresolved,
                    "recommended_next_step": row.recommended_next_step,
                    "confidence_that_unavailable": row.confidence_that_unavailable,
                }
                for row in unresolved
            ],
            "timeline": [
                {
                    "job_id": job.id,
                    "run_id": job.run_id,
                    "created_at": job.created_at.isoformat(),
                    "status": job.status,
                    "current_step": job.current_step,
                    "completeness_pct": job.completeness_pct,
                    "auto_pass_count": job.auto_pass_count,
                    "needs_review_count": job.needs_review_count,
                    "unresolved_count": job.unresolved_count,
                    "error": job.error,
                }
                for job in jobs
            ],
        }
    finally:
        db.close()


class CadencePatch(BaseModel):
    cadence: str


@router.patch("/products/{product_id}")
def patch_product(product_id: str, body: CadencePatch) -> dict[str, Any]:
    if body.cadence not in CADENCES:
        raise HTTPException(400, f"cadence must be one of {sorted(CADENCES)}")
    db = SessionLocal()
    try:
        product = db.get(CanonicalProductORM, product_id)
        if not product:
            raise HTTPException(404, "product not found")
        product.cadence = body.cadence
        db.commit()
        return {"id": product.id, "cadence": product.cadence}
    finally:
        db.close()


class BulkCadenceRequest(BaseModel):
    product_ids: list[str]
    cadence: str


@router.post("/products/cadence")
def bulk_cadence(body: BulkCadenceRequest) -> dict[str, Any]:
    """Set cadence on a selection, which is how the Library's bulk bar works."""

    if body.cadence not in CADENCES:
        raise HTTPException(400, f"cadence must be one of {sorted(CADENCES)}")
    if not body.product_ids:
        raise HTTPException(400, "product_ids is required")
    db = SessionLocal()
    try:
        updated = (
            db.query(CanonicalProductORM)
            .filter(CanonicalProductORM.id.in_(body.product_ids))
            .update({CanonicalProductORM.cadence: body.cadence}, synchronize_session=False)
        )
        db.commit()
        return {"updated": updated, "cadence": body.cadence}
    finally:
        db.close()


QUEUE_PAGE_DEFAULT = 50
QUEUE_PAGE_MAX = 500


def _group_key(item: dict[str, Any]) -> tuple[str, str]:
    """One question per product and quarter.

    Several contested figures for one quarter are one thing for a reviewer to
    decide, and the queue says so once, with the figures beneath it.
    """
    return (item["product_id"], str(item["period"] or ""))


@router.get("/review/queue")
def review_queue(
    product_id: str | None = Query(default=None),
    item_type: str | None = Query(default=None),
    reason: str | None = Query(default=None),
    limit: int = Query(default=QUEUE_PAGE_DEFAULT),
    offset: int = Query(default=0),
) -> dict[str, Any]:
    """Everything awaiting a person, across products, a page at a time.

    Two streams, because the pipeline produces two: a value the judge flagged,
    and a quarter it expected to find and could not. Items are grouped by
    product and quarter, and the page is a page of groups, so a quarter with
    many contested figures is one row rather than one per figure. The counts
    and the filter options describe the whole set, not the page.
    """
    limit = max(1, min(limit, QUEUE_PAGE_MAX))
    offset = max(0, offset)

    db = SessionLocal()
    try:
        items: list[dict[str, Any]] = []
        identities = _Identities(db)

        if item_type in (None, "flagged"):
            task_query = (
                db.query(ValidationTaskORM, DatapointORM, DrugJobORM)
                .join(DatapointORM, DatapointORM.id == ValidationTaskORM.datapoint_id)
                .join(DrugJobORM, DrugJobORM.id == ValidationTaskORM.job_id)
                .filter(ValidationTaskORM.status == "open")
            )
            if reason:
                task_query = task_query.filter(ValidationTaskORM.reason == reason)
            for task, dp, job in task_query.all():
                key = identities.key(job)
                if product_id and key != product_id:
                    continue
                items.append(
                    {
                        "id": task.id,
                        "type": "flagged",
                        "product_id": key,
                        "product": identities.name(key, job),
                        "job_id": job.id,
                        "datapoint_id": dp.id,
                        "period": dp.period,
                        "reason": task.reason,
                        "confidence": task.confidence_score,
                        "value_normalized_usd_millions": dp.value_normalized_usd_millions,
                        "revenue_scope": dp.revenue_scope,
                        "reported_as": dp.reported_as,
                        "source_url": dp.source_url,
                        "source_quote": dp.source_quote,
                        "extraction_method": dp.extraction_method,
                        "validation_status": dp.validation_status,
                        "issue_flags": dp.issue_flags,
                        "reviewer_notes": task.reviewer_notes,
                    }
                )

        if item_type in (None, "missing"):
            missing_query = (
                db.query(UnresolvedQuarterORM, DrugJobORM)
                .join(DrugJobORM, DrugJobORM.id == UnresolvedQuarterORM.job_id)
                .filter(UnresolvedQuarterORM.resolution.is_(None))
            )
            for row, job in missing_query.all():
                key = identities.key(job)
                if product_id and key != product_id:
                    continue
                items.append(
                    {
                        "id": row.id,
                        "type": "missing",
                        "product_id": key,
                        "product": identities.name(key, job),
                        "job_id": job.id,
                        "period": row.period,
                        "reason": _missing_reason(row.period, row.reason_unresolved),
                        "confidence": row.confidence_that_unavailable,
                        "reason_unresolved": row.reason_unresolved,
                        "sources_checked": row.sources_checked,
                        "recommended_next_step": row.recommended_next_step,
                        "reviewer_notes": row.reviewer_notes,
                    }
                )

        # The reason filter is applied after both streams are gathered, so the
        # options offered are the reasons this product's and type's items
        # carry, whichever one is selected.
        reasons: dict[str, int] = {}
        for item in items:
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
        if reason:
            items = [item for item in items if item["reason"] == reason]

        products: dict[str, str] = {}
        for item in items:
            if item["product_id"]:
                products.setdefault(item["product_id"], item["product"])

        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for item in items:
            group = grouped.get(_group_key(item))
            if group is None:
                group = grouped[_group_key(item)] = {
                    "product_id": item["product_id"],
                    "product": item["product"],
                    "job_id": item["job_id"],
                    "period": item["period"],
                    "types": [],
                    "reasons": [],
                    "items": [],
                }
            if item["type"] not in group["types"]:
                group["types"].append(item["type"])
            if item["reason"] not in group["reasons"]:
                group["reasons"].append(item["reason"])
            group["items"].append(item)
        groups = sorted(
            grouped.values(),
            key=lambda group: (group["product"] or "", str(group["period"] or "")),
        )
        return {
            "groups": groups[offset : offset + limit],
            "groups_total": len(groups),
            "limit": limit,
            "offset": offset,
            "total": len(items),
            "flagged": sum(1 for item in items if item["type"] == "flagged"),
            "missing": sum(1 for item in items if item["type"] == "missing"),
            "reasons": dict(sorted(reasons.items())),
            "products": [
                {"id": key, "name": name}
                for key, name in sorted(products.items(), key=lambda kv: kv[1] or "")
            ],
            "reason_help": REASON_HELP,
        }
    finally:
        db.close()


class UnresolvedAction(BaseModel):
    action: str
    value_normalized_usd_millions: float | None = None
    revenue_scope: str | None = None
    source_url: str | None = None
    source_quote: str | None = None
    reviewer_notes: str | None = None


@router.post("/unresolved-quarters/{unresolved_id}/actions")
def resolve_unresolved_quarter(
    unresolved_id: str, body: UnresolvedAction
) -> dict[str, Any]:
    """Close out a quarter the pipeline could not fill.

    ``enter_value`` demands a citation for the same reason the export gate does:
    a confirmed number without a source cannot be published, so one is refused
    here rather than written and rejected later.
    """

    if body.action not in RESOLUTION_BY_ACTION:
        raise HTTPException(
            400, f"action must be one of {'|'.join(sorted(RESOLUTION_BY_ACTION))}"
        )

    db = SessionLocal()
    try:
        row = db.get(UnresolvedQuarterORM, unresolved_id)
        if not row:
            raise HTTPException(404, "unresolved quarter not found")

        created_datapoint_id: str | None = None
        if body.action == "enter_value":
            if body.value_normalized_usd_millions is None:
                raise HTTPException(400, "value_normalized_usd_millions is required")
            if not (body.source_url or "").strip():
                raise HTTPException(
                    400, "source_url is required: a confirmed value must carry a citation"
                )
            datapoint = DatapointORM(
                id=new_id(),
                job_id=row.job_id,
                period=row.period,
                # Stated rather than left to the column default of "unknown".
                # The completeness count reads period_type to decide what is a
                # quarter, so a value entered for a gap that did not say so
                # closed the gap without filling it.
                period_type=(
                    PeriodType.QUARTERLY.value
                    if names_a_quarter(row.period)
                    else PeriodType.UNKNOWN.value
                ),
                value_normalized_usd_millions=body.value_normalized_usd_millions,
                currency="USD",
                unit="millions",
                revenue_scope=body.revenue_scope or "Unknown",
                source_url=body.source_url.strip(),
                source_quote=(body.source_quote or "").strip()
                or "Entered by reviewer from the cited source",
                extraction_method="reviewer",
                confidence_score=1.0,
                validation_status=ValidationStatus.CONFIRMED.value,
                reviewer_notes=body.reviewer_notes,
                citation_json={
                    "source_url": body.source_url.strip(),
                    "source_quote": body.source_quote,
                    "entered_at": datetime.utcnow().isoformat(),
                },
            )
            db.add(datapoint)
            created_datapoint_id = datapoint.id
        row.resolution = RESOLUTION_BY_ACTION[body.action]

        if body.reviewer_notes:
            row.reviewer_notes = body.reviewer_notes

        db.add(
            ReviewEventORM(
                id=new_id(),
                job_id=row.job_id,
                datapoint_id=created_datapoint_id,
                action=f"unresolved:{body.action}",
                before_json={"period": row.period, "resolution": None},
                after_json={"period": row.period, "resolution": row.resolution},
                notes=body.reviewer_notes,
            )
        )
        counted = refresh_completeness(db, db.get(DrugJobORM, row.job_id))
        db.commit()
        return {
            "id": row.id,
            "resolution": row.resolution,
            "datapoint_id": created_datapoint_id,
            "completeness_pct": counted.pct,
            "unresolved_count": counted.gaps,
        }
    finally:
        db.close()


class ProfileFieldPatch(BaseModel):
    value: str
    source_url: str
    reviewer_notes: str | None = None


@router.patch("/profile-fields/{field_id}")
def patch_profile_field(field_id: str, body: ProfileFieldPatch) -> dict[str, Any]:
    """Edit a product-level profile field, with the citation it needs to publish."""

    if not body.source_url.strip():
        raise HTTPException(
            400, "source_url is required: a confirmed field must carry a citation"
        )
    db = SessionLocal()
    try:
        field = db.get(DrugProfileFieldORM, field_id)
        if not field:
            raise HTTPException(404, "profile field not found")
        before = {"value": field.value, "validation_status": field.validation_status}
        field.value = body.value
        field.validation_status = ValidationStatus.CONFIRMED.value
        field.citation_json = {
            "source_url": body.source_url.strip(),
            "source_quote": body.reviewer_notes,
            "extraction_method": "reviewer",
            "edited_at": datetime.utcnow().isoformat(),
        }
        db.add(
            ReviewEventORM(
                id=new_id(),
                job_id=field.job_id,
                datapoint_id=None,
                action="profile_field_edit",
                before_json=before,
                after_json={
                    "field": field.field,
                    "value": field.value,
                    "validation_status": field.validation_status,
                },
                notes=body.reviewer_notes,
            )
        )
        db.commit()
        return {
            "id": field.id,
            "field": field.field,
            "value": field.value,
            "validation_status": field.validation_status,
            "citation": field.citation_json,
        }
    finally:
        db.close()
