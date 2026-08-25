from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import DrugJobORM, ExtractionRunORM
from app.domain.models import JobStatus

logger = logging.getLogger(__name__)

_TERMINAL = {
    JobStatus.READY_FOR_REVIEW.value,
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
}


def refresh_run_status(db: Session, run_id: str) -> str | None:
    """Set extraction_runs.status from child jobs once all jobs are terminal."""
    run = db.get(ExtractionRunORM, run_id)
    if not run:
        logger.warning("run_status_skip missing_run run_id=%s", run_id)
        return None
    jobs = db.query(DrugJobORM).filter_by(run_id=run.id).all()
    if not jobs:
        return run.status
    statuses = {j.status for j in jobs}
    if not statuses <= _TERMINAL:
        return run.status
    if JobStatus.FAILED.value in statuses and not (
        statuses & {JobStatus.READY_FOR_REVIEW.value, JobStatus.COMPLETED.value}
    ):
        run.status = "failed"
    elif JobStatus.READY_FOR_REVIEW.value in statuses:
        run.status = "ready_for_review"
    elif JobStatus.CANCELLED.value in statuses and statuses <= {
        JobStatus.CANCELLED.value,
        JobStatus.FAILED.value,
    }:
        run.status = "cancelled"
    else:
        run.status = "completed"
    db.commit()
    logger.info("run_status run_id=%s status=%s jobs=%s", run.id, run.status, sorted(statuses))
    return run.status
