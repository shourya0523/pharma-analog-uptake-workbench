"""A series says when the product was approved and where its data starts.

Those are two quarters and they are rarely the same one. The approval date
anchors the x-axis; the first quarter a series can plot is wherever the
disclosures begin, and a quarter covering eighteen days between launch and the
quarter's end is not a quarter of selling. Neither was published at all, so an
eighteen-day stub plotted as the first full quarter of a ramp and there was
nothing on the sheet to say otherwise.

Both reach the product sheet and the dashboard payload, and the datapoint
sheets carry the series each figure belongs to and which figure the series
holds.

Invented names: Calderon, Acme Pharma.
"""

from __future__ import annotations

import csv
import io
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.dashboard.series import build_dashboard_preview
from app.db.migrations import upgrade_database
from app.db.models import (
    CanonicalProductORM,
    DatapointORM,
    DrugJobORM,
    ExtractionRunORM,
    UnresolvedQuarterORM,
)
from app.domain.models import (
    REPORTED_WITH_ANOTHER_PRODUCT,
    PeriodType,
    SeriesSelection,
    ValidationStatus,
)
from app.export.builder import PRODUCT_HEADERS, ExportBuilder, product_export_rows
from app.parsing.labels import FLAG_PARTIAL
from app.pipeline.series_identity import (
    commercial_start_quarter,
    series_end_reason,
    series_identity,
)
from app.storage.filestore import LocalFileStore

_IDENTITY = series_identity(
    issuer="0000000001",
    product="Calderon",
    revenue_scope="Product family",
    geography=None,
    formulation="aggregate",
    reported_as=None,
    currency="USD",
    period_type=PeriodType.QUARTERLY.value,
)

# An approval in the middle of a quarter, a stub for the days after it, then
# quarters of selling - and, before any of it, a figure filed under the
# product's name for a quarter in which it was not on sale.
QUARTERS = [
    ("pre-launch", "2023Q3", 81.5, SeriesSelection.SELECTED.value, []),
    ("stub", "2024Q1", 1.2, SeriesSelection.SELECTED.value, [FLAG_PARTIAL]),
    ("first-full", "2024Q2", 8.7, SeriesSelection.SELECTED.value, []),
    ("second-reading", "2024Q2", 8.7, SeriesSelection.DUPLICATE.value, []),
    ("next", "2024Q3", 21.0, SeriesSelection.SELECTED.value, []),
]


def _database(*, approval: date | None, unresolved: list[tuple[str, str]] = ()):
    engine = create_engine("sqlite://")
    upgrade_database(engine)
    db = Session(engine)
    db.add(ExtractionRunORM(id="run", status="completed"))
    db.add(
        DrugJobORM(
            id="job", run_id="run", drug_name="Calderon",
            manufacturer="Acme Pharma", cik="0000000001", status="ready_for_review",
        )
    )
    if approval:
        db.add(
            CanonicalProductORM(
                id="canonical", canonical_name="Calderon", identity_key="calderon",
                current_commercial_owner="Acme Pharma", initial_approval_date=approval,
            )
        )
    for row_id, period, value, selection, flags in QUARTERS:
        db.add(
            DatapointORM(
                id=row_id, job_id="job", period=period,
                period_type=PeriodType.QUARTERLY.value,
                value_normalized_usd_millions=value, revenue_scope="Product family",
                formulation="aggregate", currency="USD",
                reported_as="Calderon and NuVessa" if row_id == "next" else None,
                source_url="https://sec.gov/a-filing", source_quote="Calderon net sales",
                validation_status=ValidationStatus.AUTO_PASS.value,
                series_identity=_IDENTITY, series_selection=selection,
                issue_flags=list(flags),
            )
        )
    for period, reason in unresolved:
        db.add(
            UnresolvedQuarterORM(
                id=f"gap-{period}", job_id="job", period=period,
                reason_unresolved=reason,
                recommended_next_step="Check the filer's own schedule",
                confidence_that_unavailable=0.6,
            )
        )
    db.commit()
    return db


def test_the_first_quarter_with_a_figure_is_not_always_where_selling_starts():
    """A pre-launch figure and an eighteen-day stub are both skipped."""
    quarters = [(period, FLAG_PARTIAL not in flags) for _, period, _, _, flags in QUARTERS]
    assert commercial_start_quarter(quarters, launch_quarter="2024Q1") == "2024Q2"
    # The other answer: with the stub covering the whole quarter, that quarter
    # is where the series begins.
    whole = [(period, True) for period, _ in quarters]
    assert commercial_start_quarter(whole, launch_quarter="2024Q1") == "2024Q1"
    # And with no launch quarter known, nothing is excluded for being early.
    assert commercial_start_quarter(quarters, launch_quarter=None) == "2023Q3"
    assert commercial_start_quarter([], launch_quarter="2024Q1") is None


