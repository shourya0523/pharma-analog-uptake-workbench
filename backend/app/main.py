from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db.models import (
    DatapointORM,
    DrugJobORM,
    ExtractionRunORM,
    ExportORM,
    ReviewEventORM,
    ValidationTaskORM,
    SessionLocal,
    init_db,
)
from app.domain.models import DrugInput, ExtractionOptions, JobStatus, ValidationStatus, new_id
from app.export.builder import ExportBuilder, TemplateMapper
from app.jobs.handler import handle_job
from app.jobs.queue import get_job_queue
from app.storage.filestore import get_file_store

settings = get_settings()
app = FastAPI(title=settings.app_name)
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
_cors_star = _cors_origins == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_star else _cors_origins,
    allow_credentials=not _cors_star,
    allow_methods=["*"],
    allow_headers=["*"],
)

job_queue = get_job_queue()
file_store = get_file_store()


async def _handle_job(payload: dict[str, Any]) -> None:
    await handle_job(payload, file_store=file_store)


@app.on_event("startup")
async def startup() -> None:
    init_db()
    await job_queue.start(_handle_job)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


class PasteRunRequest(BaseModel):
    drugs: list[DrugInput]
    options: ExtractionOptions = ExtractionOptions()


def _create_run(db: Session, drugs: list[DrugInput], options: ExtractionOptions) -> ExtractionRunORM:
    run = ExtractionRunORM(id=new_id(), status="queued", options_json=options.model_dump())
    db.add(run)
    for d in drugs:
        db.add(
            DrugJobORM(
                id=new_id(),
                run_id=run.id,
                drug_name=d.drug_name.strip(),
                generic_name=d.generic_name,
                manufacturer=d.manufacturer,
                ticker=d.ticker,
                cik=d.cik,
                indication=d.indication,
                known_source_url=d.known_source_url,
                status=JobStatus.QUEUED.value,
            )
        )
    db.commit()
    db.refresh(run)
    return run


@app.post("/runs")
async def create_run(body: PasteRunRequest) -> dict[str, Any]:
    if not body.drugs:
        raise HTTPException(400, "At least one drug is required")
    db = SessionLocal()
    try:
        run = _create_run(db, body.drugs, body.options)
        jobs = db.query(DrugJobORM).filter_by(run_id=run.id).all()
        for job in jobs:
            await job_queue.enqueue("drug_job", {"job_id": job.id, "run_id": run.id})
        run.status = "running"
        db.commit()
        return {"run_id": run.id, "job_count": len(jobs)}
    finally:
        db.close()


@app.post("/runs/from-csv")
async def create_run_from_csv(
    file: UploadFile = File(...),
    options_json: str = Form(default="{}"),
) -> dict[str, Any]:
    import json

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        drugs: list[DrugInput] = []
        for row in reader:
            name = row.get("drug_name") or row.get("Drug") or row.get("product") or row.get("name")
            if not name:
                continue
            drugs.append(
                DrugInput(
                    drug_name=name.strip(),
                    generic_name=(row.get("generic_name") or None),
                    manufacturer=(row.get("manufacturer") or None),
                    ticker=(row.get("ticker") or None),
                    cik=(row.get("CIK") or row.get("cik") or None),
                    indication=(row.get("indication") or None),
                    known_source_url=(row.get("known_source_url") or None),
                )
            )
    except Exception as exc:
        raise HTTPException(400, f"Could not parse CSV: {exc}") from exc
    if not drugs:
        raise HTTPException(400, "CSV contained no drug names")
    try:
        options = ExtractionOptions.model_validate(json.loads(options_json or "{}"))
    except Exception:
        options = ExtractionOptions()
    db = SessionLocal()
    try:
        run = _create_run(db, drugs, options)
        jobs = db.query(DrugJobORM).filter_by(run_id=run.id).all()
        for job in jobs:
            await job_queue.enqueue("drug_job", {"job_id": job.id, "run_id": run.id})
        run.status = "running"
        db.commit()
        return {"run_id": run.id, "job_count": len(jobs), "parsed_drugs": [d.drug_name for d in drugs]}
    finally:
        db.close()


