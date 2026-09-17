"""A period label is not a period: the span belongs in the key.

`2023Q3` names the quarter and, on a year-to-date row, the nine months that
end with it. Reconciliation grouped on the label alone, so the two spans met
in one group as rival claims about one figure - and the longer one, being
larger, was either published for the quarter or held it as a conflict.

Invented names: Calderon, Nebulized Calderon.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DatapointORM, DrugJobORM, ExtractionRunORM
from app.domain.models import ValidationStatus, new_id
from app.pipeline.orchestrator import PipelineOrchestrator


def _job():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon",
                     manufacturer="Acme Pharma", status="running", quality_flags=[])
    db.add_all([run, job]); db.commit()
    return db, job


def _row(job, value, *, period_type, method="llm", period="2023Q3"):
    return DatapointORM(
        id=new_id(), job_id=job.id, source_id=None, period=period,
        period_type=period_type, value_reported=value,
        value_normalized_usd_millions=value, currency="USD", unit="millions",
        revenue_scope="Product family", extraction_method=method,
        confidence_score=0.9, source_url="https://example.invalid/10q",
        validation_status=ValidationStatus.AUTO_PASS.value, issue_flags=[],
        source_quote=f"Calderon {value}",
        citation_json={"source_type": "quarterly_report"},
    )


def _reconcile(monkeypatch, db, job, rows):
    orch = PipelineOrchestrator(db, file_store=None)

    async def no_opinion(**_):
        return {"resolved": [], "conflicts": []}

    monkeypatch.setattr(orch.llm, "reconcile", no_opinion)
    db.add_all(rows); db.commit()
    asyncio.run(orch._reconcile_with_llm(job, rows))
    return {row.id: row for row in db.query(DatapointORM).all()}


def test_a_nine_month_total_does_not_contest_the_quarter_it_ends(monkeypatch):
    db, job = _job()
    quarter = _row(job, 36.393, period_type="quarterly", method="xbrl_fact")
    ytd = _row(job, 98.8, period_type="nine_month")
    got = _reconcile(monkeypatch, db, job, [quarter, ytd])
    assert got[quarter.id].validation_status == ValidationStatus.AUTO_PASS.value
    assert got[ytd.id].validation_status == ValidationStatus.AUTO_PASS.value
    assert not (got[quarter.id].issue_flags or [])
    assert not (got[ytd.id].issue_flags or [])


def test_two_readings_of_one_span_are_still_one_group(monkeypatch):
    """The key gains a field; it does not stop grouping what belongs together."""
    db, job = _job()
    tagged = _row(job, 36.393, period_type="quarterly", method="xbrl_fact")
    printed = _row(job, 36.4, period_type="quarterly", method="table")
    got = _reconcile(monkeypatch, db, job, [tagged, printed])
    assert got[tagged.id].validation_status == ValidationStatus.AUTO_PASS.value
    assert got[printed.id].validation_status == ValidationStatus.CORROBORATES.value
