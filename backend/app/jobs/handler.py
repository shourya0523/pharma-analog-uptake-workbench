from __future__ import annotations

import logging
from typing import Any

from app.db.models import SessionLocal
from app.jobs.run_status import refresh_run_status
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import FileStore, get_file_store

logger = logging.getLogger(__name__)


async def handle_job(payload: dict[str, Any], file_store: FileStore | None = None) -> None:
    store = file_store or get_file_store()
    job_id = payload["job_id"]
    run_id = payload["run_id"]
    db = SessionLocal()
    try:
        orch = PipelineOrchestrator(db, file_store=store)
        await orch.run_job(job_id)
    finally:
        try:
            refresh_run_status(db, run_id)
        except Exception:
            logger.exception("run_status_update_failed run_id=%s", run_id)
        db.close()
