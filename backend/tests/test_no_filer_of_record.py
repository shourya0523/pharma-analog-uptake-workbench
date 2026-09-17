"""A quarter no filing of the named issuer covers is said to be one.

A product that changed hands has quarters the issuer named on the job never
reported: a predecessor with no SEC filings, an acquirer whose first release
covers a stub. Retrieval finds nothing and the completeness stage records a
gap to fill from a filing not yet retrieved - but there is no such filing.
The pipeline now asks the search who reported the product then, and what
that leaves is recorded as its own reason, so a reviewer is asked who the
filer was rather than to look again.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    DatapointORM,
    DrugJobORM,
    ExtractionRunORM,
    UnresolvedQuarterORM,
)
from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
from app.parsing.periods import quarter_end, quarters_reported_in
from app.pipeline.orchestrator import NO_FILER_OF_RECORD, PipelineOrchestrator
from app.storage.filestore import LocalFileStore


def test_a_window_covers_the_quarters_reported_inside_it():
    """Gold's layout: a window opening early in the second quarter and
    closing after the next year's first-quarter reports."""
    assert quarters_reported_in(date(2016, 4, 5), date(2017, 5, 5)) == [
        "2016Q1", "2016Q2", "2016Q3", "2016Q4", "2017Q1",
    ]
    # The prior fourth quarter's annual report lands before the window opens.
    assert "2015Q4" not in quarters_reported_in(date(2016, 4, 5), date(2017, 5, 5))
    assert quarters_reported_in(None, None) == []
    assert quarter_end(2016, 4) == date(2016, 12, 31) and quarter_end(2016, 1) == date(2016, 3, 31)


def _orchestrator(tmp_path, *, options: dict):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json=options)
    db.add(run)
    job = DrugJobORM(
        id=new_id(), run_id=run.id, drug_name="Calderon", manufacturer="Acme Pharma",
        status="running", quality_flags=[],
    )
    db.add(job)
    db.commit()
    return PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path))), job, db


def _filing(dated: date) -> RetrievedSource:
    return RetrievedSource(
        source_id=new_id(), source_type=SourceType.SEC_FILING,
        url=f"https://example.invalid/{dated.isoformat()}.htm", filing_type="10-Q",
        retrieval_status=RetrievalStatus.SUCCESS, source_date=dated,
    )


WINDOW = {"earnings_since": "2016-04-05", "earnings_until": "2017-05-05"}


def test_quarters_are_unfiled_only_when_no_filing_of_the_issuer_is_in_the_window(tmp_path):
    orch, job, _db = _orchestrator(tmp_path, options=WINDOW)

    nothing_in_window = [_filing(date(2019, 8, 1))]
    assert orch._quarters_no_filing_covers(job, nothing_in_window, WINDOW, []) == [
        "2016Q1", "2016Q2", "2016Q3", "2016Q4", "2017Q1",
    ]

    one_in_window = nothing_in_window + [_filing(date(2016, 8, 1))]
    assert orch._quarters_no_filing_covers(job, one_in_window, WINDOW, []) == [], (
        "the issuer did file in the window; what it left out is the ordinary kind of gap"
    )

    answered = DatapointORM(id=new_id(), job_id=job.id, period="2016Q3", value_normalized_usd_millions=1.0)
    assert "2016Q3" not in orch._quarters_no_filing_covers(job, nothing_in_window, WINDOW, [answered])
    assert orch._quarters_no_filing_covers(job, nothing_in_window, {}, []) == [], "no window, no claim"


def test_what_the_search_leaves_is_recorded_as_no_filer_of_record(tmp_path):
    orch, job, db = _orchestrator(tmp_path, options=WINDOW)
    found = [RetrievedSource(
        source_id=new_id(), source_type=SourceType.COMPANY_IR,
        url="https://example.invalid/historical-schedule.pdf",
        retrieval_status=RetrievalStatus.SUCCESS,
    )]
    answered = DatapointORM(id=new_id(), job_id=job.id, period="2016Q4", value_normalized_usd_millions=2.0)

    orch._record_unfiled_quarters(job, ["2016Q3", "2016Q4"], [answered], found)

    recorded = {u.period: u for u in db.query(UnresolvedQuarterORM).filter_by(job_id=job.id).all()}
    assert set(recorded) == {"2016Q3"}, "the quarter the search answered is not unresolved"
    assert recorded["2016Q3"].reason_unresolved.startswith(f"[{NO_FILER_OF_RECORD}]")
    assert "Acme Pharma" in recorded["2016Q3"].reason_unresolved
    assert recorded["2016Q3"].sources_checked == ["https://example.invalid/historical-schedule.pdf"]


@pytest.mark.asyncio
async def test_the_search_is_asked_for_the_quarters_by_name(tmp_path):
    orch, job, _db = _orchestrator(tmp_path, options=WINDOW)
    asked: list[dict] = []

    async def fallback_retrieve(**kwargs):
        asked.append(kwargs)
        return []

    orch.search.fallback_retrieve = fallback_retrieve
    sources, parsed = await orch._search_quarters_fallback(job, ["2016Q2", "2016Q3"])

    assert (sources, parsed) == ([], {})
    assert asked and asked[0]["goal"] == "quarters"
    assert "2016Q2, 2016Q3" in asked[0]["context"] and "Acme Pharma" in asked[0]["context"]


@pytest.mark.asyncio
async def test_a_gap_is_recorded_once_whoever_names_it(tmp_path):
    """The deterministic fill and the model both name the quarter between
    two extracted ones. The model's list was checked against unresolved
    rows read before the fill was flushed, so every interior gap was
    recorded twice, and the queue said it twice."""
    orch, job, db = _orchestrator(tmp_path, options={})
    for period, value in (("2024Q1", 5.0), ("2024Q3", 7.0)):
        db.add(DatapointORM(
            id=new_id(), job_id=job.id, period=period, period_type="quarterly",
            value_normalized_usd_millions=value, validation_status="auto_pass",
            source_url="https://example.invalid/10q.htm", source_quote=f"Calderon {value}",
        ))
    db.commit()

    class NamesTheGap:
        async def completeness(self, **kwargs):
            return {"missing_periods": [{"period": "2024Q2", "reason_code": "gap"}],
                    "completeness_pct": 66.0}

    orch.llm = NamesTheGap()
    await orch._completeness(job)

    recorded = [u.period for u in db.query(UnresolvedQuarterORM).filter_by(job_id=job.id).all()]
    assert recorded == ["2024Q2"]
