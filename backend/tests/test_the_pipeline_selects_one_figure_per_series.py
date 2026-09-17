"""The quality stage settles which reading each series holds, and says so.

Two readings of one quarter were recorded as a duplicate and left open
forever, and the key they were recorded under read the labels as written - so
a quarter read twice under two spellings of one place was not a duplicate at
all, and nothing downstream knew which of the two to draw.

The key is now the series each reading declares itself part of, the selection
settles which reading the series holds, and a duplicate the selection settled
says what settled it instead of staying open. A duplicate nothing published is
still open, because nothing has answered it.

Invented names: Calderon, Acme Pharma.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    DatapointORM,
    DrugJobORM,
    ExtractionRunORM,
    QualityCheckORM,
)
from app.domain.models import (
    QualityCheckStatus,
    SeriesSelection,
    SourceType,
    ValidationStatus,
    new_id,
)
from app.pipeline.orchestrator import PipelineOrchestrator
from app.quality.checks import DUPLICATE_SERIES_READING, UNRECOGNISED_GEOGRAPHY
from app.storage.filestore import LocalFileStore


# A quote that names the product and spells the figure, so the gate publishes
# on the evidence and the selection is the only thing under test.
def _quote(value: float) -> str:
    return f"Calderon net sales were ${value} million in the quarter"


def _row(job, *, value, geography, scope="Product family", method="table", period="2024Q2"):
    return DatapointORM(
        id=new_id(),
        job_id=job.id,
        source_id="s1",
        period=period,
        value_reported=value,
        value_normalized_usd_millions=value,
        currency="USD",
        unit="millions",
        period_type="quarterly",
        revenue_scope=scope,
        formulation="aggregate",
        geography=geography,
        source_url="https://example.invalid/10q.htm",
        source_quote=_quote(value),
        extraction_method=method,
        confidence_score=0.9,
        validation_status=ValidationStatus.PENDING.value,
        citation_json={"source_url": "https://example.invalid/10q.htm"},
        issue_flags=[],
    )


def _pipeline(tmp_path, rows_of):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    job = DrugJobORM(
        id=new_id(), run_id=run.id, drug_name="Calderon",
        manufacturer="Acme Pharma", cik="0000000001", status="running", quality_flags=[],
    )
    db.add_all([run, job])
    rows = rows_of(job)
    db.add_all(rows)
    db.commit()
    return db, PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path))), job, rows


def _checks(db, issue_type):
    return db.query(QualityCheckORM).filter_by(issue_type=issue_type).all()


@pytest.mark.asyncio
async def test_two_spellings_of_one_place_are_one_series_and_one_figure(tmp_path):
    """The old key read `U.S.` and `US` as two quarters; they are one."""
    db, orch, job, rows = _pipeline(
        tmp_path,
        lambda job: [
            _row(job, value=12.0, geography="U.S.", scope="U.S.", method="xbrl_fact"),
            _row(job, value=12.0, geography="US", scope="U.S."),
        ],
    )
    await orch._quality_and_validation(job)

    tagged, printed = (db.get(DatapointORM, row.id) for row in rows)
    assert tagged.series_identity == printed.series_identity
    assert tagged.series_selection == SeriesSelection.SELECTED.value
    assert printed.series_selection == SeriesSelection.DUPLICATE.value

    duplicates = _checks(db, DUPLICATE_SERIES_READING)
    assert [check.affected_datapoint for check in duplicates] == [printed.id]
    assert duplicates[0].status == QualityCheckStatus.RESOLVED.value
    assert tagged.id in duplicates[0].explanation


@pytest.mark.asyncio
async def test_a_region_beside_the_whole_product_is_not_a_duplicate(tmp_path):
    """The other answer: two figures for two things, both kept, nothing flagged."""
    db, orch, job, rows = _pipeline(
        tmp_path,
        lambda job: [
            _row(job, value=144.1, geography=None),
            _row(job, value=20.0, geography="ex-US", scope="ex-U.S."),
        ],
    )
    await orch._quality_and_validation(job)

    whole, region = (db.get(DatapointORM, row.id) for row in rows)
    assert whole.series_identity != region.series_identity
    assert whole.series_selection == region.series_selection == SeriesSelection.SELECTED.value
    assert _checks(db, DUPLICATE_SERIES_READING) == []


@pytest.mark.asyncio
async def test_a_duplicate_nothing_published_stays_open(tmp_path):
    """A question the pipeline did not answer is not recorded as answered."""
    db, orch, job, rows = _pipeline(
        tmp_path,
        lambda job: [
            _row(job, value=12.0, geography="U.S.", scope="Company total"),
            _row(job, value=12.0, geography="US", scope="Company total"),
        ],
    )
    await orch._quality_and_validation(job)

    stored = [db.get(DatapointORM, row.id) for row in rows]
    assert {row.validation_status for row in stored} == {ValidationStatus.NEEDS_REVIEW.value}
    assert {row.series_selection for row in stored} == {None}
    duplicates = _checks(db, DUPLICATE_SERIES_READING)
    assert len(duplicates) == 1
    assert duplicates[0].status == QualityCheckStatus.OPEN.value


@pytest.mark.asyncio
async def test_a_place_the_vocabulary_does_not_know_is_raised_rather_than_mapped(tmp_path):
    """A spelling nobody recognises is visible, and the figure still publishes."""
    db, orch, job, rows = _pipeline(
        tmp_path, lambda job: [_row(job, value=3.0, geography="Acme Territories")]
    )
    await orch._quality_and_validation(job)

    stored = db.get(DatapointORM, rows[0].id)
    assert stored.geography_normalized.startswith("unrecognised")
    assert stored.validation_status == ValidationStatus.AUTO_PASS.value
    raised = _checks(db, UNRECOGNISED_GEOGRAPHY)
    assert [check.affected_datapoint for check in raised] == [stored.id]
    assert "Acme Territories" in raised[0].explanation


@pytest.mark.asyncio
async def test_a_known_place_raises_nothing(tmp_path):
    """The other answer, so a check stuck at "always fires" does not pass."""
    db, orch, job, rows = _pipeline(
        tmp_path, lambda job: [_row(job, value=3.0, geography="Rest of world", scope="ex-U.S.")]
    )
    await orch._quality_and_validation(job)
    assert db.get(DatapointORM, rows[0].id).geography_normalized == "ex-united-states"
    assert _checks(db, UNRECOGNISED_GEOGRAPHY) == []


@pytest.mark.asyncio
async def test_the_identity_is_stamped_where_the_labels_are_final(tmp_path):
    """One stamp, at the stage that has the last word on what a row says.

    A row's series is whatever it ends up declaring: the enricher fills a
    geography off the quote and reconciliation carries a corroborator's line
    onto the row it corroborates, both after the row is written. Stamping at
    creation wrote an answer to a question the row had not finished answering,
    and then wrote it again here.

    Both answers: a row the extractor has just written carries no identity, and
    the quality stage gives every row one.
    """
    db, orch, job, rows = _pipeline(
        tmp_path,
        lambda job: [_row(job, value=34.9, geography="U.S.")],
    )
    candidate = {
        "period": "2024Q3", "period_type": "quarterly", "value_reported": 12.0,
        "value_normalized_usd_millions": 12.0, "currency": "USD", "unit": "millions",
        "revenue_scope": "Product family", "geography": "US",
        "source_quote": _quote(12.0), "confidence": 0.9,
    }
    source = _Source("s1", "https://example.invalid/10q.htm")
    written = orch._datapoint_from_candidate(job, source, candidate)
    db.commit()
    assert written.series_identity is None
    assert written.geography_normalized is None

    await orch._quality_and_validation(job)

    stored = db.query(DatapointORM).all()
    assert len(stored) == len(rows) + 1
    assert all(row.series_identity for row in stored)
    assert all(row.geography_normalized for row in stored)


class _Source:
    """The few attributes `_datapoint_from_candidate` reads off a source."""

    def __init__(self, source_id: str, url: str) -> None:
        self.source_id = source_id
        self.url = url
        self.title = None
        self.filing_type = None
        self.accession_number = None
        self.source_date = None
        self.source_type = SourceType.SEC_FILING
        self.storage_key = None
