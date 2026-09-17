"""Completeness describes the job, not the run that last touched it.

The number was computed once, in the pipeline's completeness stage, and never
again. A reviewer could answer every open quarter and watch it sit still,
which made it a record of what the run found rather than of what the job now
holds - and it is shown as the latter.

Three things had to be true at once for it to move, and none of them were:
the count had to be retaken when review changed the rows it reads; a quarter
a reviewer had settled had to stop being counted as missing; and a value a
reviewer entered had to be recognised as a quarter at all.

Both directions are exercised on purpose. A recount that always returned 100
would pass a test that only watched the number rise, and so would one that
always returned 0 against a test that only watched it fall.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app import main
from app.api import products as products_api
from app.db.migrations import upgrade_database
from app.db.models import (
    DatapointORM,
    DrugJobORM,
    ExtractionRunORM,
    UnresolvedQuarterORM,
    ValidationTaskORM,
)
from app.domain.models import PeriodType, ValidationStatus
from app.quality.completeness import refresh_completeness

# Two quarters read, two the pipeline could not fill: the number the run
# recorded, 96.0, is a figure the model returned and not the ratio, which is
# what makes a stale value visible rather than coincidentally right.
QUARTERS_READ = ("2025Q3", "2025Q4")
QUARTERS_MISSING = ("2026Q1", "2026Q2")


def _database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    upgrade_database(engine)
    with Session(engine) as db:
        db.add(ExtractionRunORM(id="run-1", status="completed"))
        db.add(
            DrugJobORM(
                id="job-1", run_id="run-1", drug_name="Calderon",
                status="ready_for_review", completeness_pct=96.0, unresolved_count=2,
            )
        )
        for index, period in enumerate(QUARTERS_READ):
            db.add(
                DatapointORM(
                    id=f"dp-{index}", job_id="job-1", period=period,
                    period_type=PeriodType.QUARTERLY.value,
                    value_normalized_usd_millions=10.0 + index,
                    source_url="https://sec.gov/a-filing",
                    source_quote="Calderon 10.0",
                    validation_status=ValidationStatus.AUTO_PASS.value,
                )
            )
        db.add(
            ValidationTaskORM(
                id="vt-0", job_id="job-1", datapoint_id="dp-0",
                reason="recent_period", confidence_score=0.9, status="open",
            )
        )
        for index, period in enumerate(QUARTERS_MISSING):
            db.add(
                UnresolvedQuarterORM(
                    id=f"uq-{index}", job_id="job-1", period=period,
                    reason_unresolved="No product-level quarterly value extracted",
                    sources_checked=["https://sec.gov/a-filing"],
                    recommended_next_step="Check the earnings release",
                    confidence_that_unavailable=0.3,
                )
            )
        db.commit()
    return engine, sessionmaker(bind=engine)


@pytest.fixture()
def client(monkeypatch):
    _engine, factory = _database()
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(products_api, "SessionLocal", factory)
    # Bare, not `with`: entering the client runs the app's lifespan, which
    # migrates whatever `DATABASE_URL` names. These tests read the in-memory
    # engine above, so there is nothing for startup to do and a machine the
    # suite runs on has no business being stamped by it.
    yield TestClient(main.app), factory


def _job(factory) -> DrugJobORM:
    with factory() as db:
        return db.get(DrugJobORM, "job-1")


def test_the_stored_number_is_what_the_run_said_until_something_recounts(client):
    """The starting point: stale, and stale in a way the ratio would not be."""
    _, factory = client
    job = _job(factory)
    assert job.completeness_pct == 96.0
    assert job.unresolved_count == 2


def test_entering_a_value_fills_a_quarter_and_closes_its_gap(client):
    """Two of four answered becomes three of four, counted from the rows."""
    test_client, factory = client
    response = test_client.post(
        "/unresolved-quarters/uq-0/actions",
        json={
            "action": "enter_value",
            "value_normalized_usd_millions": 42.0,
            "source_url": "https://sec.gov/a-filing",
            "source_quote": "Calderon 42.0",
        },
    )
    assert response.status_code == 200

    job = _job(factory)
    assert job.unresolved_count == 1
    assert job.completeness_pct == 75.0, "three quarters held of four asked about"


def test_the_entered_value_is_recognised_as_a_quarter(client):
    """Without a period type it was stored as `unknown` and counted as nothing.

    The gap closed and the numerator did not move, so answering a quarter
    could lower the percentage.
    """
    test_client, factory = client
    test_client.post(
        "/unresolved-quarters/uq-0/actions",
        json={
            "action": "enter_value",
            "value_normalized_usd_millions": 42.0,
            "source_url": "https://sec.gov/a-filing",
            "source_quote": "Calderon 42.0",
        },
    )
    with factory() as db:
        entered = (
            db.query(DatapointORM)
            .filter(DatapointORM.job_id == "job-1", DatapointORM.period == "2026Q1")
            .one()
        )
    assert entered.period_type == PeriodType.QUARTERLY.value


def test_finding_the_issuer_published_nothing_also_closes_the_gap(client):
    """Not every answer is a value; establishing there is none is one too."""
    test_client, factory = client
    response = test_client.post(
        "/unresolved-quarters/uq-0/actions",
        json={"action": "not_disclosed", "reviewer_notes": "Never broken out"},
    )
    assert response.status_code == 200
    assert response.json()["datapoint_id"] is None

    job = _job(factory)
    assert job.unresolved_count == 1
    assert job.completeness_pct == pytest.approx(66.7, abs=0.05)


def test_re_queueing_hands_the_quarter_back_rather_than_answering_it(client):
    """The one resolution that is not an answer leaves the count alone."""
    test_client, factory = client
    response = test_client.post(
        "/unresolved-quarters/uq-0/actions",
        json={"action": "re_queue", "reviewer_notes": "Try the 8-K/A"},
    )
    assert response.status_code == 200

    job = _job(factory)
    assert job.unresolved_count == 2, "a re-queued quarter is still open"
    assert job.completeness_pct == 50.0


def test_rejecting_a_figure_takes_its_quarter_away(client):
    """The other direction: a figure the reviewer refused is not an answer."""
    test_client, factory = client
    response = test_client.post(
        "/validation-tasks/vt-0/actions", json={"action": "reject", "notes": "Wrong line"}
    )
    assert response.status_code == 200

    job = _job(factory)
    assert job.completeness_pct == pytest.approx(33.3, abs=0.05)


def test_coverage_is_counted_and_takes_nothing_from_a_model(client):
    """The percentage names a count of quarters, and only that.

    A model's own figure was passed in beside the count and had to be argued
    into being ignored; the parameter that carried it is gone, so there is
    nothing left for the same number on the same card to mean twice.
    """
    import inspect

    _, factory = client
    with factory() as db:
        job = db.get(DrugJobORM, "job-1")
        counted = refresh_completeness(db, job)
    assert counted.pct == 50.0
    assert counted.quarters == 2
    assert counted.gaps == 2
    assert list(inspect.signature(refresh_completeness).parameters) == ["db", "job"]
