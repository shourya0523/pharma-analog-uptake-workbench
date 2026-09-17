"""A row held for what its label said is not published by what its quote says.

`_judge` demotes a row carrying a label flag to needs_review, records the flag
in the issues, and then re-decides the status from the support classification.
That second decision did not consult the flags, so the demotion was undone in
the same pass and the row published.

The two are answers to different questions. "Supported" says the figure is in
the text cited for it, which a quote can settle. A label flag says the figure
may not be this product's own, or may cover part of the period, which a quote
cannot settle at all.

Invented names: Calderon, NuVessa.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DatapointORM, DrugJobORM, ExtractionRunORM
from app.domain.models import ValidationStatus, new_id
from app.parsing.labels import FLAG_COMBINED
from app.pipeline.orchestrator import LABEL_FLAGS, PipelineOrchestrator
from app.storage.filestore import LocalFileStore

# A quote naming the product and carrying the value, for a quarter, at product
# scope: the deterministic judgment answers "supported" on this without a
# model, so the gate is the only thing under test.
QUOTE = "Calderon and NuVessa | 19.843 | 17.2"


def _job(tmp_path, flags):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon",
                     manufacturer="Acme Pharma", status="running", quality_flags=[])
    row = DatapointORM(
        id=new_id(), job_id=job.id, source_id="s1", period="2025Q4",
        value_reported=19.843, value_normalized_usd_millions=19.843,
        currency="USD", unit="millions", period_type="quarterly",
        revenue_scope="Product family", formulation="aggregate",
        source_url="https://example.invalid/10q.htm", source_quote=QUOTE,
        extraction_method="table", confidence_score=0.75,
        validation_status=ValidationStatus.PENDING.value,
        citation_json={"source_url": "https://example.invalid/10q.htm"},
        issue_flags=list(flags),
    )
    db.add_all([run, job, row])
    db.commit()
    return db, PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path))), job, row


@pytest.mark.asyncio
async def test_a_combined_line_is_not_published_by_its_quote(tmp_path):
    db, orch, job, row = _job(tmp_path, [FLAG_COMBINED])

    await orch._judge(job, [row], [], {}, {})

    assert row.source_support == "supported", "the quote does carry the figure"
    assert row.validation_status == ValidationStatus.NEEDS_REVIEW.value
    assert f"label:{FLAG_COMBINED}" in (row.issue_flags or [])


@pytest.mark.asyncio
async def test_the_same_row_without_the_flag_still_publishes(tmp_path):
    """The gate is the flag, not the quote or the scope."""
    db, orch, job, row = _job(tmp_path, [])

    await orch._judge(job, [row], [], {}, {})

    assert row.validation_status == ValidationStatus.AUTO_PASS.value


@pytest.mark.asyncio
async def test_every_label_flag_holds_the_row(tmp_path):
    """Whatever the vocabulary grows to, each member of it holds a row.

    Asked over `LABEL_FLAGS` itself rather than over a list written here, so a
    flag added to the vocabulary is covered without this test being edited.
    """
    async def supported(**_):
        return {"support_classification": "supported",
                "validation_status": ValidationStatus.AUTO_PASS.value, "issues": []}

    for flag in sorted(LABEL_FLAGS):
        db, orch, job, row = _job(tmp_path, [flag])
        # The most favourable answer a judge can give, so nothing but the flag
        # can be what holds the row. A flag the deterministic judgment does
        # not know falls through to this instead of to the network.
        orch.llm.judge = supported
        await orch._judge(job, [row], [], {}, {})
        assert row.validation_status != ValidationStatus.AUTO_PASS.value, flag
