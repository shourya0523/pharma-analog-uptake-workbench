"""Product-centric reads and reviewer writes.

The pipeline stores everything against the job that produced it, which answers
"what did this run find" but not "what do we know about this product". These
routes read the same rows the other way round: a product's current state is its
most recent job, and its history is every job that ever named it.
"""

from __future__ import annotations

# ruff: noqa: B008, BLE001
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
from app.domain.models import ValidationStatus, new_id

router = APIRouter(tags=["products"])

CADENCES = {"quarterly", "one_off"}


def _jobs_for(db: Session, product_id: str) -> list[DrugJobORM]:
    """Every job for a product, newest first."""

    return (
        db.query(DrugJobORM)
        .filter(DrugJobORM.product_id == product_id)
        .order_by(DrugJobORM.created_at.desc())
        .all()
    )


def _latest_job(db: Session, product_id: str) -> DrugJobORM | None:
    jobs = _jobs_for(db, product_id)
    return jobs[0] if jobs else None


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
    """The Library: one row per canonical product, independent of any run."""

    db = SessionLocal()
    try:
        query = db.query(CanonicalProductORM)
        if cadence:
            query = query.filter(CanonicalProductORM.cadence == cadence)
        products = query.order_by(CanonicalProductORM.canonical_name).all()

        rows: list[dict[str, Any]] = []
        for product in products:
            job = _latest_job(db, product.id)
            flagged, missing = _open_queue_counts(db, job.id) if job else (0, 0)
            quarters = (
                db.query(DatapointORM).filter(DatapointORM.job_id == job.id).count()
                if job
                else 0
            )
            formulation = _formulation(db, product.id)
            row = {
                "id": product.id,
                "name": product.canonical_name,
                "generic": ", ".join(product.active_moieties_json or []) or None,
                "company": product.current_commercial_owner or product.regulatory_sponsor,
                "indication": _indication_text(db, product.id),
                "moa": _moa_text(db, product.id),
                "roa": formulation.route_category if formulation else None,
                "cadence": product.cadence,
                "completeness_pct": job.completeness_pct if job else 0.0,
                "quarters": quarters,
                "flagged": flagged,
                "missing": missing,
                "last_job_id": job.id if job else None,
                "last_run_at": job.created_at.isoformat() if job else None,
                "last_run_status": job.status if job else None,
            }
            if q:
                needle = q.strip().lower()
                haystack = " ".join(
                    str(row[key] or "") for key in ("name", "generic", "company", "moa")
                ).lower()
                if needle not in haystack:
                    continue
            rows.append(row)
        return {"products": rows, "total": len(rows)}
    finally:
        db.close()


@router.get("/products/{product_id}")
def get_product(product_id: str) -> dict[str, Any]:
    """One product: its profile, its quarters, and the runs that touched it."""

    db = SessionLocal()
    try:
        product = db.get(CanonicalProductORM, product_id)
        if not product:
            raise HTTPException(404, "product not found")

        jobs = _jobs_for(db, product_id)
        latest = jobs[0] if jobs else None
        formulation = _formulation(db, product_id)
        indication = (
            db.query(ProductIndicationORM)
            .filter(ProductIndicationORM.product_id == product_id)
            .first()
        )
        moa = (
            db.query(MoAComponentORM)
            .filter(MoAComponentORM.product_id == product_id)
            .first()
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
            "id": product.id,
            "name": product.canonical_name,
            "generic": ", ".join(product.active_moieties_json or []) or None,
            "company": product.current_commercial_owner or product.regulatory_sponsor,
            "regulatory_sponsor": product.regulatory_sponsor,
            "application_number": product.application_number,
            "initial_approval_date": (
                product.initial_approval_date.isoformat()
                if product.initial_approval_date
                else None
            ),
            "cadence": product.cadence,
            "indication": indication.disease if indication else None,
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


@router.get("/review/queue")
def review_queue(
    product_id: str | None = Query(default=None),
    item_type: str | None = Query(default=None),
    reason: str | None = Query(default=None),
) -> dict[str, Any]:
    """Everything awaiting a person, across products.

    Two streams, because the pipeline produces two: a value the judge flagged,
    and a quarter it expected to find and could not.
    """

    db = SessionLocal()
    try:
        items: list[dict[str, Any]] = []
        product_names = {
            product.id: product.canonical_name
            for product in db.query(CanonicalProductORM).all()
        }

        if item_type in (None, "flagged"):
            task_query = (
                db.query(ValidationTaskORM, DatapointORM, DrugJobORM)
                .join(DatapointORM, DatapointORM.id == ValidationTaskORM.datapoint_id)
                .join(DrugJobORM, DrugJobORM.id == ValidationTaskORM.job_id)
                .filter(ValidationTaskORM.status == "open")
            )
            if product_id:
                task_query = task_query.filter(DrugJobORM.product_id == product_id)
            if reason:
                task_query = task_query.filter(ValidationTaskORM.reason == reason)
            for task, dp, job in task_query.all():
                items.append(
                    {
                        "id": task.id,
                        "type": "flagged",
                        "product_id": job.product_id,
                        "product": product_names.get(job.product_id) or job.drug_name,
                        "job_id": job.id,
                        "datapoint_id": dp.id,
                        "period": dp.period,
                        "reason": task.reason,
                        "confidence": task.confidence_score,
                        "value_normalized_usd_millions": dp.value_normalized_usd_millions,
                        "revenue_scope": dp.revenue_scope,
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
            if product_id:
                missing_query = missing_query.filter(DrugJobORM.product_id == product_id)
            for row, job in missing_query.all():
                items.append(
                    {
                        "id": row.id,
                        "type": "missing",
                        "product_id": job.product_id,
                        "product": product_names.get(job.product_id) or job.drug_name,
                        "job_id": job.id,
                        "period": row.period,
                        "reason": "not_disclosed"
                        if row.period == "product_revenue"
                        else "interior_gap",
                        "confidence": row.confidence_that_unavailable,
                        "reason_unresolved": row.reason_unresolved,
                        "sources_checked": row.sources_checked,
                        "recommended_next_step": row.recommended_next_step,
                        "reviewer_notes": row.reviewer_notes,
                    }
                )

        if reason and item_type == "missing":
            items = [item for item in items if item["reason"] == reason]

        items.sort(key=lambda item: (item["product"] or "", str(item["period"] or "")))
        return {
            "items": items,
            "total": len(items),
            "flagged": sum(1 for item in items if item["type"] == "flagged"),
            "missing": sum(1 for item in items if item["type"] == "missing"),
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

    if body.action not in {"enter_value", "not_disclosed", "re_queue"}:
        raise HTTPException(400, "action must be enter_value|not_disclosed|re_queue")

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
            row.resolution = "value_entered"
        elif body.action == "not_disclosed":
            row.resolution = "not_disclosed"
        else:
            row.resolution = "re_queued"

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
        db.commit()
        return {
            "id": row.id,
            "resolution": row.resolution,
            "datapoint_id": created_datapoint_id,
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
