"""Several jobs commit to one SQLite file while the API polls it.

SQLite admits one writer. A job that flushes a row and then waits on the model
holds the write lock for the length of that call, and every other job's commit
times out behind it, fails, and - because the failure is itself a commit -
leaves the job recorded as still running.
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DrugJobORM, ExtractionRunORM, sqlite_connect_args
from app.domain.models import (
    ParsedDocument,
    ParsingStatus,
    RetrievalStatus,
    RetrievedSource,
    SourceType,
    new_id,
)
from app.jobs import handler
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import LocalFileStore

LABEL = {
    "application_number": "NDA000001",
    "sponsor_name": "ACME",
    "openfda": {
        "brand_name": ["CALDERON"],
        "generic_name": ["CALDERONIB"],
        "route": ["ORAL"],
        "pharm_class_epc": ["Kinase Inhibitor [EPC]"],
    },
    "submissions": [
        {"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20200101"}
    ],
}


def _database(tmp_path, *, timeout: int | None = None):
    path = tmp_path / "workbench.db"
    connect_args = sqlite_connect_args("sqlite://")
    if timeout is not None:
        connect_args["timeout"] = timeout
    engine = create_engine(f"sqlite:///{path}", connect_args=connect_args)
    Base.metadata.create_all(engine)
    return path, sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _job(db):
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    job = DrugJobORM(
        id=new_id(),
        run_id=run.id,
        drug_name="Calderon",
        generic_name="calderonib",
        manufacturer="Acme",
        status="running",
        quality_flags=[],
    )
    db.add(job)
    db.commit()
    return job


class AnotherJobWrites:
    """A model whose every call is another job committing to the same file.

    The second connection waits at most a second; the pipeline's own timeout
    is longer, so a lock held across the call shows up as an error here rather
    than as a slow test.
    """

    def __init__(self, path):
        self.path = path
        self.calls = 0

    def _write(self):
        self.calls += 1
        other = sqlite3.connect(self.path, timeout=1)
        try:
            other.execute("UPDATE drug_jobs SET current_step = 'source_retrieve'")
            other.commit()
        finally:
            other.close()

    async def extract_metadata(self, **_):
        self._write()
        return {"fields": []}

    async def judge_profile_field(self, **_):
        self._write()
        return {}


@pytest.mark.asyncio
async def test_a_model_call_does_not_hold_the_write_lock(tmp_path):
    path, session = _database(tmp_path)
    db = session()
    job = _job(db)
    orch = PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path)))
    orch.llm = AnotherJobWrites(str(path))
    sources = [
        RetrievedSource(
            source_id="fda",
            source_type=SourceType.OPENFDA,
            url="https://api.fda.gov/drug/drugsfda.json?search=calderon",
            retrieval_status=RetrievalStatus.SUCCESS,
            metadata={"results": [LABEL]},
        ),
        RetrievedSource(
            source_id="s1",
            source_type=SourceType.SEC_FILING,
            url="https://example.invalid/10q.htm",
            filing_type="10-Q",
            retrieval_status=RetrievalStatus.SUCCESS,
        ),
    ]
    parsed = {
        "s1": ParsedDocument(
            source_id="s1",
            text_blocks=["Calderon is indicated for the treatment of adults."],
            parsing_status=ParsingStatus.SUCCESS,
        )
    }

    await orch._extract_metadata(job, sources, parsed, {"product_metadata": True})

    assert orch.llm.calls > 0, "the step never reached the model, so nothing was tested"


@pytest.mark.asyncio
async def test_a_job_that_fails_inside_a_commit_is_recorded_as_failed(tmp_path, monkeypatch):
    path, session = _database(tmp_path, timeout=1)
    db = session()
    job = _job(db)
    job_id, run_id = job.id, job.run_id
    db.close()
    monkeypatch.setattr(handler, "SessionLocal", session)

    async def another_job_holds_the_lock(self, row):
        other = sqlite3.connect(str(path))
        other.execute("BEGIN IMMEDIATE")
        try:
            row.current_step = "source_retrieve"
            self.db.flush()
        finally:
            other.rollback()
            other.close()

    monkeypatch.setattr(PipelineOrchestrator, "_identity", another_job_holds_the_lock)

    with pytest.raises(Exception, match="database is locked"):
        await handler.handle_job({"job_id": job_id, "run_id": run_id},
                                 file_store=LocalFileStore(str(tmp_path)))

    db = session()
    assert db.get(DrugJobORM, job_id).status == "failed"
    assert db.get(ExtractionRunORM, run_id).status == "failed"
    db.close()


def test_a_lock_timeout_names_the_job_holding_the_write(tmp_path, caplog):
    """The log has to say who was holding the write, not only who waited."""
    from app.db.models import watch_for_held_writes

    path = tmp_path / "workbench.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 1})
    watch_for_held_writes(engine)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    holder, waiter = session(), session()
    holder.add(ExtractionRunORM(id=new_id(), status="running", options_json={}))
    holder.flush()  # a writer now, and not committing
    waiter.add(ExtractionRunORM(id=new_id(), status="running", options_json={}))
    with pytest.raises(Exception, match="database is locked"), caplog.at_level("ERROR"):
        waiter.commit()
    holder.rollback()

    held = [r.getMessage() for r in caplog.records if "write_lock_held_by" in r.getMessage()]
    assert len(held) == 1
    assert "INSERT INTO extraction_runs" in held[0]
    assert "test_jobs_share_one_database" in held[0] or "tests/" in held[0] or "frames=" in held[0]
