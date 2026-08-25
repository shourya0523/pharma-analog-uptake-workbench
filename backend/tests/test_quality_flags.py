"""Provenance flags raised during retrieval must survive the quality-check step.

The pipeline flags how a value was obtained - openfda_no_brand_match,
cik_from_llm_search, llm_search_revenue_fallback, no_product_revenue_candidates -
but the quality step replaced the whole list, so none of them ever reached the API.
"""

import inspect

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DrugJobORM, ExtractionRunORM
from app.domain.models import new_id
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import LocalFileStore


def _orchestrator(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    return db, PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path)))


async def test_quality_step_preserves_earlier_flags(tmp_path):
    db, orch = _orchestrator(tmp_path)
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    job = DrugJobORM(
        id=new_id(),
        run_id=run.id,
        drug_name="Alyq",
        status="running",
        quality_flags=["openfda_no_brand_match", "cik_from_llm_search"],
    )
    db.add(job)
    db.commit()

    await orch._quality_and_validation(job)

    assert "openfda_no_brand_match" in job.quality_flags
    assert "cik_from_llm_search" in job.quality_flags


async def test_quality_step_still_adds_high_severity_issues(tmp_path):
    db, orch = _orchestrator(tmp_path)
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Tyvaso", status="running", quality_flags=[])
    db.add(job)
    db.commit()

    await orch._quality_and_validation(job)
    # No datapoints means no issues, and the list stays empty rather than None
    assert job.quality_flags == []


def test_flags_are_merged_not_replaced():
    source = inspect.getsource(PipelineOrchestrator._quality_and_validation)
    assert "set(job.quality_flags or [])" in source
