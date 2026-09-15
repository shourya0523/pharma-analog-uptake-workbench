"""A reconciler verdict that names something other than a candidate.

`_reconcile_with_llm` asks a model which of several claims for one period is
the figure, and the reply names a winner by the candidate's id. The reply is
free text: a model that is shown `id` beside `value_reported` may answer with
the figure, and the string went into `winners` unchecked. Most of the function
only ever asks `row.id in winners`, where a name no row answers to is merely
absent - but the flag that tells a restatement from a plain conflict indexes
`winners` by key, and there the same string ended the job.

Both shapes of reply are exercised: one naming a candidate, which the model
settles, and one naming a non-candidate, which it does not.

Invented names: Calderon, Acme Pharma. Sources are dated so that one filing
reports the quarter and the other prints it as a comparative a year later.
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


def _source(db, job, filed):
    src = SourceDocumentORM(
        id=new_id(), job_id=job.id, source_type="quarterly_report",
        source_url=f"https://example.invalid/{filed}", source_date=filed,
        accession_number=f"0000000000-{filed}", retrieval_status="fetched",
        metadata_json={},
    )
    db.add(src); db.commit()
    return src.id


def _point(job, value, *, method, source_id, period="2021Q4"):
    return DatapointORM(
        id=new_id(), job_id=job.id, source_id=source_id, period=period,
        period_type="quarterly", value_reported=value,
        value_normalized_usd_millions=value, currency="USD", unit="millions",
        revenue_scope="Product family", extraction_method=method,
        confidence_score=0.9, source_url=f"https://example.invalid/{method}",
        validation_status=ValidationStatus.AUTO_PASS.value, issue_flags=[],
        source_quote=f"Calderon {value}",
        citation_json={"source_type": "quarterly_report"},
    )


def _rows(db, job):
    """One filing reporting the quarter, one printing it a year later."""
    rows = [
        _point(job, 483.3, method="xbrl_fact", source_id=_source(db, job, "2022-02-15")),
        _point(job, 470.0, method="table", source_id=_source(db, job, "2023-02-15")),
    ]
    db.add_all(rows); db.commit()
    return rows


def _reconcile(db, job, rows, reply):
    orch = PipelineOrchestrator(db, file_store=None)

    async def answers(**_):
        return reply

    orch.llm.reconcile = answers
    asyncio.run(orch._reconcile_with_llm(job, rows))
    return {row.extraction_method: row for row in db.query(DatapointORM).all()}


def test_a_verdict_naming_the_figure_instead_of_the_candidate_leaves_it_to_the_ranking():
    db, job = _job()
    rows = _rows(db, job)
    got = _reconcile(db, job, rows, {
        "resolved": [{"winner_id": "483.3"}], "conflicts": [],
    })
    assert got["xbrl_fact"].validation_status == "auto_pass", (
        "the filing that reports the quarter is published"
    )
    assert got["table"].validation_status == "needs_review"
    assert "restated_in_later_filing" in got["table"].issue_flags


def test_a_conflict_whose_winner_is_not_a_candidate_demotes_nobody():
    db, job = _job()
    rows = _rows(db, job)
    got = _reconcile(db, job, rows, {
        "resolved": [],
        "conflicts": [{"candidate_ids": [r.id for r in rows], "winner_id": "483.3"}],
    })
    assert got["xbrl_fact"].validation_status == "auto_pass", (
        "a winner nobody answers to is no verdict, so the group is ranked "
        "rather than demoted whole"
    )


def test_a_verdict_that_names_a_candidate_is_the_verdict():
    db, job = _job()
    rows = _rows(db, job)
    later = next(r for r in rows if r.extraction_method == "table")
    got = _reconcile(db, job, rows, {
        "resolved": [], "conflicts": [
            {"candidate_ids": [r.id for r in rows], "winner_id": later.id},
        ],
    })
    assert got["table"].validation_status == "auto_pass", (
        "the model settled the group, against what the ranking would have said"
    )
    assert got["xbrl_fact"].validation_status == "needs_review"
