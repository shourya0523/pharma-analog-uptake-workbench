"""A reply object whose fields are not the types the fields are read as.

The completeness stage asks which quarters are missing and why. An entry is an
object carrying a `reason_code` that keys a table of standard reasons, and two
sentences the unresolved-quarter row stores. Each is read as text, and the
reply is free to put a number, an array or an object there instead.

The two failures differ in where they surface. A non-string code ends the job
at `.lower()`, on the line that reads it. A non-string sentence travels: it
reaches the row, and the text column refuses it at the next flush, inside
whatever query happened to trigger that flush - which is where the traceback
then points.

Both shapes now mean what an absent field means, so a well-formed entry beside
a malformed one is still recorded with the reason the model gave.

Invented names: Calderon, Acme Pharma.
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
    UnresolvedQuarterORM,
)
from app.domain.models import ValidationStatus, new_id
from app.pipeline.orchestrator import PipelineOrchestrator


def _completeness(missing_periods):
    """Run the stage over one known quarter and the model's list of gaps."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon",
                     manufacturer="Acme Pharma", status="running", quality_flags=[])
    db.add_all([run, job])
    db.add(DatapointORM(
        id=new_id(), job_id=job.id, source_id=None, period="2021Q1",
        period_type="quarterly", value_reported=10.0,
        value_normalized_usd_millions=10.0, currency="USD", unit="millions",
        revenue_scope="Product family", extraction_method="table",
        confidence_score=0.9, source_url="https://example.invalid/a",
        validation_status=ValidationStatus.AUTO_PASS.value, issue_flags=[],
        source_quote="Calderon 10.0", citation_json={},
    ))
    db.commit()

    orch = PipelineOrchestrator(db, file_store=None)

    async def reply(**_):
        return {"missing_periods": missing_periods, "completeness_pct": 50}

    orch.llm.completeness = reply
    asyncio.run(orch._completeness(job))
    return {row.period: row for row in db.query(UnresolvedQuarterORM).all()}


def test_a_code_that_is_not_text_is_no_code():
    for not_a_code in (7, ["need_filing"], {"code": "need_filing"}, True):
        rows = _completeness([{"period": "2021Q2", "reason_code": not_a_code}])
        assert rows["2021Q2"].reason_unresolved.startswith("[gap]"), (
            f"{not_a_code!r} names none of the standard reasons"
        )
        assert rows["2021Q2"].confidence_that_unavailable == 0.4


def test_a_sentence_that_is_not_text_does_not_reach_the_row():
    rows = _completeness([{
        "period": "2021Q2",
        "recommended_next_step": {"do": "read the quarterly report"},
        "reason": ["not disclosed"],
    }])
    assert isinstance(rows["2021Q2"].recommended_next_step, str)
    assert rows["2021Q2"].recommended_next_step.startswith("Review SEC")
    assert rows["2021Q2"].reason_unresolved == (
        "[gap] Missing quarter — analyst follow-up required"
    ), "the standard reason stands in, rather than an object printed into the text"


def test_an_entry_the_model_stated_is_recorded_as_the_model_stated_it():
    rows = _completeness([{
        "period": "2021Q2",
        "reason_code": "NEED_FILING",
        "reason": "Disclosed in an 8-K/A not yet retrieved",
        "recommended_next_step": "Fetch the 8-K/A",
    }])
    row = rows["2021Q2"]
    assert row.reason_unresolved == "[need_filing] Disclosed in an 8-K/A not yet retrieved", (
        "a code stated in capitals is still that code"
    )
    assert row.recommended_next_step == "Fetch the 8-K/A"
    assert row.confidence_that_unavailable == 0.35, (
        "the confidence comes from the code the model gave, not from the default"
    )


def test_a_field_left_blank_falls_back_like_one_that_is_not_text():
    rows = _completeness([{"period": "2021Q2", "reason_code": "  ", "reason": ""}])
    assert rows["2021Q2"].reason_unresolved == (
        "[gap] Missing quarter — analyst follow-up required"
    )
