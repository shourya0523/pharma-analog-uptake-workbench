"""A job that hangs is stopped, and the queue behind it moves on.

The queue holds a concurrency permit for as long as the handler is awaited, and
every timeout in the stack below the handler is per HTTP operation rather than
wall clock - a model call is several attempts with sleeps between them, and none
of that is a bound on the job. Nothing bounded a job, so a job that hung held
its permit for the life of the process: its own row read `running` for ever,
which is what a caller polling the run waits on, and at one permit nothing
queued behind it ever started.

Invented names: Calderon, NuVessa.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.models import Base, DatapointORM, DrugJobORM, ExtractionRunORM
from app.domain.models import JobStatus, ValidationStatus, new_id
from app.jobs import handler as handler_module
from app.jobs.queue import InProcessJobQueue
from app.pipeline.orchestrator import PipelineOrchestrator

# Long enough that a job left unbounded is still awaiting it when the queue is
# asked to drain, and the drain is given a fraction of it.
A_HANG = 120.0
LONG_ENOUGH_TO_DRAIN = 10.0
A_SHORT_DEADLINE = 1


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _database(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(handler_module, "SessionLocal", factory)
    return factory


def _job(db, drug: str) -> DrugJobORM:
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name=drug,
                     status=JobStatus.RUNNING.value, quality_flags=[])
    db.add_all([run, job])
    db.commit()
    return job


def _deadline(monkeypatch, seconds: int) -> None:
    """Set the deadline the way an operator would.

    Through the environment rather than by replacing the handler's own
    `get_settings`, so the test says nothing about where the handler reads it.
    """
    monkeypatch.setenv("JOB_DEADLINE_SECONDS", str(seconds))
    get_settings.cache_clear()


async def test_a_job_that_outlives_its_deadline_is_stopped_and_the_queue_moves_on(
    monkeypatch,
):
    """Both answers, through the queue that holds the permit.

    The job that hangs is failed with a reason naming the deadline and keeps the
    figure its finished stage committed; the job queued behind it starts and
    finishes inside its own deadline, which is the permit having been given up.
    """
    factory = _database(monkeypatch)
    _deadline(monkeypatch, A_SHORT_DEADLINE)
    db = factory()
    hangs = _job(db, "Calderon")
    finishes = _job(db, "NuVessa")

    async def run_job(self, job_id):
        job = self.db.get(DrugJobORM, job_id)
        self.db.add(DatapointORM(
            id=new_id(), job_id=job.id, period="2024Q1", period_type="quarterly",
            value_normalized_usd_millions=12.0, revenue_scope="Product family",
            currency="USD", source_url="https://sec.gov/a-filing",
            source_quote=f"{job.drug_name} 12.0",
            validation_status=ValidationStatus.AUTO_PASS.value,
        ))
        self.db.commit()
        if job.drug_name == "Calderon":
            await asyncio.sleep(A_HANG)
        job.status = JobStatus.READY_FOR_REVIEW.value
        self.db.commit()

    monkeypatch.setattr(PipelineOrchestrator, "run_job", run_job)

    queue = InProcessJobQueue(max_concurrent=1)
    await queue.start(handler_module.handle_job)
    for job in (hangs, finishes):
        await queue.enqueue("drug_job", {"job_id": job.id, "run_id": job.run_id})
    await asyncio.wait_for(queue.stop(), timeout=LONG_ENOUGH_TO_DRAIN)

    db.expire_all()
    stopped = db.get(DrugJobORM, hangs.id)
    assert stopped.status == JobStatus.FAILED.value
    assert "deadline" in stopped.error
    kept = db.query(DatapointORM).filter(DatapointORM.job_id == hangs.id).all()
    assert [row.period for row in kept] == ["2024Q1"]
    assert db.get(DrugJobORM, finishes.id).status == JobStatus.READY_FOR_REVIEW.value
