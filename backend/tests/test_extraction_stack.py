"""Tests for the fingerprint -> read -> normalize -> check extraction stack.

The two defect cases below are not hypothetical. Both shipped into the gold
dataset and survived its full test suite, so they are pinned here against the
code that is supposed to make them impossible.
"""

from __future__ import annotations

from app.extraction.candidates import extract_revenue_candidates
from app.extraction.check import run_checks
from app.extraction.extract import map_values_to_blocks, read_table, tokenize_row
from app.extraction.fingerprint import PeriodBlock, build_fingerprint
from app.extraction.process import Datapoint, normalize_all

UTHR_THOUSANDS = [
    ["", "Three Months Ended September 30,", "", "", ""],
    ["(in thousands)", "2015", "2014", "% Change", ""],
    ["Tyvaso ®", "121,718", "119,685", "1.7", "%"],
]

# Same issuer, same exhibit layout, one year later - stated in millions.
UTHR_MILLIONS = [
    ["", "Three Months Ended September 30,", "", "", ""],
    ["($ in millions)", "2016", "2015", "% Change", ""],
    ["Tyvaso ®", "101.8", "121.7", "(16.4", ")%"],
]

MERCK_QUARTER_AND_YTD = [
    ["($ in millions)", "Three Months Ended June 30,", "", "Six Months Ended June 30,", ""],
    ["", "2024", "2023", "2024", "2023"],
    ["Winrevair", "70", "-", "70", "-"],
]


def test_unit_comes_from_the_table_not_the_filing_date():
    """The 2016 exhibit says millions; nothing may assume otherwise."""
    thousands = build_fingerprint(UTHR_THOUSANDS)
    millions = build_fingerprint(UTHR_MILLIONS)

    assert thousands.unit_label == "thousands"
    assert thousands.unit_scale_to_millions == 0.001
    assert millions.unit_label == "millions"
    assert millions.unit_scale_to_millions == 1.0
    # Two layouts that differ only in unit must not share a signature, so a
    # recipe learned for one is never reused for the other.
    assert thousands.signature != millions.signature


def test_undeclared_unit_yields_nothing_rather_than_a_guess():
    rows = [
        ["", "Three Months Ended September 30,", ""],
        ["", "2016", "2015"],
        ["Tyvaso", "101.8", "121.7"],
    ]
    fingerprint = build_fingerprint(rows)
    assert not fingerprint.unit_declared
    assert not fingerprint.usable
    assert read_table(rows, product="Tyvaso").values == []


def test_reported_values_normalize_to_the_same_scale_across_a_unit_change():
    """Both exhibits land near $100M; neither quarter becomes $0.1M."""
    thousands = normalize_all(read_table(UTHR_THOUSANDS, product="Tyvaso").values)
    millions = normalize_all(read_table(UTHR_MILLIONS, product="Tyvaso").values)

    from_thousands = {p.period: p.value_normalized_usd_millions for p in thousands}
    from_millions = {p.period: p.value_normalized_usd_millions for p in millions}

    assert from_thousands["2015Q3"] == 121.718
    assert from_millions["2016Q3"] == 101.8
    # The same quarter read from either exhibit agrees to the precision each
    # one printed: 121,718 thousands against a restated 121.7 million.
    assert abs(from_thousands["2015Q3"] - from_millions["2015Q3"]) < 0.05


def test_year_to_date_column_is_never_emitted_as_a_quarter():
    """Merck's six-month column sits beside the quarter and must stay YTD."""
    readout = read_table(MERCK_QUARTER_AND_YTD, product="Winrevair")
    by_period = {(v.period, v.period_type): v.value_as_reported for v in readout.values}

    assert by_period[("2024Q2", "quarterly")] == 70.0
    assert by_period[("2024", "six_month")] == 70.0
    assert not any(v.period_type == "quarterly" and v.period == "2024" for v in readout.values)

    candidates, _, _ = extract_revenue_candidates(
        [MERCK_QUARTER_AND_YTD], product="Winrevair"
    )
    assert [c["period"] for c in candidates] == ["2024Q2"]


