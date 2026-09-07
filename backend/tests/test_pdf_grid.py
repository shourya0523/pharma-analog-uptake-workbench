"""A page has no table structure; these check the one recovered from it.

The fixtures are drawn rather than loaded: text is placed at coordinates, which
is all a PDF is, so what each test asserts about the reading is traceable to
where the words were put.
"""

from __future__ import annotations

import io

import pymupdf
import pytest

from app.extraction.fingerprint import build_fingerprint, column_periods
from app.parsing.documents import flatten_grid, pdf_table_grids


def page_of(lines: list[tuple[float, list[tuple[float, str]]]]) -> bytes:
    """A one-page PDF with each word placed where it is asked for."""
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    for y, words in lines:
        for x, text in words:
            page.insert_text((x, y), text, fontsize=9)
    raw = document.tobytes()
    document.close()
    return raw


# A heading centred over columns wider than itself, which is what a filing
# looks like and what markup would have stated outright.
SCHEDULE = [
    (100, [(60, "($MM)")]),
    (120, [(250, "FOURTH QUARTER"), (400, "TWELVE MONTHS")]),
    (140, [(250, "2016"), (320, "2015"), (400, "2016"), (470, "2015")]),
    (160, [(60, "Alfacept")]),
    (180, [(60, "US"), (255, "151"), (325, "20"), (405, "471"), (475, "20")]),
    (200, [(60, "Intl"), (255, "49"), (325, "10"), (405, "101"), (475, "10")]),
    (220, [(60, "WW"), (255, "200"), (325, "30"), (405, "572"), (475, "30")]),
]


@pytest.fixture
def schedule() -> list[list[str | None]]:
    _blocks, grids = pdf_table_grids(page_of(SCHEDULE))
    assert grids, "expected the page to yield a table"
    return grids[0]


def test_the_rows_come_back_in_the_columns_they_were_printed_in(schedule):
    body = [row for row in schedule if row[0] in {"US", "Intl", "WW"}]
    assert [[cell for cell in row if cell] for row in body] == [
        ["US", "151", "20", "471", "20"],
        ["Intl", "49", "10", "101", "10"],
        ["WW", "200", "30", "572", "30"],
    ]


def test_a_heading_reaches_the_figures_it_stands_over(schedule):
    """Its own width covers two columns; the figures beneath it are four."""
    periods = column_periods(schedule)
    assert len({period for period in periods.values()}) == 4
    assert sorted(periods.values()) == [
        (3, 12, 2015), (3, 12, 2016), (12, 12, 2015), (12, 12, 2016),
    ]


def test_the_column_of_labels_is_offered_to_no_heading(schedule):
    """Whatever a period covers, it is never the column naming the products."""
    label_column = next(
        index for index, cell in enumerate(schedule[-1]) if cell == "WW"
    )
    assert label_column not in column_periods(schedule)


def test_the_page_says_what_its_numbers_are_worth(schedule):
    fingerprint = build_fingerprint(flatten_grid(schedule), "", grid=schedule)
    assert fingerprint.unit_label == "millions" and fingerprint.unit_declared


def test_a_second_table_below_a_band_of_white_is_a_second_table():
    """Read as one table, the lower heading's rows take the upper one's columns."""
    restated = SCHEDULE + [
        (400, [(250, "FIRST QUARTER")]),
        (420, [(250, "2017"), (320, "2016")]),
        (440, [(60, "Betamine"), (255, "12"), (325, "4")]),
    ]
    _blocks, grids = pdf_table_grids(page_of(restated))
    assert len(grids) == 2
    assert sorted(column_periods(grids[1]).values()) == [(3, 3, 2016), (3, 3, 2017)]
