"""The label is read before EDGAR is asked whose filings to download.

`_identity` ran first and had only what the caller typed. A drug name on its
own reaches no name index - the index answers a ticker or a company name - so
the job went straight to the model, which returned a CIK and a confidence
nobody read, and that company's filings were downloaded against this job. The
sponsor was sitting in the drug label the same run went on to fetch.

So the product's own documents are retrieved and read first, the sponsor they
name is on the job, and the CIK is then looked up from a company name.

And when no name resolves, the run says so in those words: "we could not reach
the SEC" is a different sentence from "we do not know whose filings to ask
for", and a reader takes the first as a transient failure of ours.

Invented names: Calderon, Acme Pharma.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DrugJobORM, ExtractionRunORM
from app.domain.models import (
    NO_FILER_OF_RECORD,
    JobStep,
    RetrievalStatus,
    RetrievedSource,
    SourceType,
    new_id,
)
from app.pipeline import orchestrator as pipeline
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import LocalFileStore


def _offline(monkeypatch):
    """No model, so every step that would reach for one is a no-op here."""
    settings = pipeline.get_settings().model_copy(update={"enable_llm_search": False})
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)


def _job(**fields):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon",
                     status="running", quality_flags=[], **fields)
    db.add_all([run, job]); db.commit()
    return db, job


def _label(status=RetrievalStatus.SUCCESS):
    return RetrievedSource(
        source_id=new_id(), source_type=SourceType.OPENFDA,
        url="https://example.invalid/openfda", retrieval_status=status,
    )


def test_the_sponsor_is_known_before_the_cik_is_looked_up(monkeypatch, tmp_path):
    """The order is the claim: what `resolve_cik` is handed is what the label
    step put on the job, not what the caller typed."""
    _offline(monkeypatch)
    db, job = _job()
    orch = PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path)))
    order: list[str] = []
    asked: list[tuple] = []

    async def _fda(**_kw):
        order.append("label")
        return [_label()]

    async def _sec(**_kw):
        order.append("filings")
        return []

    async def _metadata(self, job_, sources, parsed, options):
        order.append("metadata")
        # What `_extract_metadata` does with the label: the sponsor it names
        # becomes the job's manufacturer.
        job_.manufacturer = "Acme Pharma"
        self.db.commit()

    async def _resolve_cik(ticker=None, company_name=None):
        order.append("identity")
        asked.append((ticker, company_name))
        return "0000000001" if company_name else None

    monkeypatch.setattr(orch.fda, "retrieve", _fda)
    monkeypatch.setattr(orch.sec, "retrieve", _sec)
    monkeypatch.setattr(orch.sec, "resolve_cik", _resolve_cik)
    monkeypatch.setattr(PipelineOrchestrator, "_extract_metadata", _metadata)
    for step in ("_judge_profile", "_quality_and_validation", "_completeness"):
        async def _skip(*_a, _step=step, **_kw):
            return None
        monkeypatch.setattr(PipelineOrchestrator, step, _skip)

    async def _no_rows(*_a, **_kw):
        return []
    monkeypatch.setattr(PipelineOrchestrator, "_extract_revenue", _no_rows)
    monkeypatch.setattr(PipelineOrchestrator, "_judge", _no_rows)
    monkeypatch.setattr(orch, "_expand_aliases", lambda _job: _no_rows())

    asyncio.run(orch.run_job(job.id))

    assert order.index("label") < order.index("metadata") < order.index("identity")
    assert order.index("identity") < order.index("filings")
    assert asked == [(None, "Acme Pharma")]
    assert job.cik == "0000000001"
    assert job.current_step == JobStep.READY_FOR_REVIEW.value


@pytest.mark.asyncio
async def test_no_filer_of_record_when_nothing_says_who_files(tmp_path, monkeypatch):
    db, job = _job()
    orch = PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path)))

    async def _none(**_kw):
        return []

    monkeypatch.setattr(orch.sec, "retrieve", _none)
    monkeypatch.setattr(orch.search, "fallback_retrieve", _none)
    await orch._retrieve(job, {"sec_filings": True, "openfda": False})

    assert NO_FILER_OF_RECORD in (job.quality_flags or [])
    assert "sec_retrieval_failed" not in (job.quality_flags or [])


@pytest.mark.asyncio
async def test_a_resolved_filer_with_nothing_in_the_window_is_not_that(tmp_path, monkeypatch):
    """An issuer we know, which filed nothing in the window, is a different
    answer and keeps the flag off."""
    db, job = _job(cik="0000000001")
    orch = PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path)))

    async def _none(**_kw):
        return []

    monkeypatch.setattr(orch.sec, "retrieve", _none)
    monkeypatch.setattr(orch.search, "fallback_retrieve", _none)
    await orch._retrieve(job, {"sec_filings": True, "openfda": False})

    assert NO_FILER_OF_RECORD not in (job.quality_flags or [])


@pytest.mark.asyncio
async def test_the_label_pass_does_not_trip_the_filing_search(tmp_path, monkeypatch):
    """The pass that asks openFDA asks for no filings, so "no filings found"
    is not a thing it can conclude."""
    db, job = _job()
    orch = PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path)))
    asked: list[str] = []

    async def _fallback(**kw):
        asked.append(kw.get("goal"))
        return []

    async def _fda(**_kw):
        return [_label()]

    monkeypatch.setattr(orch.search, "fallback_retrieve", _fallback)
    monkeypatch.setattr(orch.fda, "retrieve", _fda)
    await orch._retrieve(
        job, {"sec_filings": False, "earnings_releases": False, "openfda": True}
    )

    assert asked == []
    assert NO_FILER_OF_RECORD not in (job.quality_flags or [])