def test_dash_holds_its_column_so_later_values_do_not_shift_left():
    """A printed dash is a column, not an absence.

    Collapsing it is the mechanism that moved Merck's 2024 figures one quarter
    left and booked the full-year total as Q4.
    """
    assert tokenize_row(["70", "-", "70", "-"]) == [70.0, None, 70.0, None]

    blocks = (
        PeriodBlock(months=3, end_month=6, year=2024, value_index=0),
        PeriodBlock(months=3, end_month=6, year=2023, value_index=1),
        PeriodBlock(months=6, end_month=6, year=2024, value_index=2),
        PeriodBlock(months=6, end_month=6, year=2023, value_index=3),
    )
    assigned, reason = map_values_to_blocks([70.0, None, 70.0, None], blocks)
    assert reason is None
    # Columns 0 and 2 keep their values; the dashed columns report nothing.
    assert assigned == {0: 70.0, 2: 70.0}


def test_ambiguous_layout_is_refused_rather_than_guessed():
    blocks = (
        PeriodBlock(months=3, end_month=6, year=2024, value_index=0),
        PeriodBlock(months=3, end_month=6, year=2023, value_index=1),
    )
    # A third number that is not the change between the first two means the row
    # is not laid out the way the header declared.
    assigned, reason = map_values_to_blocks([70.0, 149.0, 999.0], blocks)
    assert assigned is None
    assert reason == "unverified_extra_columns"


def _point(period, value, period_type="quarterly", quote=None):
    return Datapoint(
        product_label="Tyvaso",
        period=period,
        period_type=period_type,
        value_normalized_usd_millions=value,
        value_as_reported=value,
        source_unit="millions",
        source_currency="USD",
        fx_rate_to_usd=None,
        source_quote=quote if quote is not None else f"Tyvaso | {value}",
        fingerprint_signature="sig",
        normalization_status="ok",
    )


def test_check_catches_a_thousandfold_scale_break():
    points = [_point("2015Q4", 119.13), _point("2016Q1", 0.1022)]
    codes = {finding.code for finding in run_checks(points)}
    assert "scale_continuity" in codes


def test_check_catches_a_total_recorded_as_a_quarter():
    """Q1-Q4 that sum to twice the stated annual total is the Merck defect."""
    points = [
        _point("2024Q1", 70),
        _point("2024Q2", 149),
        _point("2024Q3", 200),
        _point("2024Q4", 419),
        _point("2024", 419, period_type="annual"),
    ]
    codes = {finding.code for finding in run_checks(points)}
    assert "quarters_sum_to_period_total" in codes


def test_checks_pass_on_a_correct_series():
    points = [
        _point("2024Q2", 70),
        _point("2024Q3", 149),
        _point("2024Q4", 200),
        _point("2024", 419, period_type="annual"),
    ]
    assert [f for f in run_checks(points) if f.severity == "error"] == []


def test_non_usd_filing_is_converted_not_passed_through_as_dollars():
    rows = [
        ["Sales by product, in CHF millions", "Year ended December 31,", ""],
        ["", "2010", "2009"],
        ["Tracleer", "1,636.1", "1,508.0"],
    ]
    points = normalize_all(read_table(rows, product="Tracleer").values)
    by_period = {p.period: p for p in points}

    tracleer_2010 = by_period["2010"]
    assert tracleer_2010.source_currency == "CHF"
    assert tracleer_2010.value_as_reported == 1636.1
    # 2010 rate is 0.9670 USD per CHF, so the comparable figure is lower than
    # the franc figure - passing it through unconverted would overstate it.
    assert tracleer_2010.value_normalized_usd_millions == 1582.1087
    assert tracleer_2010.fx_rate_to_usd == 0.9670