def test_the_two_quarters_reach_the_payload_and_the_product_sheet():
    db = _database(approval=date(2024, 3, 13))
    payload = build_dashboard_preview(db, run_id="run")
    product = payload["products"][0]
    assert product["launch_quarter"] == "2024Q1"
    assert product["commercial_start_quarter"] == "2024Q2"

    headers, rows = product_export_rows(db, "run")
    values = dict(zip(headers, rows[0], strict=True))
    assert values["launch_quarter"] == "2024Q1"
    assert values["commercial_start_quarter"] == "2024Q2"
    assert PRODUCT_HEADERS == headers


def test_without_an_approval_date_the_anchor_is_absent_rather_than_guessed():
    """A launch quarter nobody recorded is not the first quarter with a figure."""
    payload = build_dashboard_preview(_database(approval=None), run_id="run")
    product = payload["products"][0]
    assert product["launch_quarter"] is None
    assert product["commercial_start_quarter"] == "2023Q3"


def test_the_series_payload_says_what_each_point_is():
    db = _database(approval=date(2024, 3, 13))
    payload = build_dashboard_preview(db, run_id="run")
    periods = [point["period"] for point in payload["series"]]
    # The duplicate reading of 2024Q2 is not a second point.
    assert sorted(periods) == ["2023Q3", "2024Q1", "2024Q2", "2024Q3"]
    point = next(p for p in payload["series"] if p["period"] == "2024Q1")
    assert point["period_type"] == PeriodType.QUARTERLY.value
    assert point["series_identity"] == _IDENTITY
    assert point["series_selection"] == SeriesSelection.SELECTED.value
    assert point["partial_period"] is True
    pair = next(p for p in payload["series"] if p["period"] == "2024Q3")
    assert pair["reported_as"] == "Calderon and NuVessa"

    held = build_dashboard_preview(db, run_id="run", include_held=True)
    assert len(held["series"]) == len(QUARTERS)


async def test_the_quarterly_file_holds_the_figure_the_series_holds(tmp_path):
    db = _database(approval=date(2024, 3, 13))
    store = LocalFileStore(str(tmp_path))
    exports = await ExportBuilder(db, store).export_powerbi_csvs("run")

    async def sheet(fmt):
        export = next(item for item in exports if item.format == fmt)
        return list(csv.DictReader(io.StringIO((await store.get(export.storage_key)).decode())))

    quarterly = await sheet("quarterly_revenue_csv")
    assert [row["period"] for row in quarterly] == ["2023Q3", "2024Q1", "2024Q2", "2024Q3"]
    assert {row["series_identity"] for row in quarterly} == {_IDENTITY}
    # Nothing is lost: the unfiltered sheet still holds the second reading,
    # saying what it is.
    every = await sheet("all_datapoints_csv")
    assert len(every) == len(QUARTERS)
    assert sorted(row["series_selection"] for row in every) == [
        SeriesSelection.DUPLICATE.value,
        *[SeriesSelection.SELECTED.value] * 4,
    ]


# What the pipeline writes when the issuer reports the product only together
# with another one: the pair's figure is published under the pair, and the
# product's own is not a number anybody discloses.
_PAIRED = f"[{REPORTED_WITH_ANOTHER_PRODUCT}] Calderon is reported only as Calderon + NuVessa"
_UNREAD = "No reliable product-level quarterly value extracted"


def test_a_series_that_ends_at_an_event_says_which_event():
    db = _database(
        approval=date(2024, 3, 13),
        unresolved=[("2024Q4", _PAIRED), ("2025Q1", _PAIRED)],
    )
    product = build_dashboard_preview(db, run_id="run")["products"][0]
    assert product["series_end_quarter"] == "2024Q3"
    assert product["series_end_reason"] == REPORTED_WITH_ANOTHER_PRODUCT

    headers, rows = product_export_rows(db, "run")
    values = dict(zip(headers, rows[0], strict=True))
    assert values["series_end_reason"] == REPORTED_WITH_ANOTHER_PRODUCT


def test_a_quarter_nobody_could_read_is_a_gap_and_not_an_ending():
    """The other answer, and the one that matters: a hole is not an event."""
    db = _database(approval=date(2024, 3, 13), unresolved=[("2024Q4", _UNREAD)])
    product = build_dashboard_preview(db, run_id="run")["products"][0]
    assert product["series_end_quarter"] == "2024Q3"
    assert product["series_end_reason"] is None

    # Nor is a run of quarters that stopped for two different reasons.
    assert series_end_reason(
        last_quarter="2024Q3",
        unresolved=[("2024Q4", _PAIRED), ("2025Q1", _UNREAD)],
    ) is None
    # Nor a series still running, with nothing unresolved after it.
    assert series_end_reason(last_quarter="2024Q3", unresolved=[]) is None
