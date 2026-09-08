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


@pytest.mark.asyncio
async def test_an_unsettled_conflict_falls_through_to_the_ranking(tmp_path):
    """A conflict the model declines to settle must not demote everything.

    `conflicts` entries carry `candidate_ids` and a `winner_id`. With no
    winner named, every id in the entry was marked a loser, and the
    source-priority fallback then skipped the group because it already
    contained losers - so a schedule reading 54.0 and a sentence reading 13.4
    were both withheld and the quarter went unanswered. Measured once in a
    sample of 32: Orenitram 2019Q2.
    """
    db, orch, job, table_row = _job(tmp_path, revenue_scope="Product family", formulation=None)
    prose_row = DatapointORM(
        id=new_id(), job_id=job.id, source_id="s1", period="2019Q3",
        value_reported=13.4, value_normalized_usd_millions=13.4,
        currency="USD", unit="millions", period_type="quarterly",
        revenue_scope="Product family", formulation=None,
        source_url="https://example.invalid/ex99.htm",
        source_quote="quantities sold decreased by $13.4 million",
        extraction_method="prose", confidence_score=0.75,
        validation_status=ValidationStatus.PENDING.value,
        citation_json={"source_type": "earnings_release"}, issue_flags=[],
    )
    table_row.citation_json = {"source_type": "earnings_release"}
    db.add(prose_row)
    db.commit()

    class _Undecided:
        """A reconciler that reports the disagreement and picks no winner."""

        async def reconcile(self, **_kwargs):
            return {"conflicts": [{"candidate_ids": [table_row.id, prose_row.id]}]}

    orch.llm = _Undecided()
    await orch._reconcile_with_llm(job, [table_row, prose_row])

    assert prose_row.validation_status == ValidationStatus.NEEDS_REVIEW.value
    assert table_row.validation_status != ValidationStatus.NEEDS_REVIEW.value, (
        "the schedule is the stronger claim and must survive the fallback"
    )


def test_a_schedule_outranks_a_sentence_from_the_same_exhibit():
    """SOURCE_PRIORITY ranks documents; it cannot separate two readers of one.

    An 8-K exhibit carries both a product-sales schedule and narrative around
    it, so both candidates are `earnings_release` and the source ranking leaves
    them tied. The tie was then broken by extraction order.
    """
    from app.pipeline.orchestrator import claim_rank

    assert claim_rank("xbrl_fact") < claim_rank("table")
    assert claim_rank("table") < claim_rank("derived_from_period_total")
    assert claim_rank("derived_from_period_total") < claim_rank("prose")
    assert claim_rank("table") < claim_rank("prose")
    # An unknown producer ranks last rather than first.
    assert claim_rank(None) > claim_rank("prose")


def test_a_figure_and_its_normalisation_that_disagree_cannot_publish():
    """Every check reads `value_reported`; a consumer reads the normalized one.

    Remodulin 2009Q3 arrived from the model reported as 87.4 with 87,400 beside
    it as USD millions, and was published carrying
    `deterministic:product_quote_value_ok` - because the judge confirms 87.4
    against a quote that says 87.4, and nothing anywhere looked at the figure
    that reaches a reader. Gold is 87.4.

    The candidate's own two numbers are enough to catch it: 87.4 in millions is
    87.4, not 87,400.
    """
    from app.pipeline.orchestrator import _scale_disagrees

    # The real row.
    assert _scale_disagrees(87.4, 87400.0, "millions", "USD")
    # The ordinary cases stay silent.
    assert not _scale_disagrees(87.4, 87.4, "millions", "USD")
    assert not _scale_disagrees(1514.0, 1.514, "thousands", "USD")
    assert not _scale_disagrees(2.5, 2500.0, "billions", "USD")
    # Rounding is not a scaling error.
    assert not _scale_disagrees(87.4, 87.41, "millions", "USD")
    # An exchange rate this function does not model must not be called one.
    assert not _scale_disagrees(87.4, 95.2, "millions", "CHF")
    # Missing figures are somebody else's problem.
    assert not _scale_disagrees(None, 87400.0, "millions", "USD")
    assert not _scale_disagrees(87.4, None, "millions", "USD")


@pytest.mark.asyncio
async def test_the_gate_holds_a_row_whose_figures_disagree(tmp_path):
    """The flag has to reach the status, not just sit in the issue list.

    The judge answers "supported" here, because the quote does carry the
    as-reported figure - that is precisely why this row published.
    """
    db, orch, job, row = _job(tmp_path, revenue_scope="Product family", formulation=None)
    row.issue_flags = ["normalization_disagrees_with_unit"]
    db.commit()

    await orch._judge(job, [row], [], {}, {})

    assert row.source_support == "supported", "the quote does support 121.0"
    assert row.validation_status == ValidationStatus.NEEDS_REVIEW.value, (
        "a row whose own two figures cannot both be right must not publish, "
        "however well the quote supports the one the judge reads"
    )
