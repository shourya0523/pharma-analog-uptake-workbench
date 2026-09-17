"""Coverage names distinct published quarters over the quarters asked for.

Three readings had to be wrong together for a series with nothing in it to
read as complete, and each is a separate assertion here:

- rows were counted rather than quarters, so several readings of one quarter
  read as several quarters;
- every status but ``rejected`` counted, so a quarter whose only figure was
  a question counted as an answer;
- the denominator was the gaps the pipeline had itself recorded, which it
  writes only where the issuer filed nothing at all, so a product whose
  quarters the extractor merely failed to read had no gaps and no shortfall.

Both answers are represented on purpose. A ratio that always returned 0 would
pass a test that only watched an empty series read low, and one that always
returned 100 would pass a test that only watched a full one read high.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.migrations import upgrade_database
from app.db.models import (
    DatapointORM,
    DrugJobORM,
    ExtractionRunORM,
    UnresolvedQuarterORM,
)
from app.domain.models import PeriodType, ValidationStatus
from app.quality.completeness import quarters_the_run_asked_for, refresh_completeness

# The window a run declares, as a run declares it. What it covers is the
# parser's answer rather than this file's, so the quarters are named once,
# below, against that answer.
WINDOW = {"earnings_since": "2024-04-05", "earnings_until": "2025-05-05"}
WINDOW_QUARTERS = ("2024Q1", "2024Q2", "2024Q3", "2024Q4", "2025Q1")
# A quarter the window does not reach, for the two tests that need one.
OUTSIDE = "2025Q3"


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    upgrade_database(engine)
    with Session(engine) as db:
        db.add(ExtractionRunORM(id="run-1", status="completed", options_json=WINDOW))
        db.commit()
    return sessionmaker(bind=engine)


def _job(db, job_id: str) -> DrugJobORM:
    job = DrugJobORM(id=job_id, run_id="run-1", drug_name="Calderon", status="ready_for_review")
    db.add(job)
    return job


def _row(db, job_id: str, row_id: str, period: str, status: str, **kwargs) -> None:
    db.add(
        DatapointORM(
            id=row_id,
            job_id=job_id,
            period=period,
            period_type=kwargs.pop("period_type", PeriodType.QUARTERLY.value),
            value_normalized_usd_millions=kwargs.pop("value", 10.0),
            source_url="https://sec.gov/a-filing",
            source_quote="Calderon net sales",
            validation_status=status,
            **kwargs,
        )
    )


def test_the_window_the_run_declared_is_what_coverage_is_a_fraction_of(factory):
    with factory() as db:
        job = _job(db, "job-window")
        db.flush()
        asked = quarters_the_run_asked_for(job)
    assert asked == set(WINDOW_QUARTERS)


def test_a_quarter_read_several_times_is_one_quarter(factory):
    """Three readings of one quarter, and one quarter of the window covered."""
    with factory() as db:
        job = _job(db, "job-repeat")
        for index in range(3):
            _row(db, "job-repeat", f"dp-{index}", "2024Q2", ValidationStatus.AUTO_PASS.value)
        counted = refresh_completeness(db, job)
    assert counted.quarters == 1
    assert counted.pct == 20.0


def test_a_quarter_whose_only_figure_is_held_is_not_covered(factory):
    """The shape that read 100%: rows for every quarter, none published.

    A held figure is a question about a quarter, not an answer to it, so a
    run that answered none of the quarters it asked about covers none.
    """
    with factory() as db:
        job = _job(db, "job-held")
        for index, period in enumerate(WINDOW_QUARTERS):
            _row(db, "job-held", f"dp-{index}", period, ValidationStatus.NEEDS_REVIEW.value)
        _row(db, "job-held", "dp-c", "2024Q1", ValidationStatus.CORROBORATES.value)
        counted = refresh_completeness(db, job)
    assert counted.quarters == 0
    assert counted.pct == 0.0


def test_a_run_that_answered_every_quarter_it_asked_about_reads_full(factory):
    """The other answer: nothing here can only ever report a shortfall."""
    with factory() as db:
        job = _job(db, "job-full")
        for index, period in enumerate(WINDOW_QUARTERS):
            _row(db, "job-full", f"dp-{index}", period, ValidationStatus.AUTO_PASS.value)
        counted = refresh_completeness(db, job)
    assert counted.quarters == len(WINDOW_QUARTERS)
    assert counted.pct == 100.0


def test_a_confirmed_figure_counts_and_a_rejected_one_does_not(factory):
    with factory() as db:
        job = _job(db, "job-review")
        _row(db, "job-review", "dp-0", "2024Q1", ValidationStatus.CONFIRMED.value)
        _row(db, "job-review", "dp-1", "2024Q2", ValidationStatus.REJECTED.value)
        counted = refresh_completeness(db, job)
    assert counted.quarters == 1
    assert counted.pct == 20.0


def test_a_longer_span_is_not_a_quarter(factory):
    """An annual figure, and one labelled with a quarter, are not quarters."""
    with factory() as db:
        job = _job(db, "job-annual")
        _row(
            db, "job-annual", "dp-0", "2024",
            ValidationStatus.AUTO_PASS.value, period_type=PeriodType.ANNUAL.value,
        )
        _row(
            db, "job-annual", "dp-1", "2024Q4",
            ValidationStatus.AUTO_PASS.value, period_type=PeriodType.ANNUAL.value,
        )
        counted = refresh_completeness(db, job)
    assert counted.quarters == 0
    assert counted.pct == 0.0


def test_a_gap_the_pipeline_recorded_is_a_quarter_it_did_not_answer(factory):
    """A recorded gap is in the denominator wherever the quarter sits.

    The gaps the pipeline records reach outside the window whenever the
    issuer filed nothing at all. They are quarters it looked for and has no
    figure for, which is what the denominator counts, so a run that answered
    every quarter it asked for and recorded one gap beyond them does not read
    as having answered everything.
    """
    with factory() as db:
        job = _job(db, "job-gap")
        for index, period in enumerate(WINDOW_QUARTERS):
            _row(db, "job-gap", f"dp-{index}", period, ValidationStatus.AUTO_PASS.value)
        db.add(
            UnresolvedQuarterORM(
                id="uq-0",
                job_id="job-gap",
                period=OUTSIDE,
                reason_unresolved="The issuer filed nothing in the window",
                sources_checked=["https://sec.gov/a-filing"],
                recommended_next_step="Check the earnings release",
                confidence_that_unavailable=0.4,
            )
        )
        counted = refresh_completeness(db, job)
    assert counted.gaps == 1
    # Five quarters answered, one gap: six quarters known about.
    assert counted.pct == round(100 * 5 / 6, 1)


def test_a_quarter_answered_outside_the_window_counts_on_both_sides(factory):
    """A quarter the run answered is one it knows about, window or not.

    It is reported as a quarter held and it is in the denominator too, so the
    ratio cannot exceed 1 without a clamp standing in for that.
    """
    with factory() as db:
        job = _job(db, "job-outside")
        for index, period in enumerate((*WINDOW_QUARTERS, OUTSIDE)):
            _row(db, "job-outside", f"dp-{index}", period, ValidationStatus.AUTO_PASS.value)
        counted = refresh_completeness(db, job)
    assert counted.quarters == len(WINDOW_QUARTERS) + 1
    assert counted.pct == 100.0


def test_a_run_that_declared_no_window_counts_what_it_holds_and_misses(factory):
    """With nothing asked for, the rows and the recorded gaps are the question."""
    with factory() as db:
        db.add(ExtractionRunORM(id="run-open", status="completed", options_json={}))
        job = DrugJobORM(
            id="job-open", run_id="run-open", drug_name="NuVessa", status="ready_for_review"
        )
        db.add(job)
        _row(db, "job-open", "dp-0", "2024Q1", ValidationStatus.AUTO_PASS.value)
        db.add(
            UnresolvedQuarterORM(
                id="uq-open",
                job_id="job-open",
                period="2024Q2",
                reason_unresolved="The issuer filed nothing in the window",
                sources_checked=["https://sec.gov/a-filing"],
                recommended_next_step="Check the earnings release",
                confidence_that_unavailable=0.4,
            )
        )
        counted = refresh_completeness(db, job)
    assert quarters_the_run_asked_for(job) == set()
    assert counted.pct == 50.0


def test_the_denominator_is_every_quarter_the_run_knows_about(factory):
    """The two numbers on the card come from one set of quarters.

    The quarters held were counted over everything the job answered and the
    percentage over the window alone, so a run that reached back past its own
    window reported a count and a percentage from two different universes.

    Both answers: a job whose answers all sit inside the window reads exactly
    its share of the window, and a job that answered quarters beyond it has
    those quarters on both sides of the ratio rather than on neither.
    """
    inside = WINDOW_QUARTERS[:3]
    beyond = ("2025Q3", "2025Q4")
    with factory() as db:
        job = _job(db, "job-inside")
        for index, period in enumerate(inside):
            _row(db, "job-inside", f"in-{index}", period, ValidationStatus.AUTO_PASS.value)
        within = refresh_completeness(db, job)

        reaching = _job(db, "job-beyond")
        for index, period in enumerate((*inside, *beyond)):
            _row(db, "job-beyond", f"be-{index}", period, ValidationStatus.AUTO_PASS.value)
        outside = refresh_completeness(db, reaching)

    assert within.quarters == len(inside)
    assert within.pct == round(100 * len(inside) / len(WINDOW_QUARTERS), 1)

    assert outside.quarters == len(inside) + len(beyond)
    known = len(WINDOW_QUARTERS) + len(beyond)
    assert outside.pct == round(100 * outside.quarters / known, 1)
    assert outside.pct > within.pct, "quarters the run answered may not count for nothing"
