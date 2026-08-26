from __future__ import annotations

from typing import Any

from app.db.models import DrugJobORM, ExtractionRunORM, SessionLocal
from app.domain.models import JobStatus
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import FileStore, get_file_store


async def handle_job(payload: dict[str, Any], file_store: FileStore | None = None) -> None:
    store = file_store or get_file_store()
    db = SessionLocal()
    try:
        orch = PipelineOrchestrator(db, file_store=store)
        await orch.run_job(payload["job_id"])
        run = db.get(ExtractionRunORM, payload["run_id"])
        if run:
            jobs = db.query(DrugJobORM).filter_by(run_id=run.id).all()
            if all(
                j.status
                in {
                    JobStatus.READY_FOR_REVIEW.value,
                    JobStatus.COMPLETED.value,
                    JobStatus.FAILED.value,
                }
                for j in jobs
            ):
                run.status = (
                    "ready_for_review"
                    if any(j.status == JobStatus.READY_FOR_REVIEW.value for j in jobs)
                    else "completed"
                )
                db.commit()
    finally:
        db.close()