@app.post("/runs/infer-template")
async def infer_template(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read()
    try:
        return TemplateMapper().infer(data)
    except Exception as exc:
        raise HTTPException(400, f"Could not read workbook: {exc}") from exc


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        run = db.get(ExtractionRunORM, run_id)
        if not run:
            raise HTTPException(404, "run not found")
        jobs = db.query(DrugJobORM).filter_by(run_id=run_id).all()
        return {
            "id": run.id,
            "status": run.status,
            "options": run.options_json,
            "jobs": [
                {
                    "id": j.id,
                    "drug_name": j.drug_name,
                    "status": j.status,
                    "current_step": j.current_step,
                    "sources_found": j.sources_found,
                    "candidates_extracted": j.candidates_extracted,
                    "auto_pass_count": j.auto_pass_count,
                    "needs_review_count": j.needs_review_count,
                    "unresolved_count": j.unresolved_count,
                    "completeness_pct": j.completeness_pct,
                    "quality_flags": j.quality_flags,
                    "error": j.error,
                }
                for j in jobs
            ],
            "aggregate": {
                "total": len(jobs),
                "ready": sum(1 for j in jobs if j.status == JobStatus.READY_FOR_REVIEW.value),
                "failed": sum(1 for j in jobs if j.status == JobStatus.FAILED.value),
                "running": sum(1 for j in jobs if j.status == JobStatus.RUNNING.value),
            },
        }
    finally:
        db.close()


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        job = (
            db.query(DrugJobORM)
            .options(
                joinedload(DrugJobORM.profile_fields),
                joinedload(DrugJobORM.datapoints),
                joinedload(DrugJobORM.sources),
                joinedload(DrugJobORM.unresolved_quarters),
                joinedload(DrugJobORM.validation_tasks),
                joinedload(DrugJobORM.quality_checks),
            )
            .filter_by(id=job_id)
            .first()
        )
        if not job:
            raise HTTPException(404, "job not found")
        return {
            "id": job.id,
            "run_id": job.run_id,
            "drug_name": job.drug_name,
            "generic_name": job.generic_name,
            "manufacturer": job.manufacturer,
            "ticker": job.ticker,
            "cik": job.cik,
            "status": job.status,
            "current_step": job.current_step,
            "completeness_pct": job.completeness_pct,
            "profile": [
                {
                    "field": f.field,
                    "value": f.value,
                    "citation": f.citation_json,
                    "validation_status": f.validation_status,
                }
                for f in job.profile_fields
            ],
            "datapoints": [
                {
                    "id": d.id,
                    "period": d.period,
                    "value_reported": d.value_reported,
                    "value_normalized_usd_millions": d.value_normalized_usd_millions,
                    "currency": d.currency,
                    "unit": d.unit,
                    "period_type": d.period_type,
                    "revenue_scope": d.revenue_scope,
                    "geography": d.geography,
                    "formulation": d.formulation,
                    "source_url": d.source_url,
                    "source_quote": d.source_quote,
                    "source_support": d.source_support,
                    "confidence_score": d.confidence_score,
                    "validation_status": d.validation_status,
                    "issue_flags": d.issue_flags,
                    "reviewer_notes": d.reviewer_notes,
                    "citation": d.citation_json,
                }
                for d in job.datapoints
            ],
            "sources": [
                {
                    "source_id": s.id,
                    "source_type": s.source_type,
                    "source_title": s.source_title,
                    "source_url": s.source_url,
                    "source_date": s.source_date,
                    "filing_type": s.filing_type,
                    "accession_number": s.accession_number,
                    "page_or_section": s.page_or_section,
                    "retrieval_status": s.retrieval_status,
                    "parsing_status": s.parsing_status,
                    "relevant_datapoints_found": s.relevant_datapoints_found,
                    "notes": s.notes,
                }
                for s in job.sources
            ],
            "unresolved_quarters": [
                {
                    "id": u.id,
                    "period": u.period,
                    "reason_unresolved": u.reason_unresolved,
                    "sources_checked": u.sources_checked,
                    "recommended_next_step": u.recommended_next_step,
                    "confidence_that_unavailable": u.confidence_that_unavailable,
                    "reviewer_notes": u.reviewer_notes,
                }
                for u in job.unresolved_quarters
            ],
            "validation_tasks": [
                {
                    "id": t.id,
                    "datapoint_id": t.datapoint_id,
                    "reason": t.reason,
                    "confidence_score": t.confidence_score,
                    "status": t.status,
                    "reviewer_notes": t.reviewer_notes,
                }
                for t in job.validation_tasks
            ],
            "quality_checks": [
                {
                    "id": q.id,
                    "issue_type": q.issue_type,
                    "severity": q.severity,
                    "affected_datapoint": q.affected_datapoint,
                    "explanation": q.explanation,
                    "recommended_action": q.recommended_action,
                    "status": q.status,
                }
                for q in job.quality_checks
            ],
        }
    finally:
        db.close()


class DatapointPatch(BaseModel):
    value_normalized_usd_millions: float | None = None
    validation_status: str | None = None
    reviewer_notes: str | None = None
    revenue_scope: str | None = None


@app.patch("/datapoints/{datapoint_id}")
def patch_datapoint(datapoint_id: str, body: DatapointPatch) -> dict[str, Any]:
    db = SessionLocal()
    try:
        dp = db.get(DatapointORM, datapoint_id)
        if not dp:
            raise HTTPException(404, "datapoint not found")
        before = {
            "value_normalized_usd_millions": dp.value_normalized_usd_millions,
            "validation_status": dp.validation_status,
            "reviewer_notes": dp.reviewer_notes,
        }
        if body.value_normalized_usd_millions is not None:
            dp.value_normalized_usd_millions = body.value_normalized_usd_millions
        if body.validation_status is not None:
            dp.validation_status = body.validation_status
        if body.reviewer_notes is not None:
            dp.reviewer_notes = body.reviewer_notes
        if body.revenue_scope is not None:
            dp.revenue_scope = body.revenue_scope
        db.add(
            ReviewEventORM(
                id=new_id(),
                job_id=dp.job_id,
                datapoint_id=dp.id,
                action="edit",
                before_json=before,
                after_json=body.model_dump(exclude_none=True),
                notes=body.reviewer_notes,
            )
        )
        db.commit()
        return {"id": dp.id, "validation_status": dp.validation_status}
    finally:
        db.close()


class ValidationAction(BaseModel):
    action: str
    notes: str | None = None


@app.post("/validation-tasks/{task_id}/actions")
def validation_action(task_id: str, body: ValidationAction) -> dict[str, Any]:
    db = SessionLocal()
    try:
        task = db.get(ValidationTaskORM, task_id)
        if not task:
            raise HTTPException(404, "task not found")
        dp = db.get(DatapointORM, task.datapoint_id)
        if not dp:
            raise HTTPException(404, "datapoint not found")
        mapping = {
            "confirm": ValidationStatus.CONFIRMED.value,
            "reject": ValidationStatus.REJECTED.value,
            "follow_up": ValidationStatus.FOLLOW_UP.value,
        }
        if body.action not in mapping:
            raise HTTPException(400, "action must be confirm|reject|follow_up")
        before = {"validation_status": dp.validation_status}
        dp.validation_status = mapping[body.action]
        if body.notes:
            dp.reviewer_notes = body.notes
            task.reviewer_notes = body.notes
        task.status = "closed"
        db.add(
            ReviewEventORM(
                id=new_id(),
                job_id=task.job_id,
                datapoint_id=dp.id,
                action=body.action,
                before_json=before,
                after_json={"validation_status": dp.validation_status},
                notes=body.notes,
            )
        )
        db.commit()
        return {"task_id": task.id, "datapoint_status": dp.validation_status}
    finally:
        db.close()


@app.get("/dashboard/preview")
def dashboard_preview(run_id: str | None = None) -> dict[str, Any]:
    db = SessionLocal()
    try:
        q = db.query(DrugJobORM).options(
            joinedload(DrugJobORM.profile_fields),
            joinedload(DrugJobORM.datapoints),
        )
        if run_id:
            q = q.filter_by(run_id=run_id)
        jobs = q.all()
        products = []
        series = []
        for j in jobs:
            fields = {f.field: f.value for f in j.profile_fields}
            products.append(
                {
                    "job_id": j.id,
                    "product_name": j.drug_name,
                    "therapeutic_area": fields.get("therapeutic_area") or fields.get("pharmacologic_class"),
                    "manufacturer": j.manufacturer or fields.get("manufacturer"),
                    "fda_approval_date": fields.get("fda_approval_date"),
                    "approved_indications": fields.get("indication") or j.indication,
                    "moa": fields.get("moa") or fields.get("pharmacologic_class"),
                    "roa": fields.get("roa"),
                    "treatment_type": fields.get("treatment_type"),
                    "approved_lot": fields.get("approved_lot"),
                    "reached_peak_yet": fields.get("reached_peak_yet"),
                    "estimated_peak_revenue": fields.get("estimated_peak_revenue"),
                    "time_to_peak": fields.get("time_to_peak"),
                    "source_link": next((d.source_url for d in j.datapoints if d.source_url), None),
                    "completeness_score": j.completeness_pct,
                    "validation_status": j.status,
                }
            )
            for d in j.datapoints:
                series.append(
                    {
                        "product": j.drug_name,
                        "period": d.period,
                        "value": d.value_normalized_usd_millions,
                        "validation_status": d.validation_status,
                        "source_url": d.source_url,
                        "source_quote": d.source_quote,
                        "citation": d.citation_json,
                        "issue_flags": d.issue_flags,
                        "reviewer_notes": d.reviewer_notes,
                    }
                )
        return {"products": products, "series": series}
    finally:
        db.close()


class ExportRequest(BaseModel):
    run_id: str | None = None
    job_id: str | None = None
    format: str


@app.post("/exports")
async def create_export(body: ExportRequest) -> dict[str, Any]:
    db = SessionLocal()
    try:
        builder = ExportBuilder(db, file_store)
        if body.format == "product_workbook":
            if not body.job_id:
                raise HTTPException(400, "job_id required")
            dps = db.query(DatapointORM).filter_by(job_id=body.job_id).all()
            bad = [
                d.id
                for d in dps
                if d.validation_status == ValidationStatus.CONFIRMED.value
                and (not d.source_url or not d.source_quote)
            ]
            if bad:
                raise HTTPException(400, f"Confirmed datapoints missing citations: {bad}")
            exp = await builder.export_product_workbook(body.job_id)
            return {"export_id": exp.id, "storage_key": exp.storage_key}
        if body.format == "powerbi":
            if not body.run_id:
                raise HTTPException(400, "run_id required")
            exports = await builder.export_powerbi_csvs(body.run_id)
            return {"exports": [{"id": e.id, "format": e.format, "storage_key": e.storage_key} for e in exports]}
        raise HTTPException(400, "unsupported format")
    finally:
        db.close()


@app.get("/exports/{export_id}/download")
async def download_export(export_id: str) -> Response:
    db = SessionLocal()
    try:
        exp = db.get(ExportORM, export_id)
        if not exp:
            raise HTTPException(404, "export not found")
        data = await file_store.get(exp.storage_key)
        media = "application/octet-stream"
        if exp.storage_key.endswith(".xlsx"):
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif exp.storage_key.endswith(".csv"):
            media = "text/csv"
        filename = exp.storage_key.rsplit("/", 1)[-1]
        return Response(
            content=data,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        db.close()


@app.get("/")
def root() -> dict[str, str]:
    return {"app": settings.app_name, "docs": "/docs"}
