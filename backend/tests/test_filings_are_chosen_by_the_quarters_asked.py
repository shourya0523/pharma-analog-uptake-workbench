"""Which filings a job reads is decided by the quarters it was asked for.

The picker used to sort the index annual-first and newest-first and take a
count of rows. For a filer with three annual reports inside the window the
count was spent before any quarter was reached, and the quarterly pages - the
documents that state a quarter - were never fetched at all.

The answer set of a row needs no document opened. An interim report states its
own quarter and the same quarter of the year before it; an annual report
states its fiscal year and the two before it, and a fourth quarter is that year
less the nine months the third-quarter report states, so it costs the pair. A
form that states no period of its own has no answer set: an earnings 8-K's
period of report is the day the results were announced, not the quarter they
are about, and choosing it by that date would file a quarter under the wrong
name. It stays the exhibit pass's job.
"""

from __future__ import annotations

from datetime import date

from app.connectors.sources import (
    IndexedFiling,
    choose_filings,
    is_annual,
    quarter_index,
    quarter_label,
    reports_a_period,
)


def _row(row: int, form: str, period: str) -> IndexedFiling:
    """One index row, as the submissions feed states it: a form and a period
    of report."""
    return IndexedFiling(
        row=row, form=form, annual=is_annual(form),
        quarter=quarter_index(date.fromisoformat(period)),
    )


def _index_of_a_calendar_filer() -> list[IndexedFiling]:
    """Four years of one filer's periodic index, newest first, as EDGAR lists it.

    A calendar fiscal year: the annual report's period of report is 31
    December, the interim reports' are the three quarter ends before it.
    """
    rows: list[IndexedFiling] = []
    periods: list[tuple[str, str]] = []
    for year in (2026, 2025, 2024, 2023):
        periods.append(("10-K", f"{year - 1}-12-31"))
        for month, day in ((9, 30), (6, 30), (3, 31)):
            periods.append(("10-Q", f"{year}-{month:02d}-{day:02d}"))
    for index, (form, period) in enumerate(periods):
        rows.append(_row(index, form, period))
    return rows


def _asked(first: str, last: str) -> list[str]:
    start, stop = (
        quarter_index(date.fromisoformat(first)),
        quarter_index(date.fromisoformat(last)),
    )
    return [quarter_label(q) for q in range(start, stop + 1)]


def test_a_twelve_quarter_window_takes_the_interim_reports_and_what_the_subtraction_needs():
    """Twelve quarters over three years: nine an interim report states, three
    fourth quarters.

    Each interim report states one quarter slot in two adjacent years, so the
    three years of one slot - first quarters, say - are a chain of three that
    two reports cover and one cannot, whichever two are taken. Three slots,
    six reports. The three fourth quarters then cost one annual report between
    them, because it states its year and the two before it, and the nine-month
    columns the subtraction needs belong to third-quarter reports the cover has
    already taken for their own quarters.
    """
    asked = _asked("2023-01-31", "2025-12-31")
    rows = _index_of_a_calendar_filer()
    chosen = choose_filings(rows, asked, ceiling=4)
    forms = [f.form for f in rows if f.row in chosen]
    assert forms.count("10-Q") == 6
    assert forms.count("10-K") == 1
    assert set().union(*chosen.values()) == set(asked), "every quarter asked, covered"
    # Nothing beyond what the cover needs; the index holds sixteen rows.
    assert len(chosen) == 7
    # The third-quarter report is chosen once and answers both its own quarter
    # and the fourth quarter it is subtracted from.
    third_quarter = next(
        f for f in rows if f.row in chosen and f.quarter % 4 == 2
    )
    assert chosen[third_quarter.row] == ["2024Q3", "2024Q4", "2025Q3", "2025Q4"]


def test_a_quarter_another_row_already_answers_is_not_fetched_for_twice():
    """One quarter, and two rows state it - its own interim report and the
    next year's, as a comparative. One of them is taken."""
    asked = ["2025Q2"]
    chosen = choose_filings(_index_of_a_calendar_filer(), asked, ceiling=4)
    assert len(chosen) == 1
    assert list(chosen.values()) == [["2025Q2"]]


def test_an_earnings_filing_is_never_chosen_by_its_period_of_report():
    """An item 2.02 8-K's period of report is the date of the announcement. A
    row whose form states no period of its own has no answer set, so it is
    never chosen here whatever date it carries."""
    announcement = _row(0, "8-K", "2025-08-05")
    assert not reports_a_period(announcement.form)
    rows = [announcement, *(f._replace(row=f.row + 1) for f in _index_of_a_calendar_filer())]
    chosen = choose_filings(rows, _asked("2025-04-01", "2025-09-30"), ceiling=4)
    assert 0 not in chosen, "the announcement is the exhibit pass's job"
    assert chosen, "and the quarters it was not chosen for are still covered"


def test_a_fourth_quarter_costs_the_pair_or_it_is_not_covered():
    """The annual report states the year; the nine months it is taken from are
    a column of the third-quarter report. Neither alone is chosen for it."""
    asked = ["2025Q4"]
    annual = _row(0, "10-K", "2025-12-31")
    third_quarter = _row(1, "10-Q", "2025-09-30")
    assert choose_filings([annual], asked, ceiling=4) == {}
    assert choose_filings([third_quarter], asked, ceiling=4) == {}
    both = choose_filings([annual, third_quarter], asked, ceiling=4)
    assert both == {0: ["2025Q4"], 1: ["2025Q4"]}
    # The ceiling is per quarter, and this quarter costs two filings.
    assert choose_filings([annual, third_quarter], asked, ceiling=1) == {}


def test_a_quarter_no_filing_in_the_index_reports_is_left_uncovered():
    """An issuer's first filing is its first filing. A quarter before it is a
    quarter nothing answers, and the cover says so by leaving it out rather
    than by fetching the nearest row to it."""
    rows = [_row(0, "10-Q", "2025-09-30")]
    chosen = choose_filings(rows, _asked("2019-01-31", "2025-09-30"), ceiling=4)
    assert set().union(*chosen.values()) == {"2024Q3", "2025Q3"}


def test_the_cover_follows_the_filer_rather_than_the_calendar():
    """A fiscal year ending in June has its fourth quarter in April-June and
    its third-quarter report at the end of March. Nothing here is told that;
    the annual row's own period of report is what says where the year ends."""
    annual = _row(0, "10-K", "2026-06-30")
    third_quarter = _row(1, "10-Q", "2026-03-31")
    chosen = choose_filings([annual, third_quarter], ["2026Q2"], ceiling=4)
    assert chosen == {0: ["2026Q2"], 1: ["2026Q2"]}
