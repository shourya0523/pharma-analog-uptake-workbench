"""A derived quarter is a claim about the figures it subtracted.

It inherits their identity - the product, the region, the line of the
schedule - and it was read out of the documents they were read out of. The
path from a stored row back into the arithmetic ran through eight keys, and
everything else was dropped there: the pair a combined line was reported as,
the region, the route, and the precision the source declared. What came out
the other side asserted the family's identity at "Product family", cited
whichever document sorted first, and stated its figure as though it were
exact.

Invented names: Calderon, NuVessa, acme:.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    DatapointORM,
    DerivationLineageORM,
    DrugJobORM,
    EvidenceAssertionORM,
    ExtractionRunORM,
)
from app.domain.models import (
    RetrievalStatus,
    RetrievedSource,
    SourceType,
    ValidationStatus,
    new_id,
)
from app.extraction.derive import HELD_FOR_BOUND, complete_series
from app.pipeline.orchestrator import PipelineOrchestrator

ANNUAL = "https://example.invalid/acme-10k.htm"
QUARTERLY = "https://example.invalid/acme-10q.htm"


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon",
                     manufacturer="Acme Pharma", status="running", quality_flags=[])
    db.add_all([run, job]); db.commit()
    return db, job


def _row(job, period, value, *, period_type="quarterly", source_id="q", url=QUARTERLY,
         bound=None, **fields):
    row = DatapointORM(
        id=new_id(), job_id=job.id, source_id=source_id, period=period,
        period_type=period_type, value_reported=value,
        value_normalized_usd_millions=value, currency="USD", unit="millions",
        revenue_scope="Product family", extraction_method="table",
        confidence_score=0.75, source_url=url,
        validation_status=ValidationStatus.AUTO_PASS.value, issue_flags=[],
        source_quote=f"Calderon {period} {value}",
        citation_json={"source_type": "sec_filing",
                       **({"rounding_uncertainty_usd_millions": bound} if bound else {})},
        **fields,
    )
    return row


def _source(source_id, url, source_type=SourceType.SEC_FILING):
    return RetrievedSource(
        source_id=source_id, source_type=source_type, url=url, title="",
        retrieval_status=RetrievalStatus.SUCCESS,
        accession_number=f"0000000-24-{source_id}",
    )


def test_a_stored_row_reads_back_as_what_it_said_about_itself():
    db, job = _db()
    row = _row(job, "2024Q1", 19.255, reported_as="Calderon and NuVessa",
               geography="United States", route_of_administration="intravitreal",
               formulation="implant", bound=0.5)
    row.citation_json = {**row.citation_json, "combined_with": ["NuVessa"]}
    candidate = PipelineOrchestrator._candidate_of(row)
    assert candidate["reported_as"] == "Calderon and NuVessa"
    assert candidate["geography"] == "United States"
    assert candidate["route_of_administration"] == "intravitreal"
    assert candidate["revenue_scope"] == "Product family"
    assert candidate["combined_with"] == ["NuVessa"]
    assert candidate["rounding_uncertainty_usd_millions"] == 0.5
    assert candidate["source_url"] == QUARTERLY
    assert candidate["_datapoint_id"] == row.id


def _derived(db, job, *, bound=None, **total_fields):
    """One year's quarters and the year's own total, derived to the fourth."""
    quarters = [
        _row(job, "2024Q1", 10.0, bound=bound),
        _row(job, "2024Q2", 11.0, bound=bound),
        _row(job, "2024Q3", 12.0, bound=bound),
    ]
    total = _row(job, "2024", 46.041, period_type="annual", source_id="k", url=ANNUAL,
                 bound=bound, **total_fields)
    db.add_all([*quarters, total]); db.commit()
    own = [PipelineOrchestrator._candidate_of(r) for r in [*quarters, total]]
    return [c for c in complete_series({"Calderon": own}, product="Calderon")
            if c["period"] == "2024Q4"]


def test_the_derived_quarter_is_read_out_of_the_document_its_total_is_in():
    db, job = _db()
    (candidate,) = _derived(db, job)
    assert candidate["source_url"] == ANNUAL
    assert candidate["source_id"] == "k"
    roles = {term["role"]: term for term in candidate["_inputs"]}
    assert set(roles) == {"period_total", "quarter"}
    assert roles["period_total"]["source_url"] == ANNUAL
    assert {term["source_url"] for term in candidate["_inputs"]} == {ANNUAL, QUARTERLY}


def test_the_derived_quarter_carries_the_identity_of_what_it_subtracted():
    db, job = _db()
    (candidate,) = _derived(
        db, job, reported_as="Calderon and NuVessa", geography="United States",
        route_of_administration="intravitreal",
    )
    assert candidate["reported_as"] == "Calderon and NuVessa"
    assert candidate["geography"] == "United States"
    assert candidate["route_of_administration"] == "intravitreal"


def test_the_bound_survives_the_round_trip_and_reaches_the_quote():
    db, job = _db()
    (candidate,) = _derived(db, job, bound=0.5)
    assert candidate["rounding_uncertainty_usd_millions"] == 2.0
    assert "+/- 2 from input rounding" in candidate["source_quote"]

    coarse = _derived(_db()[0], job, bound=5.0)[0]
    assert HELD_FOR_BOUND in coarse["label_flags"], (
        "a bound wider than a tenth of the figure is a question for a person"
    )


def test_the_row_stored_from_it_cites_the_same_document_and_says_what_it_subtracted():
    """The lineage the row carries is the one a reader can reach.

    It was written twice: once into the citation, which the dashboard shows,
    and once as assertion and lineage rows, which nothing joins. Both answers:
    every term is named on the row, and the run writes no rows besides the row
    itself.
    """
    db, job = _db()
    (candidate,) = _derived(db, job, reported_as="Calderon and NuVessa",
                            geography="United States")
    orch = PipelineOrchestrator(db, file_store=None)
    row = orch._datapoint_from_candidate(job, _source("k", ANNUAL), candidate)
    db.commit()

    assert row.source_url == ANNUAL
    assert row.reported_as == "Calderon and NuVessa"
    assert row.geography == "United States"
    terms = row.citation_json["derived_from"]
    assert len(terms) == len(candidate["_inputs"])
    assert [term["role"] for term in terms].count("quarter") == 3
    assert {term["role"] for term in terms} == {"period_total", "quarter"}
    assert {term["source_url"] for term in terms} == {ANNUAL, QUARTERLY}

    assert db.query(DerivationLineageORM).all() == []
    assert db.query(EvidenceAssertionORM).all() == []


def test_a_label_nobody_accounted_for_is_not_subtracted():
    """A number with no claim attached about whose number it is cannot be a
    term in arithmetic published as this product's revenue."""
    db, job = _db()
    quarters = [_row(job, f"2024Q{q}", 10.0) for q in (1, 2, 3)]
    total = _row(job, "2024", 46.041, period_type="annual", source_id="k", url=ANNUAL)
    total.issue_flags = ["label_not_understood"]
    db.add_all([*quarters, total]); db.commit()
    own = [PipelineOrchestrator._candidate_of(r) for r in [*quarters, total]]
    assert complete_series({"Calderon": own}, product="Calderon") == []
