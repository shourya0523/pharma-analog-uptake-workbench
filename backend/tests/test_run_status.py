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

    requeued, abandoned = asyncio.run(go())
    # the create_task calls need a turn of the loop to run
    asyncio.run(asyncio.sleep(0))
    assert (requeued, abandoned) == (1, 1)
    assert db.get(DrugJobORM, midway.id).status == JobStatus.FAILED.value
    assert "restarted" in db.get(DrugJobORM, midway.id).error
    assert db.get(DrugJobORM, done.id).status == JobStatus.READY_FOR_REVIEW.value
