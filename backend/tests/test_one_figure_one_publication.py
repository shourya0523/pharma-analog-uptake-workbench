"""One figure, one publication.

A filer tags a product's quarter in thousands and prints it in millions with
one decimal, and the pipeline read both: 427.623 from the instance, 427.6
from the schedule. Both were published, and a caller was handed two figures
for one quarter that were the same figure at two precisions. The lower-tier
reading now corroborates the published one - cited beside it, not published
as a second figure and not held as a conflict it never was.

The same function also settles which filing states a period: the one that
reports it, not a later filing's comparative of it, which is what the issuer
later said about the period and may have been recast since.

Invented names: Calderon, acme:. Sources are dated to say which filing
reports the period.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    DatapointORM,
    DrugJobORM,
    ExtractionRunORM,
    SourceDocumentORM,
)
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


def _point(job, value, *, method, scope="Product family", source_type="quarterly_report",
           precision=None, source_id=None, period="2021Q4"):
    return DatapointORM(
        id=new_id(), job_id=job.id, source_id=source_id, period=period, period_type="quarterly",
        value_reported=value, value_normalized_usd_millions=value,
        currency="USD", unit="millions", revenue_scope=scope,
        extraction_method=method, confidence_score=0.9,
        source_url=f"https://example.invalid/{method}",
        validation_status=ValidationStatus.AUTO_PASS.value, issue_flags=[],
        source_quote=f"Calderon {value}",
        citation_json={"source_type": source_type,
                       **({"rounding_uncertainty_usd_millions": precision} if precision is not None else {})},
    )


def _reconcile(monkeypatch, db, job, rows):
    orch = PipelineOrchestrator(db, file_store=None)

    async def no_opinion(**_):
        return {"resolved": [], "conflicts": []}

    monkeypatch.setattr(orch.llm, "reconcile", no_opinion)
    db.add_all(rows); db.commit()
    asyncio.run(orch._reconcile_with_llm(job, rows))
    return {row.extraction_method: row for row in db.query(DatapointORM).all()}


def test_a_tagged_fact_in_thousands_and_a_printed_figure_in_millions_publish_once(monkeypatch):
    db, job = _job()
    rows = [
        _point(job, 427.623, method="xbrl_fact", precision=0.0005),
        _point(job, 427.6, method="table", precision=0.05),
    ]
    got = _reconcile(monkeypatch, db, job, rows)
    assert got["xbrl_fact"].validation_status == "auto_pass"
    assert got["table"].validation_status == "corroborates"
    assert "corroborates_published_figure" in got["table"].issue_flags
    cited = got["xbrl_fact"].citation_json["corroborated_by"]
    assert [c["extraction_method"] for c in cited] == ["table"]
    assert cited[0]["value_normalized_usd_millions"] == 427.6


def test_a_lower_tier_figure_that_disagrees_is_still_a_conflict(monkeypatch):
    db, job = _job()
    rows = [
        _point(job, 427.623, method="xbrl_fact", precision=0.0005),
        _point(job, 431.0, method="table", precision=0.05),
    ]
    got = _reconcile(monkeypatch, db, job, rows)
    assert got["xbrl_fact"].validation_status == "auto_pass"
    assert got["table"].validation_status == "needs_review"
    assert "conflict_with_higher_priority_source" in got["table"].issue_flags
    assert "corroborated_by" not in got["xbrl_fact"].citation_json


def test_two_printed_figures_that_disagree_are_both_held(monkeypatch):
    db, job = _job()
    rows = [
        _point(job, 427.6, method="table", precision=0.05),
        _point(job, 431.0, method="table", precision=0.05),
    ]
    got = _reconcile(monkeypatch, db, job, rows)
    assert {row.validation_status for row in got.values()} == {"needs_review"}


def test_worldwide_and_product_family_are_the_same_figure(monkeypatch):
    """A sentence says Worldwide and a schedule says the family; grouped apart,
    one quarter was published twice."""
    db, job = _job()
    rows = [
        _point(job, 138.347, method="table", scope="Product family"),
        _point(job, 138.3, method="llm", scope="Worldwide"),
    ]
    got = _reconcile(monkeypatch, db, job, rows)
    assert got["table"].validation_status == "auto_pass"
    assert got["llm"].validation_status == "corroborates"


def test_the_filing_that_reports_the_period_outranks_a_later_comparative(monkeypatch):
    """A later filing's comparative is what the issuer later said about the
    period - restated, if anything was recast - and is held as such."""
    db, job = _job()
    own = SourceDocumentORM(id=new_id(), job_id=job.id, source_type="quarterly_report",
                            source_url="https://example.invalid/own", source_date="2021-08-05",
                            retrieval_status="success")
    later = SourceDocumentORM(id=new_id(), job_id=job.id, source_type="quarterly_report",
                              source_url="https://example.invalid/later", source_date="2022-08-04",
                              retrieval_status="success")
    db.add_all([own, later]); db.commit()
    rows = [
        _point(job, 168.1, method="table", source_id=later.id, period="2021Q2"),
        _point(job, 164.8, method="table", source_id=own.id, period="2021Q2"),
    ]
    _reconcile(monkeypatch, db, job, rows)
    got = {row.value_normalized_usd_millions: row for row in db.query(DatapointORM).all()}
    assert got[164.8].validation_status == "auto_pass"
    assert got[168.1].validation_status == "needs_review"
    assert "restated_in_later_filing" in got[168.1].issue_flags


def test_the_gate_does_not_republish_a_row_an_earlier_stage_decided():
    """The gate preserved a hand-written list of statuses.

    A status added after that list was written - a second reading of a
    figure already published - was not on it, so the row fell through to
    "pending and clean, so auto_pass" and was published a second time under
    its own value, which is the double publication this change removes.
    """
    from app.quality.checks import apply_auto_pass_gate

    def gated(status: str) -> str:
        return apply_auto_pass_gate(
            {"id": "d", "source_url": "https://example.invalid/10q", "source_quote": "Calderon 10",
             "period_type": "quarterly", "revenue_scope": "Product family",
             "confidence_score": 0.9, "validation_status": status},
            [],
        )

    decided = [s.value for s in ValidationStatus if s is not ValidationStatus.PENDING]
    assert [gated(s) for s in decided] == decided, (
        "every status but pending is a decision an earlier stage made"
    )
    assert gated(ValidationStatus.PENDING.value) == ValidationStatus.AUTO_PASS.value
