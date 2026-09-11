"""The derivation must fire in the pipeline, not only in the eval.

`test_capabilities_are_wired.py` checks that `complete_series` has a caller
under `app/`. It has one, and having a caller is not the same as running: the
pipeline emitted table, model and prose readings and never a derived one, while
the coverage eval credited derivations for rows the pipeline had not produced.

The cause was one default. `_extract_revenue` called
`extract_revenue_candidates` without `quarterly_only`, a parameter that then
defaulted to True, so only quarterly points were ever stored — and
`complete_series` derives a missing quarter by subtracting the quarters it has
from a *total* it no longer received. Asked for quarters only, it derived
nothing, every time.

That parameter has since been removed rather than re-defaulted, because it
duplicated a decision the orchestrator has to make anyway; see
`test_totals_reach_the_derivation.py`. This test stays, because it checks the
outcome — a derived row actually reaching the stage — and not the mechanism
that happened to break it.

A grep cannot see that. This drives the stage and looks at what came out.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DrugJobORM, ExtractionRunORM
from app.domain.models import (
    ParsedDocument,
    ParsingStatus,
    RetrievalStatus,
    RetrievedSource,
    SourceType,
    new_id,
)
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import LocalFileStore

# A 10-Q's own shape: the quarter, and the year so far. Q1 is never printed on
# its own and is the difference — 21,465 = 81,272 - 31,352 - 28,455.
#
# The derivation refuses when more than one quarter of a total is missing, so a
# table stating only Q3 against the nine months derives nothing and should: two
# unknowns, one equation. Q2 is what makes Q1 determined.
TABLE = [
    [
        "",
        "Three Months Ended June 30, 2005",
        "Three Months Ended September 30, 2005",
        "Nine Months Ended September 30, 2005",
    ],
    ["Remodulin", "28,455", "31,352", "81,272"],
]
CAPTION = "Revenues (in thousands)"


def _orchestrator(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Remodulin",
                     manufacturer="United Therapeutics", status="running", quality_flags=[])
    db.add(job)
    db.commit()
    return db, PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path))), job


@pytest.mark.asyncio
async def test_a_quarter_the_issuer_left_implicit_is_derived(tmp_path):
    db, orch, job = _orchestrator(tmp_path)
    source = RetrievedSource(
        source_id="s1", source_type=SourceType.SEC_FILING,
        url="https://example.invalid/10q.htm", filing_type="10-Q",
        retrieval_status=RetrievalStatus.SUCCESS,
    )
    parsed = {
        "s1": ParsedDocument(
            source_id="s1", text_blocks=[CAPTION], tables=[TABLE],
            table_captions=[CAPTION], parsing_status=ParsingStatus.SUCCESS,
        )
    }

    rows = await orch._extract_revenue(job, [source], parsed, {"quarterly_revenue": True})

    methods = {row.extraction_method for row in rows}
    assert any(m.startswith("derived") for m in methods), (
        f"the derivation produced nothing; stored methods were {sorted(methods)}"
    )
    derived = [r for r in rows if r.extraction_method.startswith("derived")]
    assert {r.period for r in derived} == {"2005Q1"}
    assert derived[0].value_normalized_usd_millions == pytest.approx(21.465, abs=0.01)


@pytest.mark.asyncio
async def test_the_total_it_subtracted_from_is_not_itself_published(tmp_path):
    """Totals are what a quarter is subtracted from, not answers to the question."""
    db, orch, job = _orchestrator(tmp_path)
    source = RetrievedSource(
        source_id="s1", source_type=SourceType.SEC_FILING,
        url="https://example.invalid/10q.htm", filing_type="10-Q",
        retrieval_status=RetrievalStatus.SUCCESS,
    )
    parsed = {
        "s1": ParsedDocument(
            source_id="s1", text_blocks=[CAPTION], tables=[TABLE],
            table_captions=[CAPTION], parsing_status=ParsingStatus.SUCCESS,
        )
    }

    rows = await orch._extract_revenue(job, [source], parsed, {"quarterly_revenue": True})

    assert rows, "the quarters themselves must still be stored"
    assert all(r.period_type == "quarterly" for r in rows), (
        "a nine-month total is not a quarterly datapoint and must not be stored "
        "as one: " + repr([(r.period, r.period_type) for r in rows])
    )
