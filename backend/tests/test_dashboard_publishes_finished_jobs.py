"""The dashboard shows what finished jobs found, and says what each figure is for.

`build_dashboard_preview` queried every job whatever its status, so rows from
a job stopped mid-reconciliation - the step that settles two readings of one
quarter - and from jobs that failed outright were published beside rows from
jobs that ran to the end. And the rows it published said only their period
and value, so the one thing that tells a worldwide quarter from its U.S.
slice never reached the chart, which then drew whichever row it read last.

Both answers are represented: a finished job's rows are published, an
unfinished job's are not, and the unfinished job's product still appears with
its status on it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.dashboard.series import build_dashboard_preview, scope_rank
from app.db.migrations import upgrade_database
from app.db.models import DatapointORM, DrugJobORM, ExtractionRunORM
from app.domain.models import (
    FINISHED_JOB_STATUS_VALUES,
    JobStatus,
    PeriodType,
    RevenueScope,
    ValidationStatus,
)

# One job per status, each holding one published quarter, so what reaches the
# series is a function of the status alone. Names differ because the preview
# keeps one job per product name.
JOBS = [
    ("job-review", "Calderon", JobStatus.READY_FOR_REVIEW),
    ("job-done", "Calderon XR", JobStatus.COMPLETED),
    ("job-running", "NuVessa", JobStatus.RUNNING),
    ("job-failed", "Nebulized Calderon", JobStatus.FAILED),
    ("job-queued", "Calderon Depot", JobStatus.QUEUED),
    ("job-cancelled", "Calderon Oral", JobStatus.CANCELLED),
]


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    upgrade_database(engine)
    session = Session(engine)
    session.add(ExtractionRunORM(id="run", status="completed"))
    for job_id, name, status in JOBS:
        session.add(
            DrugJobORM(id=job_id, run_id="run", drug_name=name, status=status.value)
        )
        session.add(
            DatapointORM(
                id=f"dp-{job_id}",
                job_id=job_id,
                period="2024Q4",
                period_type=PeriodType.QUARTERLY.value,
                value_normalized_usd_millions=10.0,
                revenue_scope=RevenueScope.WORLDWIDE.value,
                geography="Worldwide",
                formulation="tablet",
                reported_as="Calderon and NuVessa, together",
                source_url="https://sec.gov/a-filing",
                source_quote="Calderon net sales",
                validation_status=ValidationStatus.AUTO_PASS.value,
            )
        )
    session.commit()
    return session


def test_the_finished_statuses_come_from_the_enum():
    assert FINISHED_JOB_STATUS_VALUES == {
        status.value for status in JobStatus if status.ran_to_the_end
    }
    assert JobStatus.READY_FOR_REVIEW.value in FINISHED_JOB_STATUS_VALUES
    assert JobStatus.COMPLETED.value in FINISHED_JOB_STATUS_VALUES
    assert JobStatus.RUNNING.value not in FINISHED_JOB_STATUS_VALUES
    assert JobStatus.FAILED.value not in FINISHED_JOB_STATUS_VALUES


def test_only_a_job_that_ran_to_the_end_contributes_series_rows(db):
    payload = build_dashboard_preview(db)
    assert sorted(row["product"] for row in payload["series"]) == ["Calderon", "Calderon XR"]
    # Every product is still listed, with the status that explains the absence.
    assert {product["product_name"] for product in payload["products"]} == {
        name for _, name, _ in JOBS
    }
    assert {
        product["validation_status"]
        for product in payload["products"]
        if product["product_name"] == "NuVessa"
    } == {JobStatus.RUNNING.value}


def test_the_filter_holds_when_the_viewer_asks_for_held_values(db):
    """Showing held figures is a question about a row, not about a job."""
    payload = build_dashboard_preview(db, include_held=True)
    assert sorted(row["product"] for row in payload["series"]) == ["Calderon", "Calderon XR"]


def test_each_row_says_what_the_figure_is_for(db):
    row = build_dashboard_preview(db)["series"][0]
    assert row["revenue_scope"] == RevenueScope.WORLDWIDE.value
    assert row["geography"] == "Worldwide"
    assert row["formulation"] == "tablet"
    assert row["reported_as"] == "Calderon and NuVessa, together"
    assert row["scope_rank"] == scope_rank(RevenueScope.WORLDWIDE.value)


def test_the_whole_product_outranks_every_slice_of_it():
    """The two spellings of the whole product are one scope and rank first."""
    whole = {RevenueScope.WORLDWIDE.value, RevenueScope.PRODUCT_FAMILY.value}
    assert len({scope_rank(value) for value in whole}) == 1
    for scope in RevenueScope:
        if scope.value not in whole:
            assert scope_rank(scope.value) > scope_rank(RevenueScope.WORLDWIDE.value)
    # A scope the enum does not know sorts behind every scope it does.
    assert scope_rank("acme:NotAScopeAtAll") > max(scope_rank(s.value) for s in RevenueScope)


def test_an_aggregate_peak_over_no_peaks_is_not_computed_rather_than_zero(db):
    peak = build_dashboard_preview(db)["kpis"]["aggregate_selected_peak"]
    assert peak["value"] is None
    assert peak["covered_products"] == 0
