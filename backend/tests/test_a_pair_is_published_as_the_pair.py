"""A line covering two products is the pair's figure, published as the pair.

A filer that sells two products together prints one line for both. The figure
is real and it is the sum; what nobody publishes is the split. The literature
on combination regimens has no agreed way to divide one, and the companies
report the pair, so the pair is the unit an analyst works with.

The reader used to hold such a row for a person, which left the figure in a
queue where the reviewer had no more to go on than the reader did, and left
the quarter looking like a gap. It is now published under a name that says
what it covers, and the product that was asked for gets an unresolved quarter
saying its own figure is not disclosed.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    DatapointORM,
    DrugJobORM,
    ExtractionRunORM,
    UnresolvedQuarterORM,
)
from app.domain.models import REPORTED_WITH_ANOTHER_PRODUCT, new_id
from app.pipeline.orchestrator import PipelineOrchestrator, reported_as_for
from app.storage.filestore import LocalFileStore


def test_the_name_says_what_the_figure_covers():
    assert reported_as_for("Calderon", {"combined_with": ["NuVessa"]}) == "Calderon + NuVessa"
    assert reported_as_for("Calderon", {"combined_with": ["NuVessa", "Veltrexa"]}) == (
        "Calderon + NuVessa + Veltrexa"
    )
    assert reported_as_for("Calderon", {}) is None, "a product's own row is not reported as anything else"


def test_one_printed_line_gets_one_name_whichever_product_was_asked_for():
    """The name is the filer's, so the pair does not change identity with the
    question. A row printed "NuVessa / Calderon" is the NuVessa + Calderon
    line, asked about from either side."""
    row = {"combined_with": ["NuVessa"], "source_quote": "NuVessa / Calderon 100.0 90.0"}
    mirrored = {"combined_with": ["Calderon"], "source_quote": "NuVessa / Calderon 100.0 90.0"}
    assert reported_as_for("Calderon", row) == "NuVessa + Calderon"
    assert reported_as_for("NuVessa", mirrored) == "NuVessa + Calderon"

    # A quote that does not print a sibling cannot order it; it keeps its place
    # behind the names the quote does carry, rather than sorting to the front.
    partial = {"combined_with": ["Veltrexa", "NuVessa"], "source_quote": "NuVessa / Calderon 100.0"}
    assert reported_as_for("Calderon", partial) == "NuVessa + Calderon + Veltrexa"


def _job(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(ExtractionRunORM(id="run", status="running", options_json={}))
    job = DrugJobORM(id=new_id(), run_id="run", drug_name="Calderon", manufacturer="Acme Pharma",
                     status="running", quality_flags=[])
    db.add(job); db.commit()
    return PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path))), db, job


def _row(job, period, value, *, reported_as=None):
    return DatapointORM(
        id=new_id(), job_id=job.id, period=period, period_type="quarterly",
        value_normalized_usd_millions=value, revenue_scope="Product family",
        reported_as=reported_as, source_url="https://example.invalid/10q.htm",
        source_quote=f"Calderon and NuVessa {value}", validation_status="auto_pass",
    )


def test_a_quarter_only_reported_as_a_pair_says_so(tmp_path):
    orch, db, job = _job(tmp_path)
    rows = [_row(job, "2025Q1", 21.0, reported_as="Calderon + NuVessa"),
            _row(job, "2025Q2", 25.8, reported_as="Calderon + NuVessa")]
    db.add_all(rows); db.commit()

    orch._record_quarters_only_reported_with_another_product(job, rows)

    recorded = db.query(UnresolvedQuarterORM).filter_by(job_id=job.id).all()
    assert sorted(u.period for u in recorded) == ["2025Q1", "2025Q2"]
    reason = recorded[0].reason_unresolved
    assert reason.startswith(f"[{REPORTED_WITH_ANOTHER_PRODUCT}]")
    assert "Calderon + NuVessa" in reason
    assert recorded[0].sources_checked == ["https://example.invalid/10q.htm"]


def test_a_quarter_with_the_products_own_figure_says_nothing(tmp_path):
    orch, db, job = _job(tmp_path)
    rows = [_row(job, "2025Q1", 21.0, reported_as="Calderon + NuVessa"),
            _row(job, "2025Q1", 12.0)]
    db.add_all(rows); db.commit()

    orch._record_quarters_only_reported_with_another_product(job, rows)

    assert db.query(UnresolvedQuarterORM).filter_by(job_id=job.id).count() == 0, (
        "the issuer disclosed the product on its own for that quarter"
    )


def test_a_quarter_already_spoken_for_is_not_spoken_for_twice(tmp_path):
    orch, db, job = _job(tmp_path)
    rows = [_row(job, "2025Q1", 21.0, reported_as="Calderon + NuVessa")]
    db.add_all(rows)
    db.add(UnresolvedQuarterORM(id=new_id(), job_id=job.id, period="2025Q1",
                                reason_unresolved="[gap] already recorded",
                                sources_checked=[], recommended_next_step="",
                                confidence_that_unavailable=0.3))
    db.commit()

    orch._record_quarters_only_reported_with_another_product(job, rows)

    assert db.query(UnresolvedQuarterORM).filter_by(job_id=job.id).count() == 1
