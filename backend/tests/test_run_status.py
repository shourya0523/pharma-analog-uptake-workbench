from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DrugJobORM, ExtractionRunORM
from app.domain.models import JobStatus, new_id
from app.jobs.run_status import refresh_run_status


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _job(run_id: str, status: str, step: str = "extract_revenue") -> DrugJobORM:
    return DrugJobORM(
        id=new_id(),
        run_id=run_id,
        drug_name="Opsumit",
        status=status,
        current_step=step,
    )


def test_refresh_run_status_marks_failed_when_all_jobs_fail():
    db = _session()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    db.add(_job(run.id, JobStatus.FAILED.value))
    db.commit()
    assert refresh_run_status(db, run.id) == "failed"
    db.refresh(run)
    assert run.status == "failed"


def test_refresh_run_status_stays_running_until_all_jobs_terminal():
    db = _session()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    db.add(_job(run.id, JobStatus.FAILED.value))
    db.add(_job(run.id, JobStatus.RUNNING.value, "source_retrieve"))
    db.commit()
    assert refresh_run_status(db, run.id) == "running"
    db.refresh(run)
    assert run.status == "running"


def test_refresh_run_status_ready_when_any_job_needs_review():
    db = _session()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    db.add(_job(run.id, JobStatus.READY_FOR_REVIEW.value, "ready_for_review"))
    db.add(_job(run.id, JobStatus.FAILED.value))
    db.commit()
    assert refresh_run_status(db, run.id) == "ready_for_review"


def test_a_restart_requeues_what_was_waiting_and_owns_up_to_what_was_running():
    """The in-process queue forgets on restart; the database must not lie about it."""
    import asyncio

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base, DrugJobORM, ExtractionRunORM
    from app.domain.models import JobStatus, new_id
    from app.main import recover_stranded_jobs

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    waiting = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon",
                         status=JobStatus.QUEUED.value, quality_flags=[])
    midway = DrugJobORM(id=new_id(), run_id=run.id, drug_name="NuVessa",
                        status=JobStatus.RUNNING.value, quality_flags=[])
    done = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Tavoral",
                      status=JobStatus.READY_FOR_REVIEW.value, quality_flags=[])
    db.add_all([run, waiting, midway, done]); db.commit()

    enqueued = []

    class Queue:
        async def enqueue(self, kind, payload):
            enqueued.append(payload["job_id"])

    async def go():
        return recover_stranded_jobs(db, Queue())

    requeued, abandoned, recovered = asyncio.run(go())
    # the create_task calls need a turn of the loop to run
    asyncio.run(asyncio.sleep(0))
    assert (requeued, abandoned, recovered) == (1, 1, 0)
    assert db.get(DrugJobORM, midway.id).status == JobStatus.FAILED.value
    assert "restarted" in db.get(DrugJobORM, midway.id).error
    assert db.get(DrugJobORM, done.id).status == JobStatus.READY_FOR_REVIEW.value
    # The requeued job keeps this run open; a run with nothing left to run is
    # settled at once, or it would read "running" for ever.
    assert db.get(ExtractionRunORM, run.id).status == "running"
    over = ExtractionRunORM(id=new_id(), status="running", options_json={})
    only = DrugJobORM(id=new_id(), run_id=over.id, drug_name="Calderon XR",
                      status=JobStatus.RUNNING.value, quality_flags=[])
    db.add_all([over, only]); db.commit()
    asyncio.run(go())
    assert db.get(ExtractionRunORM, over.id).status == "failed"


def test_a_restart_keeps_the_work_of_a_job_that_had_nothing_left_to_find():
    """A job interrupted after the last figure was settled is not a failure.

    A restart cannot re-run a job, because `run_job` appends its datapoints. It
    can ask whether anything the job had left to do could still have added a
    figure, and the job's own rows answer: a reading is `pending` until the
    judge decides it, and nothing after the judge writes another one.

    Both answers: a job whose readings are all decided keeps them, is settled
    from its own rows and reads as ready for review, and a job still holding an
    undecided reading is failed as before.
    """
    import asyncio

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base, DatapointORM, DrugJobORM, ExtractionRunORM
    from app.domain.models import (
        JobStatus,
        JobStep,
        SeriesSelection,
        ValidationStatus,
        new_id,
    )
    from app.main import FINISHED_AFTER_A_RESTART, recover_stranded_jobs

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = ExtractionRunORM(
        id=new_id(), status="running",
        options_json={"earnings_since": "2024-01-05", "earnings_until": "2024-10-05"},
    )
    judged = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon",
                        manufacturer="Acme Pharma", cik="0000000001",
                        status=JobStatus.RUNNING.value,
                        current_step=JobStep.COMPLETENESS.value, quality_flags=[])
    judging = DrugJobORM(id=new_id(), run_id=run.id, drug_name="NuVessa",
                         status=JobStatus.RUNNING.value,
                         current_step=JobStep.EVIDENCE_JUDGE.value, quality_flags=[])
    db.add_all([run, judged, judging])
    for index, period in enumerate(("2024Q1", "2024Q2")):
        db.add(DatapointORM(
            id=new_id(), job_id=judged.id, period=period, period_type="quarterly",
            value_normalized_usd_millions=10.0 + index, revenue_scope="Product family",
            currency="USD", source_url="https://sec.gov/a-filing",
            source_quote=f"Calderon {10.0 + index}",
            validation_status=ValidationStatus.AUTO_PASS.value,
        ))
    db.add(DatapointORM(
        id=new_id(), job_id=judging.id, period="2024Q1", period_type="quarterly",
        value_normalized_usd_millions=5.0, revenue_scope="Product family",
        currency="USD", source_url="https://sec.gov/a-filing",
        source_quote="NuVessa 5.0",
        validation_status=ValidationStatus.PENDING.value,
    ))
    db.commit()

    class Queue:
        async def enqueue(self, kind, payload):  # pragma: no cover - nothing is queued here
            raise AssertionError("nothing was waiting")

    requeued, abandoned, recovered = asyncio.run(_recover(db, Queue(), recover_stranded_jobs))
    assert (requeued, abandoned, recovered) == (0, 1, 1)

    kept = db.get(DrugJobORM, judged.id)
    assert kept.status == JobStatus.READY_FOR_REVIEW.value
    assert kept.error is None
    assert FINISHED_AFTER_A_RESTART in kept.quality_flags
    # Settled from its own rows: the series each reading holds, and the recount.
    rows = db.query(DatapointORM).filter_by(job_id=judged.id).all()
    assert all(row.series_identity for row in rows)
    assert {row.series_selection for row in rows} == {SeriesSelection.SELECTED.value}
    assert kept.completeness_pct > 0

    lost = db.get(DrugJobORM, judging.id)
    assert lost.status == JobStatus.FAILED.value
    assert "restarted" in lost.error


async def _recover(db, queue, recover):
    return recover(db, queue)
