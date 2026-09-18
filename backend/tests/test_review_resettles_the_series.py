"""A reviewer's decision changes the series, not only the row.

The pipeline settles which reading each series holds for each quarter, and a
reviewer can change the inputs to that afterwards: confirming a figure for a
quarter that already has one adds a second reading of it, rejecting one leaves
the quarter to a reading that was standing behind it, and a figure typed into
an open gap arrives belonging to no series at all. Left unasked, every one of
those puts two points on a curve for one quarter, or one point with no series.

Both answers, on each path: the second reading becomes a duplicate rather than
a point, and the quarter still holds a figure.

Invented names: Calderon, Acme Pharma.
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
from app.domain.models import (
    PeriodType,
    SeriesSelection,
    ValidationStatus,
    holds_the_series_figure,
)
from app.pipeline.orchestrator import stamp_series_identity

# One quarter read twice from the same filing - a schedule and the model's
# sentence about it - and one quarter nothing answered.
READINGS = [
    ("dp-table", "2025Q3", 10.0, "table", ValidationStatus.AUTO_PASS.value),
    ("dp-model", "2025Q3", 10.0, "llm", ValidationStatus.NEEDS_REVIEW.value),
]


def _database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    upgrade_database(engine)
    with Session(engine) as db:
        db.add(ExtractionRunORM(id="run-1", status="completed"))
        job = DrugJobORM(
            id="job-1", run_id="run-1", drug_name="Calderon",
            manufacturer="Acme Pharma", cik="0000000001",
            status="ready_for_review", completeness_pct=50.0, unresolved_count=1,
        )
        db.add(job)
        for row_id, period, value, method, status in READINGS:
            row = DatapointORM(
                id=row_id, job_id="job-1", period=period,
                period_type=PeriodType.QUARTERLY.value,
                value_normalized_usd_millions=value, revenue_scope="Product family",
                formulation="aggregate", currency="USD",
                source_url="https://sec.gov/a-filing", source_quote="Calderon 10.0",
                extraction_method=method, validation_status=status,
                series_selection=(
                    SeriesSelection.SELECTED.value
                    if status == ValidationStatus.AUTO_PASS.value
                    else None
                ),
            )
            stamp_series_identity(job, row)
            db.add(row)
        db.add(
            ValidationTaskORM(
                id="vt-0", job_id="job-1", datapoint_id="dp-model",
                reason="conflicting_values", confidence_score=0.5, status="open",
            )
        )
        db.add(
            UnresolvedQuarterORM(
                id="uq-0", job_id="job-1", period="2025Q4",
                reason_unresolved="No product-level quarterly value extracted",
                sources_checked=["https://sec.gov/a-filing"],
                recommended_next_step="Check the earnings release",
                confidence_that_unavailable=0.3,
            )
        )
        db.commit()
    return sessionmaker(bind=engine)


@pytest.fixture()
def client(monkeypatch):
    factory = _database()
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(products_api, "SessionLocal", factory)
    with TestClient(main.app) as test_client:
        yield test_client, factory


def _selections(factory) -> dict[str, str | None]:
    with factory() as db:
        return {
            row.id: row.series_selection
            for row in db.query(DatapointORM).filter_by(job_id="job-1").all()
        }


def test_confirming_a_second_reading_makes_it_a_duplicate_not_a_second_point(client):
    test_client, factory = client
    response = test_client.post(
        "/validation-tasks/vt-0/actions", json={"action": "confirm"}
    )
    assert response.status_code == 200

    selections = _selections(factory)
    # One of the two holds the quarter, and it is the schedule rather than the
    # sentence - the stronger claim, as reconciliation ranks them.
    assert selections["dp-table"] == SeriesSelection.SELECTED.value
    assert selections["dp-model"] == SeriesSelection.DUPLICATE.value
    assert sum(1 for value in selections.values() if holds_the_series_figure(value)) == 1


def test_rejecting_the_selected_reading_leaves_the_quarter_to_the_other(client):
    """The other answer: the quarter keeps a figure rather than losing one."""
    test_client, factory = client
    with factory() as db:
        row = db.get(DatapointORM, "dp-model")
        row.validation_status = ValidationStatus.AUTO_PASS.value
        db.commit()

    response = test_client.patch(
        "/datapoints/dp-table", json={"validation_status": ValidationStatus.REJECTED.value}
    )
    assert response.status_code == 200

    selections = _selections(factory)
    assert selections["dp-model"] == SeriesSelection.SELECTED.value
    # The rejected row states the same figure, so it is recorded as another
    # reading of it rather than as a point - and it is out of the curve on
    # both counts, its status and its standing.
    assert selections["dp-table"] == SeriesSelection.DUPLICATE.value
    assert not holds_the_series_figure(selections["dp-table"])


def test_a_figure_a_reviewer_types_belongs_to_a_series(client):
    test_client, factory = client
    response = test_client.post(
        "/unresolved-quarters/uq-0/actions",
        json={
            "action": "enter_value",
            "value_normalized_usd_millions": 12.5,
            "source_url": "https://sec.gov/another-filing",
            "source_quote": "Calderon net sales were $12.5 million",
        },
    )
    assert response.status_code == 200
    entered = response.json()["datapoint_id"]

    with factory() as db:
        row = db.get(DatapointORM, entered)
        assert row.series_identity
        assert row.series_selection == SeriesSelection.SELECTED.value
        assert row.geography_normalized == "unspecified"


def test_the_gap_path_re_stamps_the_rows_it_did_not_write(client):
    """One answer for three paths, so an old row is healed by each of them.

    The gap path stamped only the row it created and then asked the selection
    over every row of the job. A row written before the identity column existed
    carries none, and an empty identity is its own group - so two such rows for
    one quarter, which are two series and two legitimate points, were read as
    one series and one of them was dropped from the curve.

    Both answers: the two legacy readings are two series and both keep their
    figures, and the entered row belongs to a series of its own.
    """
    with factory_rows(client) as db:
        for row_id, scope, geography, value in (
            ("dp-old-whole", "Product family", None, 20.0),
            ("dp-old-region", "U.S.", "United States", 20.0),
        ):
            db.add(
                DatapointORM(
                    id=row_id, job_id="job-1", period="2025Q2",
                    period_type=PeriodType.QUARTERLY.value,
                    value_normalized_usd_millions=value, revenue_scope=scope,
                    formulation="aggregate", currency="USD", geography=geography,
                    source_url="https://sec.gov/an-older-filing",
                    source_quote=f"Calderon {value}",
                    extraction_method="table",
                    validation_status=ValidationStatus.AUTO_PASS.value,
                )
            )
        db.commit()

    test_client, factory = client
    response = test_client.post(
        "/unresolved-quarters/uq-0/actions",
        json={
            "action": "enter_value",
            "value_normalized_usd_millions": 12.5,
            "source_url": "https://sec.gov/another-filing",
            "source_quote": "Calderon net sales were $12.5 million",
        },
    )
    assert response.status_code == 200
    entered = response.json()["datapoint_id"]

    selections = _selections(factory)
    assert selections["dp-old-whole"] == SeriesSelection.SELECTED.value
    assert selections["dp-old-region"] == SeriesSelection.SELECTED.value
    assert selections[entered] == SeriesSelection.SELECTED.value


def factory_rows(client):
    """A session on the test client's database, for rows a fixture cannot add."""
    _test_client, factory = client
    return factory()
