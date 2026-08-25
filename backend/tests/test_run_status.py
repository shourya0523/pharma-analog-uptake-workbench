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
