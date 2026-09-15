"""Table headings written in quarter notation.

The heading parser knew "three months ended" and "Second Quarter 2024" and
nothing else. A release heads its product table "2Q 2024 | 2Q 2023 | %
Change" under "Global | U.S. | International", and its year-to-date table
"June YTD 2024"; another writes "1Q'26 | 1Q'25". Such a table stated no
period the parser could read, was dated from the document instead - which,
naming the comparative more often, was dated a year early - and every
quarter only that table states was lost.

One vocabulary now: the forms `periods.py` reads off a document are the forms
the heading parser reads off a table, a "<Month> YTD" heading is the span
ending in that month, a geography group above the period row is the column's
scope, and the document's year is the latest of the periods it names
throughout rather than the one it names most.

Invented names: Calderon.
"""

from __future__ import annotations

from app.extraction.extract import read_table
from app.extraction.fingerprint import (
    _named_periods,
    _year_row,
    build_fingerprint,
    column_periods,
    column_scopes,
)
from app.parsing.periods import detect_period_context


def test_quarter_notation_names_a_quarter():
    assert _named_periods("2Q 2024") == [(3, 6)]
    assert _named_periods("Q3 2024 Q3 2023") == [(3, 9), (3, 9)]
    assert _named_periods("1Q'26 1Q'25") == [(3, 3), (3, 3)]


def test_a_month_ytd_heading_is_the_span_ending_in_that_month():
    assert _named_periods("June YTD 2024 June YTD 2023") == [(6, 6), (6, 6)]
    assert _named_periods("September YTD 2024") == [(9, 9)]


def test_a_two_digit_year_beside_a_quarter_is_a_year():
    assert _year_row([["", "1Q'26", "1Q'25", "Change"]]) == [2026, 2025]


def test_a_ragged_table_headed_in_quarter_notation_is_dated_by_its_own_heading():
    rows = [
        ["", "2Q 2024", "2Q 2023", "% Change"],
        ["Calderon", "1,000", "900", "11%"],
    ]
    fingerprint = build_fingerprint(rows, "(in millions)")
    assert [(b.months, b.end_month, b.year) for b in fingerprint.blocks] == [
        (3, 6, 2024), (3, 6, 2023),
    ]
    readout = read_table(rows, product="Calderon", context="(in millions)")
    assert {(v.period, v.value_as_reported) for v in readout.values} == {
        ("2024Q2", 1000.0), ("2023Q2", 900.0),
    }


def test_a_grid_headed_in_quarter_notation_is_read_by_column():
    grid = [
        [None, "2Q 2024", "2Q 2023", "% Change"],
        ["Calderon", "1,000", "900", "11%"],
    ]
    assert column_periods(grid) == {1: (3, 6, 2024), 2: (3, 6, 2023)}


def test_a_geography_group_above_the_period_row_is_the_columns_scope():
    grid = [
        [None, "Global", None, "U.S.", None, "International", None],
        [None, "2Q 2024", "2Q 2023", "2Q 2024", "2Q 2023", "2Q 2024", "2Q 2023"],
        ["Calderon", "1,000", "900", "600", "550", "400", "350"],
    ]
    assert column_scopes(grid) == {
        1: "Worldwide", 2: "Worldwide", 3: "United States", 4: "United States",
        5: "International", 6: "International",
    }
    rows = [[cell or "" for cell in row] for row in grid]
    readout = read_table(rows, product="Calderon", context="(in millions)", grid=grid)
    assert readout.skipped_reason is None, readout.skipped_reason
    # The worldwide column is the product; the regions beside it are never the family.
    assert {(v.period, v.value_as_reported, v.scope) for v in readout.values} == {
        ("2024Q2", 1000.0, None), ("2023Q2", 900.0, None),
    }


def test_the_documents_year_is_the_latest_of_the_periods_it_names_throughout():
    """A release names its comparative on every table too, and often more
    times; naming it most is not what makes a period the document's own."""
    context = detect_period_context("2Q 2023 " * 30 + "2Q 2024 " * 20)
    assert (context.year, context.quarter) == (2024, 2)
    # Guidance named a few times is not named throughout.
    context = detect_period_context("2Q 2024 " * 30 + "2Q 2023 " * 20 + "1Q 2025 " * 2)
    assert (context.year, context.quarter) == (2024, 2)
