"""The exported workbook's derived matrices hold gold's own values, re-indexed.

`scripts/export_gold_workbook.py` writes two pivots of the same figures: one
against the calendar, one against each product's launch. Both are generated in
Python rather than written as Excel formulas, so nothing recalculates them and
nothing else checks them - a re-indexing that dropped a quarter, doubled one,
or anchored a series on the wrong quarter would look exactly like a correct
sheet.

The checks are on the committed workbook, so they also fail when gold moves and
the export is not re-run. That is the case this file mainly exists for: the
dataset is meant to be refreshed quarterly, and a stale workbook reads as
current.
"""

from __future__ import annotations

import json
import pathlib
from collections import defaultdict

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = REPO / "seed" / "gold"
WORKBOOK = REPO / "exports" / "pah_gold_dataset.xlsx"

CALENDAR_LEAD = ["drug_name", "benchmark_identity", "revenue_scope", "first_approval_year"]
LAUNCH_LEAD = CALENDAR_LEAD + [
    "launch_anchor_quarter", "alignment_basis", "quarters_from_launch_to_first_report",
]
STATED = "launch quarter stated in gold"
ASSUMED = "no launch quarter in gold - anchored on first reported quarter"


def load_jsonl(name):
    path = GOLD / name
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def quarter_index(period):
    return int(period[:4]) * 4 + int(period[5]) - 1


@pytest.fixture(scope="module")
def workbook():
    openpyxl = pytest.importorskip("openpyxl")
    assert WORKBOOK.exists(), (
        f"{WORKBOOK.relative_to(REPO)} is missing; regenerate it with "
        "python scripts/export_gold_workbook.py"
    )
    book = openpyxl.load_workbook(WORKBOOK, read_only=True, data_only=True)
    yield book
    book.close()


def sheet_rows(book, title, lead):
    """The lead columns and the period columns of a matrix sheet, header checked."""
    ws = book[title]
    grid = [[cell.value for cell in row] for row in ws.iter_rows()]
    header = grid[1]
    assert header[:len(lead)] == lead, f"{title} lead columns are {header[:len(lead)]}"
    columns = header[len(lead):]
    rows = [row for row in grid[2:] if row and row[0] is not None]
    return columns, rows


@pytest.fixture(scope="module")
def gold():
    quarterly = load_jsonl("quarterly_revenue.jsonl")
    observed = defaultdict(set)
    for row in quarterly:
        observed[row["drug_name"]].add(row["period"])
    return {
        "values": {(row["drug_name"], row["period"]): row["value_normalized_usd_millions"]
                   for row in quarterly},
        "rows": quarterly,
        "observed": observed,
        "coverage": {row["drug_name"]: row for row in load_jsonl("series_coverage.jsonl")},
        "profiles": {row["drug_name"]: row for row in load_jsonl("product_profiles.jsonl")},
    }


def test_the_calendar_matrix_is_exactly_the_quarterly_rows(workbook, gold):
    """Every figure in the pivot is a figure in gold, and none is missing."""
    assert len(gold["values"]) == len(gold["rows"]), "a series reports the same quarter twice"
    periods, rows = sheet_rows(workbook, "Quarterly Matrix", CALENDAR_LEAD)
    found = {
        (row[0], period): value
        for row in rows
        for period, value in zip(periods, row[len(CALENDAR_LEAD):])
        if value is not None
    }
    assert found == gold["values"]


def test_the_launch_matrix_anchors_on_the_launch_gold_states(workbook, gold):
    """The anchor is gold's launch quarter, never the first quarter reported.

    Those differ for most products here, and anchoring on the first reported
    quarter would label a series that begins mid-life as Year 1 Q1 and align it
    against a product's genuine launch. Where gold states no launch quarter the
    row falls back to its first reported quarter and has to say so.
    """
    _, rows = sheet_rows(workbook, "Launch-Aligned Matrix", LAUNCH_LEAD)
    seen_both = set()
    for row in rows:
        drug, anchor, basis, gap = row[0], row[4], row[5], row[6]
        first_reported = min(gold["observed"][drug])
        stated = gold["coverage"].get(drug, {}).get("launch_quarter")
        assert anchor == (stated or first_reported), drug
        assert basis == (STATED if stated else ASSUMED), drug
        assert gap == quarter_index(first_reported) - quarter_index(anchor), drug
        seen_both.add(basis)
    assert seen_both == {STATED, ASSUMED}, (
        "every row shares one alignment basis, so this test would pass without "
        "distinguishing them"
    )


def test_the_launch_matrix_re_indexes_and_invents_nothing(workbook, gold):
    """Each value sits at the Year N Qn its own anchor implies, and only there.

    Offset 0 is the anchor quarter and reads Year 1 Q1, so a series anchored at
    2019Q1 puts 2019Q3 at Year 1 Q3 and 2020Q1 at Year 2 Q1.
    """
    labels, rows = sheet_rows(workbook, "Launch-Aligned Matrix", LAUNCH_LEAD)
    found = {}
    for row in rows:
        drug, anchor = row[0], row[4]
        for label, value in zip(labels, row[len(LAUNCH_LEAD):]):
            if value is None:
                continue
            year, quarter = label.split()[1], label.split()[2]
            offset = (int(year) - 1) * 4 + int(quarter[1:]) - 1
            period_index = quarter_index(anchor) + offset
            found[(drug, f"{period_index // 4}Q{period_index % 4 + 1}")] = value
    assert found == gold["values"]


def test_the_workbook_never_invents_an_approval_year(workbook, gold):
    """first_approval_year is gold's, or blank.

    The curated attribute lives in reference data the pipeline reads, and a
    script that names that file and also reads gold is what
    test_gold_is_not_an_input fails. So the column is taken from gold's own
    profiles, and a product gold holds no profile for is left empty rather than
    filled from the other side of that line.
    """
    _, rows = sheet_rows(workbook, "Quarterly Matrix", CALENDAR_LEAD)
    blank = []
    for row in rows:
        drug, year = row[0], row[3]
        assert year == gold["profiles"].get(drug, {}).get("first_approval_year"), drug
        if year is None:
            blank.append(drug)
    assert set(blank) == set(gold["observed"]) - set(gold["profiles"])


def test_every_quarterly_series_reaches_the_refresh_sheet(workbook, gold):
    """Refresh Status is a census of the data, not of the builder's metadata.

    Series Coverage has a row only where the gold builder carries metadata for
    a product. A series present in the revenue rows and absent from both sheets
    is one nothing reports on, which is how a series goes stale unnoticed.
    """
    ws = workbook["Refresh Status"]
    grid = [[cell.value for cell in row] for row in ws.iter_rows()]
    header = grid[1]
    rows = [row for row in grid[2:] if row and row[0] is not None]
    named = {row[header.index("drug_name")] for row in rows}
    assert named == set(gold["observed"])

    latest = max(row["period"] for row in gold["rows"])
    assert header.index("quarters_behind_latest") >= 0
    for row in rows:
        drug = row[header.index("drug_name")]
        end = gold["coverage"].get(drug, {}).get("series_end_quarter") or max(gold["observed"][drug])
        assert row[header.index("series_end_quarter")] == end, drug
        assert row[header.index("last_reported_quarter")] == max(gold["observed"][drug]), drug
        assert row[header.index("quarters_behind_latest")] == (
            quarter_index(latest) - quarter_index(end)
        ), drug
