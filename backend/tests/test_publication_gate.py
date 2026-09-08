"""A row the judge called supported must be publishable, and this one was not.

The pipeline's readers find the great majority of the corpus. Its published
output was a third of it, and the whole of the difference was one rule.

`deterministic_formulation_fill` writes "aggregate" into `formulation` when
`revenue_scope` is already "Product family". That restates the scope; it
estimates nothing and cannot be wrong. It went through `apply_field_enrichment`
anyway, whose contract - any applied fill forces needs_review and caps
confidence at 0.55 - is the correct contract for a model's guess at a blank
field. The row was then disqualified from auto_pass twice: by the flag, and by
a confidence the quality gate's 0.7 floor rejects.

Measured over four products, two issuers and two years before the fix: 27 of
the 41 quarterly datapoints landing on a gold quarter carried
`field_enrichment_applied`, for this fill alone in 26 of them; 21 of those had
been judged "supported" with nothing else against them and none was published.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DatapointORM, DrugJobORM, ExtractionRunORM
from app.domain.formulations import AGGREGATE_FORMULATION
from app.domain.models import ValidationStatus, new_id
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import LocalFileStore

# A quote naming the product and carrying the value, for a quarter, at product
# scope: `try_deterministic_judgment` answers "supported" on this without an
# LLM, so the test is offline and the gate is the only thing under test.
QUOTE = "Tyvaso | 121.0 | 96.0"


def _job(tmp_path, **datapoint):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Tyvaso",
                     manufacturer="United Therapeutics", status="running", quality_flags=[])
    db.add(job)
    row = DatapointORM(
        id=new_id(), job_id=job.id, source_id="s1", period="2019Q3",
        value_reported=121.0, value_normalized_usd_millions=121.0,
        currency="USD", unit="millions", period_type="quarterly",
        source_url="https://example.invalid/ex99.htm", source_quote=QUOTE,
        extraction_method="table", confidence_score=0.75,
        validation_status=ValidationStatus.PENDING.value,
        citation_json={"source_url": "https://example.invalid/ex99.htm"},
        issue_flags=[], **datapoint,
    )
    db.add(row)
    db.commit()
    return db, PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path))), job, row


@pytest.mark.asyncio
async def test_the_aggregate_formulation_default_does_not_block_publication(tmp_path):
    """Product-family scope, no formulation: filled in, and still publishable."""
    db, orch, job, row = _job(tmp_path, revenue_scope="Product family", formulation=None)

    await orch._judge(job, [row], [], {}, {})

    assert row.formulation == AGGREGATE_FORMULATION, "the default is still applied"
    assert "field_enrichment_applied" not in (row.issue_flags or []), (
        "restating the row's own scope is not an estimate and must not be "
        "flagged as one - the flag blocks auto_pass and caps confidence"
    )
    assert row.source_support == "supported"
    assert row.validation_status == ValidationStatus.AUTO_PASS.value

    await orch._quality_and_validation(job)
    assert row.validation_status == ValidationStatus.AUTO_PASS.value, (
        "confidence must clear the quality gate's 0.7 floor, which the "
        "enrichment cap of 0.55 did not"
    )


@pytest.mark.asyncio
async def test_a_formulation_the_document_stated_is_left_alone(tmp_path):
    """The default fills a blank. It never overwrites what was read."""
    db, orch, job, row = _job(tmp_path, revenue_scope="Formulation-specific", formulation="nebulized")

    await orch._judge(job, [row], [], {}, {})

    assert row.formulation == "nebulized"
    assert row.validation_status == ValidationStatus.AUTO_PASS.value
