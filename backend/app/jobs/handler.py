from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import get_settings
from app.db.models import DrugJobORM, SessionLocal
from app.domain.models import JobStatus
from app.jobs.run_status import refresh_run_status
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import FileStore, get_file_store

logger = logging.getLogger(__name__)


async def handle_job(payload: dict[str, Any], file_store: FileStore | None = None) -> None:
    """Run one job, and stop it if it outlives its deadline.

    Every timeout under this one is per HTTP operation rather than wall clock,
    and the queue holds a concurrency permit for as long as this is awaited, so
    a job that hangs holds that permit for the life of the process and nothing
    behind it in the queue starts. The deadline is the event loop's, so it
    stops the job at whatever it is awaiting.

    The job is recorded as having passed its deadline, because a row left
    `running` for ever is what a caller polling the run waits on. What the
    stages that finished committed is theirs and stays; the rollback discards
    only the stage that was in flight when the deadline passed.
    """
    store = file_store or get_file_store()
    job_id = payload["job_id"]
    run_id = payload["run_id"]
    deadline = get_settings().job_deadline_seconds
    db = SessionLocal()
    try:
        orch = PipelineOrchestrator(db, file_store=store)
        await asyncio.wait_for(orch.run_job(job_id), timeout=deadline)
    except TimeoutError:
        # The cancellation that stops `run_job` does not reach its own failure
        # handler - that catches `Exception` and this is not one - so the job
        # is recorded here, after the session is made usable again.
        db.rollback()
        job = db.get(DrugJobORM, job_id)
        if job:
            job.status = JobStatus.FAILED.value
            job.error = f"the job passed its deadline of {deadline}s and was stopped"
            db.commit()
        logger.error(
            "job_deadline_passed job_id=%s run_id=%s deadline=%ss", job_id, run_id, deadline
        )
    finally:
        try:
            # A job that failed inside a commit leaves the session unusable
            # until it is rolled back; the run's status still has to be set.
            db.rollback()
            refresh_run_status(db, run_id)
        except Exception:
            logger.exception("run_status_update_failed run_id=%s", run_id)
        db.close()
