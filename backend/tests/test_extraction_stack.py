"""Tests for the fingerprint -> read -> normalize -> check extraction stack.

The two defect cases below are not hypothetical. Both shipped into the gold
dataset and survived its full test suite, so they are pinned here against the
code that is supposed to make them impossible.
"""

from __future__ import annotations

import json
import pathlib

from app.extraction.candidates import extract_revenue_candidates
from app.extraction.check import run_checks
from app.extraction.extract import map_values_to_blocks, read_table, tokenize_row
from app.extraction.fingerprint import PeriodBlock, build_fingerprint
from app.extraction.process import Datapoint, normalize_all
from app.parsing.documents import flatten_grid, html_table_grid
from bs4 import BeautifulSoup
from app.extraction.adjudicate import (
    Candidate,
    adjudicate_positional_solutions,
    adjudicate_reported_value,
    adjudicate_split_ownership_quarter,
    adjudicate_total_against_parts,
)

EXHIBIT_IN_THOUSANDS = [
    ["", "Three Months Ended September 30,", "", "", ""],
    ["(in thousands)", "2015", "2014", "% Change", ""],
    ["Tyvaso ®", "121,718", "119,685", "1.7", "%"],
]

# Same issuer, same exhibit layout, one year later - stated in millions.
EXHIBIT_IN_MILLIONS = [
    ["", "Three Months Ended September 30,", "", "", ""],
    ["($ in millions)", "2016", "2015", "% Change", ""],
    ["Tyvaso ®", "101.8", "121.7", "(16.4", ")%"],
]

QUARTER_AND_YTD = [
    ["($ in millions)", "Three Months Ended June 30,", "", "Six Months Ended June 30,", ""],
    ["", "2024", "2023", "2024", "2023"],
    ["Winrevair", "70", "-", "70", "-"],
]


def test_unit_comes_from_the_table_not_the_filing_date():
    """The 2016 exhibit says millions; nothing may assume otherwise."""
    thousands = build_fingerprint(EXHIBIT_IN_THOUSANDS)
    millions = build_fingerprint(EXHIBIT_IN_MILLIONS)

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


def test_an_amount_in_the_prose_is_not_the_table_s_unit_declaration():
    """A quoted "one billion dollars" must not outrank "(dollars in thousands)".

    Shape of a real earnings exhibit: the table itself carries no unit, so the
    unit has to come from the surrounding prose - and that prose talks about
    money in two different ways. The parenthetical above the table declares the
    unit its numbers are printed in; the CEO quote further up states an amount.
    Only the first is a declaration. Reading the second as one multiplies every
    figure in the table by a billion instead of a thousand, and the result is a
    plausible-looking number rather than a refusal, so nothing downstream can
    catch it.
    """
    context = (
        "UNITED THERAPEUTICS CORPORATION REPORTS THIRD QUARTER 2012 RESULTS\n"
        "Total Revenues of $242.5 million\n"
        "\u201cIt is certainly gratifying to see us closely approach, for the "
        "first time, a revenue run rate of one billion dollars per year,\u201d "
        "said the Chairman and Chief Executive Officer.\n"
        "The table below summarizes the components of net revenues "
        "(dollars in thousands):"
    )
    rows = [
        ["", "", "Three Months Ended September 30,", "", "Percentage", ""],
        ["", "", "2012", "", "2011", "", "Change", ""],
        ["Cardiopulmonary products:", "", "", "", "", "", "", ""],
        ["Remodulin", "", "$", "120,811", "", "$", "114,918", "", "5.1", "%"],
        ["Tyvaso", "", "88,302", "", "66,330", "", "33.1", "%"],
        ["Adcirca", "", "31,804", "", "19,772", "", "60.9", "%"],
    ]

    fingerprint = build_fingerprint(rows, context)
    assert fingerprint.unit_label == "thousands"

    candidates, _findings, _skipped = extract_revenue_candidates(
        [rows], product="Remodulin", context=context
    )
    quarter = [c for c in candidates if c["period"] == "2012Q3"]
    assert [c["value_normalized_usd_millions"] for c in quarter] == [120.811]


def test_a_stated_amount_alone_declares_no_unit_at_all():
    """With no declaration anywhere, the table is refused, not guessed at.

    The generic form of the defect above: an amount written out in prose is a
    quantity, not a statement about how the table's numbers are scaled. A
    document that only ever states amounts has declared nothing, and the
    fingerprint has to say so - an undeclared table yields no values.
    """
    rows = [
        ["", "Three Months Ended September 30,", ""],
        ["", "2012", "2011"],
        ["Remodulin", "120,811", "114,918"],
    ]
    for prose in (
        "a revenue run rate of one billion dollars per year",
        "total revenues of $242.5 million for the quarter",
        "the programme has cost hundreds of millions of dollars to date",
    ):
        fingerprint = build_fingerprint(rows, prose)
        assert not fingerprint.unit_declared, prose
        assert read_table(rows, product="Remodulin", context=prose).values == []


def test_a_currency_code_is_a_whole_uppercase_word():
    """An all-caps dateline is not a currency followed by a magnitude.

    The ISO-code alternative is what lets "in CHF millions" read as a
    declaration, and it matches three uppercase letters. Three letters left
    unanchored also sit inside longer ones, so a press release headed
    "AMGEN REPORTS THIRD QUARTER 2025 FINANCIAL RESULTS / THOUSAND OAKS, Calif."
    offers "...RESU|LTS THOUSAND" as "<currency> thousand", and every figure in
    the release comes out a thousandth of what the issuer printed.
    """
    rows = [
        ["", "Three months ended September 30,", ""],
        ["", "2025", "2024"],
        ["Repatha", "794", "567"],
    ]
    context = (
        "News Release One Amgen Center Drive Thousand Oaks, CA 91320-1799\n"
        "AMGEN REPORTS THIRD QUARTER 2025 FINANCIAL RESULTS\n"
        "THOUSAND OAKS, Calif. (Nov. 4, 2025) - Amgen today announced results."
    )
    fingerprint = build_fingerprint(rows, context)
    assert not fingerprint.unit_declared
    assert read_table(rows, product="Repatha", context=context).values == []


def test_a_currency_between_in_and_the_magnitude_is_still_a_declaration():
    """The fix must not cost the forms issuers really print.

    A currency legitimately sits between "in" and the magnitude word, including
    as a three-letter ISO code, and those are declarations.
    """
    rows = [
        ["", "Three Months Ended September 30,", ""],
        ["", "2012", "2011"],
        ["Tracleer", "120,811", "114,918"],
    ]
    for prose, expected in (
        ("in CHF millions", "millions"),
        ("in USD thousands", "thousands"),
        ("in millions of U.S. dollars", "millions"),
        ("(dollars in thousands)", "thousands"),
        ("$ in billions", "billions"),
        ("in thousands of Swiss francs", "thousands"),
    ):
        fingerprint = build_fingerprint(rows, prose)
        assert fingerprint.unit_declared, prose
        assert fingerprint.unit_label == expected, prose


def test_reported_values_normalize_to_the_same_scale_across_a_unit_change():
    """Both exhibits land near $100M; neither quarter becomes $0.1M."""
    thousands = normalize_all(read_table(EXHIBIT_IN_THOUSANDS, product="Tyvaso").values)
    millions = normalize_all(read_table(EXHIBIT_IN_MILLIONS, product="Tyvaso").values)

    from_thousands = {p.period: p.value_normalized_usd_millions for p in thousands}
    from_millions = {p.period: p.value_normalized_usd_millions for p in millions}

    assert from_thousands["2015Q3"] == 121.718
    assert from_millions["2016Q3"] == 101.8
    # The same quarter read from either exhibit agrees to the precision each
    # one printed: 121,718 thousands against a restated 121.7 million.
    assert abs(from_thousands["2015Q3"] - from_millions["2015Q3"]) < 0.05


def test_year_to_date_column_is_never_emitted_as_a_quarter():
    """A six-month column sits beside the quarter and must stay YTD."""
    readout = read_table(QUARTER_AND_YTD, product="Winrevair")
    by_period = {(v.period, v.period_type): v.value_as_reported for v in readout.values}

    assert by_period[("2024Q2", "quarterly")] == 70.0
    assert by_period[("2024", "six_month")] == 70.0
    assert not any(v.period_type == "quarterly" and v.period == "2024" for v in readout.values)

    candidates, _, _ = extract_revenue_candidates(
        [QUARTER_AND_YTD], product="Winrevair"
    )
    # The six-month figure comes through - a fourth quarter is derived by
    # subtracting from a total, so withholding totals is what loses Q4 - but it
    # comes through *as* a six-month figure. Being emitted and being emitted as
    # a quarter are different claims, and only the second is the error.
    assert [c["period"] for c in candidates] == ["2024Q2", "2024"]
    quarterly = [c for c in candidates if c["period_type"] == "quarterly"]
    assert [c["period"] for c in quarterly] == ["2024Q2"]
    assert all(c["period_type"] != "quarterly" for c in candidates if c["period"] == "2024")

    # A caller wanting quarters alone selects them, in the one line the
    # orchestrator already writes. The reader has no say in it.
    quarters_only = [c for c in candidates if c["period_type"] == "quarterly"]
    assert [c["period"] for c in quarters_only] == ["2024Q2"]


def test_dash_holds_its_column_so_later_values_do_not_shift_left():
    """A printed dash is a column, not an absence.

    Collapsing it is the mechanism that moves a schedule's figures one quarter
    left and books the full-year total as Q4.
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
    """Q1-Q4 that sum to twice the stated annual total is the column defect."""
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


# --- reading figures that are not in a delimited table ------------------------


def test_sentence_states_its_own_unit_and_period():
    """Older filings predate the product-sales exhibit and state sales in prose."""
    from app.extraction.prose import read_prose

    values = read_prose(
        "Remodulin revenues for the quarter ended June 30, 2002 were "
        "approximately $8.7 million.",
        product="Remodulin",
    )
    assert len(values) == 1
    assert values[0].period == "2002Q2"
    assert values[0].value_as_reported == 8.7
    assert values[0].unit_label == "millions"
    assert values[0].currency == "USD"


def test_sentence_naming_several_periods_is_refused():
    """Which amount belongs to which period is not inferable from proximity."""
    from app.extraction.prose import read_prose

    values = read_prose(
        "For the years ended December 31, 2016 and December 31, 2015 we "
        "recognized $404.6 million and $470.1 million in Tyvaso net product sales.",
        product="Tyvaso",
    )
    assert values == []


def test_flattened_pdf_block_keeps_its_geographies_apart():
    """PDF extraction loses the grid; the scope labels still separate the rows."""
    from app.extraction.positional import read_positional_block

    rows = read_positional_block(
        "UPTRAVI US 102 91 77 68 56 35 236 Intl 8 9 8 4 1 - 13 "
        "WW 110 100 85 72 57 35 249",
        product="Uptravi",
    )
    by_scope = {row.scope: row.values for row in rows}

    assert by_scope["United States"][0] == 102.0
    assert by_scope["Worldwide"][0] == 110.0
    # The dash is a period with nothing to report, and holds its column so the
    # values after it stay on their own periods.
    assert by_scope["International"] == (8.0, 9.0, 8.0, 4.0, 1.0, None, 13.0)
    # US and worldwide must never be merged into one series.
    assert by_scope["United States"] != by_scope["Worldwide"]


# --- completing a series from the issuer's own arithmetic ---------------------


def test_unstated_fourth_quarter_is_derived_from_the_annual_total():
    """Issuers often report three quarters and a year; Q4 is the difference."""
    from app.extraction.derive import complete_quarters_from_totals

    points = [
        _point("2003Q1", 8.546),
        _point("2003Q2", 11.729),
        _point("2003Q3", 12.852),
        _point("2003", 45.121, period_type="annual"),
    ]
    derived = complete_quarters_from_totals(points)
    assert len(derived) == 1
    assert derived[0].period == "2003Q4"
    assert abs(derived[0].value_normalized_usd_millions - 11.994) < 1e-6
    # Provenance says the pipeline computed it, not that the issuer printed it.
    assert derived[0].normalization_status == "derived_from_period_total"


def test_two_missing_quarters_derive_nothing():
    """One equation cannot resolve two unknowns, so neither is invented."""
    from app.extraction.derive import complete_quarters_from_totals

    points = [
        _point("2002Q2", 8.7),
        _point("2002Q3", 2.6),
        _point("2002", 21.174, period_type="annual"),
    ]
    assert complete_quarters_from_totals(points) == []


def test_totals_that_contradict_their_quarters_derive_nothing():
    """A negative residual means the inputs disagree; report no figure."""
    from app.extraction.derive import complete_quarters_from_totals

    points = [
        _point("2024Q1", 100.0),
        _point("2024Q2", 100.0),
        _point("2024Q3", 100.0),
        _point("2024", 250.0, period_type="annual"),
    ]
    assert complete_quarters_from_totals(points) == []


def test_family_total_resolves_the_sole_formulation_before_a_split():
    """Tyvaso was nebulized-only until the DPI inhaler launched in 2022Q2."""
    from app.extraction.derive import propagate_sole_formulation

    family = [_point("2021Q4", 119.7), _point("2022Q1", 172.0), _point("2022Q2", 198.0)]
    derived = propagate_sole_formulation(
        family, formulation_periods={"2022Q2", "2022Q3"}, formulation_label="Nebulized Tyvaso"
    )
    periods = {p.period for p in derived}

    # Pre-split quarters carry over; once both formulations sell, the family
    # total no longer identifies either one.
    assert periods == {"2021Q4", "2022Q1"}
    assert all(p.product_label == "Nebulized Tyvaso" for p in derived)
    assert all(p.normalization_status == "derived_sole_formulation" for p in derived)


def test_prose_reads_a_full_year_total():
    """"Full-year 2002" is as common as "year ended", and unlocks derivation."""
    from app.extraction.prose import read_prose

    values = read_prose(
        "Full-year 2002 Remodulin revenue was $21.174 million.", product="Remodulin"
    )
    assert len(values) == 1
    assert values[0].period == "2002"
    assert values[0].period_type == "annual"
    assert values[0].value_as_reported == 21.174


def test_prose_pairs_quarter_and_year_to_date_when_the_sentence_says_respectively():
    """The most common issuer construction of all, and it is not ambiguous.

    An issuer states a product as "$336 million and $615 million in the second
    quarter and first six months of 2025, respectively" every quarter. Refusing
    it as multi-period leaves a whole product unreadable even though the
    sentence states the correspondence outright. Neither half matches the single-period
    patterns either: the quarter's year only appears after the second phrase.
    """
    from app.extraction.prose import read_prose

    values = read_prose(
        "Sales of Winrevair were $336 million and $615 million in the second "
        "quarter and first six months of 2025, respectively, primarily "
        "reflecting continued uptake in the U.S.",
        product="Winrevair",
    )
    assert [(v.period, v.period_type, v.value_as_reported) for v in values] == [
        ("2025Q2", "quarterly", 336.0),
        ("2025", "six_month", 615.0),
    ]


def test_prose_pairing_needs_the_word_that_states_the_correspondence():
    """Without "respectively" the same sentence is back to guessing by proximity.

    This is the guard on the exception above: the pairing is read because the
    sentence declares it, not because two numbers happen to precede two dates.
    """
    from app.extraction.prose import read_prose

    values = read_prose(
        "Sales of Winrevair were $336 million and $615 million in the second "
        "quarter and first six months of 2025.",
        product="Winrevair",
    )
    assert values == []


def test_prose_pairing_refuses_a_mismatched_count():
    """Three periods and two amounts does not say which period was dropped."""
    from app.extraction.prose import read_prose

    values = read_prose(
        "Sales of Winrevair were $360 million and $976 million in the third "
        "quarter and first nine months of 2025 and full-year 2024, respectively.",
        product="Winrevair",
    )
    assert values == []


def test_launch_year_total_covers_only_quarters_since_launch():
    """A product's first year has no pre-launch quarters to account for.

    A product goes on sale in 2002Q2, so its full-year 2002 total is
    Q2 + Q3 + Q4. Requiring all four quarters made the launch year look
    under-determined - two "missing" quarters instead of one - so it never
    derived, even though the annual figure was cited.
    """
    from app.extraction.derive import complete_quarters_from_totals

    points = [
        _point("2002Q2", 8.7),
        _point("2002Q3", 2.6),
        _point("2002", 21.174, period_type="annual"),
    ]
    assert complete_quarters_from_totals(points) == []

    derived = complete_quarters_from_totals(points, commercial_start="2002Q2")
    assert [(p.period, round(p.value_normalized_usd_millions, 3)) for p in derived] == [
        ("2002Q4", 9.874)
    ]


def test_a_total_from_before_launch_derives_nothing():
    """A year the product did not sell in says nothing about any quarter."""
    from app.extraction.derive import complete_quarters_from_totals

    points = [
        _point("2001", 5.0, period_type="annual"),
        _point("2002Q2", 8.7),
    ]
    assert complete_quarters_from_totals(points, commercial_start="2002Q2") == []


def test_a_geography_column_table_is_refused_not_read_as_periods():
    """The dangerous near-miss: columns that look like periods but are places.

    An XBRL product table splits each year into U.S. / Int'l / Total, so
    the row reads "- | 55 | 55 | - | 56 | 56" - six numbers, none of which is a
    quarter. Aligned against the usual convention the first column would be
    read as the current quarter, turning a U.S. figure of nothing into the
    reported value. Refusing is the only safe answer, and it has to stay
    refused rather than becoming a silent guess later.
    """
    from app.extraction.extract import tokenize_row

    tokens = tokenize_row(["-", "55", "55", "-", "56", "56"])
    # The leading dash holds its position rather than collapsing, which is what
    # makes the refusal detectable instead of shifting 55 into first place.
    assert tokens[0] is None
    assert tokens[1] == 55.0


def test_a_geography_row_table_still_reads_normally():
    """The shape that does work, kept beside the one that does not.

    A filer writes one row per geography and keeps periods in the columns, so
    "CALDERON | U.S. | 373 | 328 | 729 | 601" is quarter, prior-year quarter,
    year-to-date, prior year-to-date - the ordinary convention, and readable.
    The distinction is what the row varies across, not whether a geography is
    named in it.
    """
    from app.extraction.extract import tokenize_row

    tokens = tokenize_row(["U.S.", "$ 373", "328", "$ 729", "601"])
    assert [t for t in tokens if t is not None][:4] == [373.0, 328.0, 729.0, 601.0]


def test_a_quarter_split_by_an_acquisition_is_assembled_from_dated_parts():
    """The one quarter shape no single filing reports.

    When a company changes hands mid-quarter the seller's last schedule stops
    at the closing date and the buyer's first one starts there. The acquired
    products' 2017Q2 exists only as two partial figures, and adding them is
    only safe if the parts are known to tile the quarter.
    """
    from app.extraction.derive import assemble_split_ownership_quarter

    parts = [
        {"covers": "2017-04-01/2017-06-15", "value": 216.0},
        {"covers": "2017-06-16/2017-07-02", "value": 45.0},
    ]
    assert assemble_split_ownership_quarter("2017Q2", parts) == 261.0
    # Order of the parts is not the caller's responsibility.
    assert assemble_split_ownership_quarter("2017Q2", list(reversed(parts))) == 261.0


def test_the_assembler_refuses_parts_that_do_not_tile_the_quarter():
    """Every rejection here is a way two real numbers add up to a wrong one."""
    from app.extraction.derive import assemble_split_ownership_quarter

    def parts(first_end, second_start, second_end="2017-07-02"):
        return [
            {"covers": f"2017-04-01/{first_end}", "value": 216.0},
            {"covers": f"{second_start}/{second_end}", "value": 45.0},
        ]

    # The closing day counted on both sides.
    assert assemble_split_ownership_quarter("2017Q2", parts("2017-06-16", "2017-06-16")) is None
    # A day neither issuer reported.
    assert assemble_split_ownership_quarter("2017Q2", parts("2017-06-14", "2017-06-16")) is None
    # Only one side of the quarter.
    assert assemble_split_ownership_quarter(
        "2017Q2", [{"covers": "2017-04-01/2017-06-15", "value": 216.0}]
    ) is None
    # Starts after the quarter does.
    assert assemble_split_ownership_quarter(
        "2017Q2",
        [
            {"covers": "2017-04-15/2017-06-15", "value": 216.0},
            {"covers": "2017-06-16/2017-07-02", "value": 45.0},
        ],
    ) is None
    # A whole extra month is a period mismatch, not a fiscal calendar.
    assert assemble_split_ownership_quarter(
        "2017Q2", parts("2017-06-15", "2017-06-16", "2017-07-31")
    ) is None


def test_the_assembler_allows_a_fiscal_quarter_end_that_is_not_a_month_end():
    """J&J's fiscal Q2 2017 ended July 2, so no bridge is exactly calendar Q2.

    Refusing the two-day overshoot would mean having no value for the quarter
    at all, which is worse than a bounded and documented imprecision. Refusing
    it silently would be worse still.
    """
    from app.extraction.derive import assemble_split_ownership_quarter

    parts = [
        {"covers": "2017-04-01/2017-06-15", "value": 216.0},
        {"covers": "2017-06-16/2017-07-02", "value": 45.0},
    ]
    assert assemble_split_ownership_quarter("2017Q2", parts) == 261.0
    assert assemble_split_ownership_quarter("2017Q2", parts, fiscal_slack_days=1) is None


def test_prose_reads_an_amount_written_out_in_dollars():
    """A figure too small for millions is printed in full, and still counts."""
    from app.extraction.prose import read_prose

    sentence = (
        "Sales of Remodulin totaled approximately $205,000 in the three months "
        "ended March 31, 2002."
    )
    values = read_prose(sentence, product="Remodulin")
    assert len(values) == 1
    assert values[0].period == "2002Q1"
    assert values[0].value_as_reported == 205000.0
    assert values[0].unit_label == "units"


def test_prose_does_not_treat_every_bare_number_as_money():
    """The guard that keeps the looser amount pattern from over-reading.

    Without both conditions - a currency symbol and thousands separators - a
    filing's bare years, section numbers and thresholds would all become
    amounts, which is a far worse failure than missing one small figure.
    """
    from app.extraction.prose import _amounts_in

    assert _amounts_in("Remodulin sales were 205000 in the quarter") == []
    assert _amounts_in("royalties on net sales in excess of $25.0 million") == [
        (25.0, "millions", "USD")
    ]
    assert _amounts_in("approximately $205,000 of revenues") == [
        (205000.0, "units", "USD")
    ]


def test_prose_pairs_an_alternating_enumeration_without_a_pairing_word():
    """Amount, period, amount, period - the structure states the pairing."""
    from app.extraction.prose import read_prose

    sentence = (
        "Sales of Remodulin totaled approximately $205,000 in the three months "
        "ended March 31, 2002, approximately $8.7 million in the three months "
        "ended June 30, 2002, and approximately $2.6 million in the three "
        "months ended September 30, 2002."
    )
    values = {v.period: v.value_as_reported for v in read_prose(sentence, product="Remodulin")}
    assert values == {"2002Q1": 205000.0, "2002Q2": 8.7, "2002Q3": 2.6}


def test_prose_leaves_a_sentence_that_does_not_strictly_alternate():
    """Two amounts in a row is not an enumeration, and is not guessed at."""
    from app.extraction.prose import read_prose

    sentence = (
        "Remodulin sales of $8.7 million and $2.6 million were recorded in the "
        "three months ended June 30, 2002."
    )
    assert read_prose(sentence, product="Remodulin") == []


def _adjudication_cases():
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "seed" / "gold" / "adjudication_cases.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_edge_case_fixtures_reach_their_expected_verdicts():
    """Every fixture, replayed through the adjudicator."""
    for case in _adjudication_cases():
        status, code = run_case(case)
        assert (status, code) == (
            case["expect"]["status"],
            case["expect"]["code"],
        ), case["case_id"]


def test_no_real_series_trips_the_adjudicator():
    """The false-positive guard, and the more important half of this feature.

    A pipeline that asks for review whenever it is unsure is not careful, it is
    noise, and the flags stop being read. Every complete year and every bridged
    quarter in the real catalog has to resolve cleanly; if a change to the
    thresholds starts flagging healthy data, this fails and the thresholds are
    what is wrong.
    """
    tripped = real_rows_that_trip()
    assert tripped == [], f"adjudicator flagged real data: {tripped}"


def test_most_edge_cases_are_things_that_actually_happened():
    """Fixtures should be evidence, not imagination.

    A suite of invented contradictions would prove only that the code has
    branches. The majority here are situations observed in the documents this
    dataset is built from - several found by this repo's own evals - and the
    rest are labelled as constructed so nobody mistakes them for evidence that
    issuers routinely publish nonsense.
    """
    cases = _adjudication_cases()
    provenance = {case["provenance"] for case in cases}
    assert provenance <= {"observed", "constructed"}
    observed = [case for case in cases if case["provenance"] == "observed"]
    assert len(observed) > len(cases) / 2
    for case in cases:
        assert len(case["why"]) > 80, case["case_id"]


def test_the_verdict_vocabulary_is_closed():
    """Three statuses, and each fixture's code is reachable from the module."""
    from app.extraction import adjudicate

    assert {adjudicate.RESOLVED, adjudicate.NEEDS_REVIEW, adjudicate.IMPOSSIBLE} == {
        "resolved",
        "needs_review",
        "impossible",
    }
    for case in _adjudication_cases():
        assert case["expect"]["status"] in {"resolved", "needs_review", "impossible"}


def test_rounding_between_a_total_and_its_own_parts_is_never_a_contradiction():
    """The single most important non-firing case.

    Issuers round each published period independently, so a stated total and
    the sum of its parts differ by about a unit routinely. If that were
    reported as a defect the warning would fire on most healthy years in this
    dataset and be worthless.
    """
    from app.extraction.adjudicate import adjudicate_total_against_parts

    # Real figures from a filing: stated nine months 229, quarters sum 230.
    verdict = adjudicate_total_against_parts(
        229, {"Q1": 68, "Q2": 80, "Q3": 82}, expected_parts=3
    )
    assert verdict.resolved, verdict.detail

    # Twice the tolerance is not rounding any more.
    verdict = adjudicate_total_against_parts(
        229, {"Q1": 68, "Q2": 80, "Q3": 90}, expected_parts=3
    )
    assert verdict.status == "impossible"
    assert verdict.code == "parts_exceed_total"


# --- Read by column, when the table is available as a rectangle -------------
#
# Everything above reads ragged rows and has to infer which column is which.
# These read the same tables as rectangles, where the headings say it outright.

# A filer splits one heading over two rows - "Three Months Ended" then
# "March 31," - and spans the years beneath it.
SPLIT_HEADING_EXHIBIT = """
<table>
  <tr><td></td><td></td><td colspan="7">Three Months Ended</td></tr>
  <tr><td></td><td></td><td colspan="7">March 31,</td></tr>
  <tr><td></td><td></td><td colspan="3">2016</td><td></td><td colspan="3">2015</td></tr>
  <tr><td>Harvoni - U.S.</td><td></td><td colspan="2">1,407</td><td></td><td></td>
      <td colspan="2">3,016</td><td></td></tr>
</table>
"""

# A quarter, its year to date, and a change column between each pair of years.
TEN_Q_EXHIBIT = """
<table>
  <tr><td></td><td colspan="5">Three Months Ended June 30,</td>
      <td colspan="5">Six Months Ended June 30,</td></tr>
  <tr><td></td><td colspan="2">2024</td><td colspan="2">2023</td><td>% Chg</td>
      <td colspan="2">2024</td><td colspan="2">2023</td><td>% Chg</td></tr>
  <tr><td>Tyvaso</td><td colspan="2">352.0</td><td colspan="2">276.5</td><td>27</td>
      <td colspan="2">679.4</td><td colspan="2">521.9</td><td>30</td></tr>
</table>
"""


def read_exhibit(markup: str, product: str, context: str = "(in millions)"):
    grid = html_table_grid(BeautifulSoup(markup, "lxml").find("table"))
    return grid, read_table(flatten_grid(grid), product=product, context=context, grid=grid)


def test_a_heading_split_across_rows_is_one_statement_again():
    """A heading broken over two lines is still one heading, in either reading.

    Issuers break "Three Months Ended" from the "March 31," beneath it wherever
    the column widths make them. Reading the heading a row at a time finds no
    period in either line and refuses a table that says exactly what it means.
    """
    grid, readout = read_exhibit(SPLIT_HEADING_EXHIBIT, "Harvoni")
    ragged = read_table(flatten_grid(grid), product="Harvoni", context="(in millions)")
    expected = {("2016Q1", 1407.0), ("2015Q1", 3016.0)}
    assert {(value.period, value.value_as_reported) for value in readout.values} == expected
    assert {(value.period, value.value_as_reported) for value in ragged.values} == expected


def test_the_quarter_and_the_year_to_date_keep_their_own_lengths():
    _grid, readout = read_exhibit(TEN_Q_EXHIBIT, "Tyvaso")
    by_period = {
        (value.period, value.period_type): value.value_as_reported for value in readout.values
    }
    assert by_period == {
        ("2024Q2", "quarterly"): 352.0,
        ("2023Q2", "quarterly"): 276.5,
        ("2024", "six_month"): 679.4,
        ("2023", "six_month"): 521.9,
    }


def test_a_change_column_is_not_revenue():
    """27 and 30 sit under "% Chg", which names no period, so they are dropped."""
    _grid, readout = read_exhibit(TEN_Q_EXHIBIT, "Tyvaso")
    assert 27.0 not in {value.value_as_reported for value in readout.values}
    assert 30.0 not in {value.value_as_reported for value in readout.values}


# The shape that shows a rectangle can be built and still describe nothing: the
# year spans three columns while the rows beneath write figures in two of them,
# so which column holds 2020 depends on which row you look at. Real, from a
# press release; the names here are invented.
HEADINGS_OUT_OF_STEP_WITH_THE_BODY = """
<table>
  <tr><td colspan="9">(in millions)</td></tr>
  <tr><td colspan="9">Three Months Ended</td></tr>
  <tr><td colspan="9">March 31,</td></tr>
  <tr><td></td><td colspan="3">2021</td><td colspan="5">2020</td></tr>
  <tr><td>Alfacept &#8211; U.S.</td><td>$</td><td>1,465</td><td>$</td><td>1,412</td></tr>
  <tr><td>Alfacept &#8211; Europe</td><td colspan="2">216</td><td colspan="2">181</td></tr>
  <tr><td>Alfacept &#8211; Other</td><td colspan="2">143</td><td colspan="2">100</td></tr>
  <tr><td colspan="2">1,824</td><td colspan="2">1,693</td></tr>
</table>
"""


def test_one_row_out_of_step_condemns_the_reading_for_the_whole_table():
    """The rows that did not trip the check were read against the same headings.

    Europe's 216 and 181 both land under "2021", which cannot happen where the
    headings and the figures share columns. The U.S. line reads correctly under
    the same headings - by luck, because its currency signs happen to push its
    figures into the right columns - and trusting it because it did not fail is
    how the table's own contradiction gets published as a number.
    """
    grid, readout = read_exhibit(HEADINGS_OUT_OF_STEP_WITH_THE_BODY, "Alfacept")
    assert not readout.fingerprint.by_column, "expected the geometry to be dropped"
    assert {(v.period, v.value_as_reported) for v in readout.values} == {
        ("2021Q1", 1824.0),
        ("2020Q1", 1693.0),
    }
    assert 1465.0 not in {v.value_as_reported for v in readout.values}


# Two headings stacked over two blocks of figures, not two periods side by side.
STACKED_HEADINGS = [
    ["", "Three Months Ended June 30,", ""],
    ["", "2024", "2023"],
    ["Tyvaso", "352.0", "276.5"],
    ["", "Six Months Ended June 30,", ""],
    ["", "2024", "2023"],
    ["Tyvaso", "679.4", "521.9"],
]


def test_a_heading_carries_forward_only_when_it_ends_mid_phrase():
    """The line below "Three Months Ended" continues it; the line below a whole
    heading starts a new one. Reading every header row as one statement would
    turn this table's two stacked headings into a quarter column beside a
    year-to-date column, and file 276.5 as the six months to June 2023."""
    fingerprint = build_fingerprint(STACKED_HEADINGS)
    assert [(block.months, block.year) for block in fingerprint.blocks] == [
        (3, 2024),
        (3, 2023),
    ]


# --- One product, several lines ---------------------------------------------
#
# A filer reports a product by region and prints the worldwide figure as the
# sum beneath. Every one of those lines names the product, and the figure that
# answers for it is the sum.

REGIONAL_LINES = [
    ["($ in millions)", "Three Months Ended June 30,", ""],
    ["", "2016", "2015"],
    ["Harvoni – U.S.", "1,474", "2,826"],
    ["Harvoni – Europe", "512", "623"],
    ["Harvoni – Japan", "448", "—"],
    ["Harvoni – Other International", "130", "159"],
    ["2,564", "3,608", ""],
]


def test_the_product_is_the_total_not_whichever_region_comes_first():
    readout = read_table(REGIONAL_LINES, product="Harvoni")
    assert {(v.period, v.value_as_reported) for v in readout.values} == {
        ("2016Q2", 2564.0),
        ("2015Q2", 3608.0),
    }


def test_the_total_is_quoted_together_with_the_lines_it_sums():
    """The quote has to let a reader check the arithmetic, not just the number."""
    readout = read_table(REGIONAL_LINES, product="Harvoni")
    quote = readout.values[0].source_quote
    assert "Harvoni – U.S. 1,474" in quote and "2,564" in quote


def test_a_total_printed_among_the_lines_is_found_by_the_same_arithmetic():
    labelled = REGIONAL_LINES[:-1] + [["Harvoni – Total", "2,564", "3,608"]]
    readout = read_table(labelled, product="Harvoni")
    assert {(v.period, v.value_as_reported) for v in readout.values} == {
        ("2016Q2", 2564.0),
        ("2015Q2", 3608.0),
    }


def test_several_lines_and_no_total_is_refused_rather_than_picked_between():
    readout = read_table(REGIONAL_LINES[:-1], product="Harvoni")
    assert readout.values == []
    assert "several_lines_no_total" in readout.skipped_reason


def test_a_row_naming_the_product_alone_is_the_product():
    """"Tyvaso" is the product; "Tyvaso DPI" is a formulation that shares its name."""
    rows = [
        ["($ in millions)", "Three Months Ended June 30,", ""],
        ["", "2024", "2023"],
        ["Tyvaso", "352.0", "276.5"],
        ["Tyvaso DPI", "180.0", "90.0"],
    ]
    readout = read_table(rows, product="Tyvaso")
    assert {(v.product_label, v.period, v.value_as_reported) for v in readout.values} == {
        ("Tyvaso", "2024Q2", 352.0),
        ("Tyvaso", "2023Q2", 276.5),
    }


def test_a_line_that_did_not_parse_still_counts_as_a_line():
    """Whether a row is a component is not decided by whether its numbers read.

    Dropping the rows that failed and then finding one row left is how a single
    region gets published as the product, so the count is of rows naming the
    product, not of rows that could be read.
    """
    rows = [
        ["($ in millions)", "Three Months Ended June 30,", ""],
        ["", "2016", "2015"],
        ["Harvoni – U.S.", "1,474", "2,826"],
        ["Harvoni – Europe", "512", "623", "1,067", "1,100", "42"],
    ]
    readout = read_table(rows, product="Harvoni")
    assert readout.values == []
    assert "several_lines_no_total" in readout.skipped_reason


def test_a_nil_dash_is_the_zero_it_means():
    """A line that sold nothing everywhere is a line, not a heading.

    Reading its dashes as "no figures here" makes the row look like a label
    with nothing under it, so the label is carried onto the next product and
    the line stops counting towards its own total.
    """
    from app.extraction.extract import cell_number

    assert cell_number("—") == 0.0
    assert cell_number("-") == 0.0
    assert cell_number("") is None
    assert cell_number("Atripla") is None


def test_a_total_is_found_when_the_currency_symbol_shifts_one_line():
    """A filer prints "$" in its own cell on the first line of a block only.

    That puts the first component one column right of its siblings while both
    state the same quarter. Adding the components up column by column then
    reaches no total, and the table is refused for having none with the total
    printed directly beneath it.
    """
    rows = [
        ["", "Three Months Ended June 30,"],
        ["", "2015"],
        ["Harvoni - U.S.", "$", "2,826"],
        ["Harvoni - Europe", "623"],
        ["Harvoni - Other International", "159"],
        ["", "3,608"],
    ]
    grid = [
        [None, "Three Months Ended June 30,", None],
        [None, "2015", None],
        ["Harvoni - U.S.", "$", "2,826"],
        ["Harvoni - Europe", None, "623"],
        ["Harvoni - Other International", None, "159"],
        [None, None, "3,608"],
    ]
    readout = read_table(rows, product="Harvoni", context="(in millions)", grid=grid)
    assert [value.value_as_reported for value in readout.values] == [3608.0]


def test_a_sentence_is_not_labelled_as_a_table_read():
    """``extraction_method`` names the reader, not the branch it arrived on.

    ``read_prose`` and ``read_tables`` produce the same ``Datapoint`` and reach
    the candidate contract through one function, which stamped every value it
    emitted as ``table_fingerprint``. The export carries that column for a
    reader deciding how much to trust a figure, so a sentence was presented as
    a row read out of a declared table.
    """
    from app.extraction.candidates import _as_candidate
    from app.extraction.process import Datapoint

    def point(signature: str) -> Datapoint:
        return Datapoint(
            product_label="Remodulin", period="2005Q2", period_type="quarterly",
            value_normalized_usd_millions=17.4, value_as_reported=17.4,
            source_unit="millions", source_currency="USD", fx_rate_to_usd=None,
            source_quote="Remodulin revenues were $17.4 million in the quarter.",
            fingerprint_signature=signature, normalization_status="ok",
        )

    assert _as_candidate(point("prose"), "Remodulin")["extraction_method"] == "prose_sentence"
    assert _as_candidate(point("u=millions|c=USD"), "Remodulin")["extraction_method"] == "table_fingerprint"


def test_a_derived_quarter_is_not_stored_as_a_tagged_fact():
    """The filer tagged it, or arithmetic produced it. Not both.

    ``_datapoint_from_candidate`` is shared by the XBRL reader and the
    derivations and hardcoded ``xbrl_fact``, so a quarter obtained by
    subtracting three quarters from a stated total was exported as a figure the
    issuer had tagged - and flagged ``extracted_from_xbrl`` besides.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base, DrugJobORM, ExtractionRunORM
    from app.domain.models import new_id
    from app.pipeline.orchestrator import PipelineOrchestrator

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Tyvaso", status="running")
    db.add(job)
    db.commit()
    orch = PipelineOrchestrator.__new__(PipelineOrchestrator)
    orch.db = db

    from app.domain.models import SourceType

    class _Src:
        source_id = "s1"
        source_type = SourceType.EARNINGS_RELEASE
        url = "https://example.invalid/8-k.htm"
        filing_type = "8-K"
        accession_number = "0000000000-00-000000"

    base = {"period": "2020Q4", "period_type": "quarterly", "value_reported": 100.0,
            "value_normalized_usd_millions": 100.0, "source_quote": "q"}
    tagged = orch._datapoint_from_candidate(
        job, _Src(), {**base, "extraction_method": "xbrl_fact", "xbrl_member": "TyvasoMember"})
    derived = orch._datapoint_from_candidate(
        job, _Src(), {**base, "extraction_method": "derived_from_period_total", "_derived": True})

    assert tagged.extraction_method == "xbrl_fact"
    assert tagged.issue_flags == ["extracted_from_xbrl"]
    assert derived.extraction_method == "derived_from_period_total"
    assert derived.issue_flags == ["derived_from_reported_series"]


def test_model_output_cannot_name_its_own_provenance():
    """A candidate the model produced is ``llm`` whatever it calls itself.

    The LLM branch and the deterministic branch store rows through the same
    code, so the reader's label is honoured only from a reader.
    """
    from app.pipeline.orchestrator import _deterministic_method

    assert _deterministic_method({"_from_table": True, "extraction_method": "prose_sentence"}) == "prose"
    assert _deterministic_method({"_from_table": True, "extraction_method": "table_fingerprint"}) == "table"
    assert _deterministic_method({"extraction_method": "table_fingerprint"}) == "llm"
    assert _deterministic_method({"_from_table": True, "extraction_method": "hand_audited"}) == "table"


def test_a_sentence_does_not_pre_empt_a_derivation():
    """Derivations run where nothing stronger answered, not where nothing did.

    `complete_series` was applied only to periods no row existed for, so any
    reader that produced anything at all pre-empted it. A sentence offering 1.0
    for a quarter whose family total derives exactly to 94.645 did not lose to
    the better answer - it stopped the better answer being computed. The prose
    reader is the weakest producer there is, and it was silencing the
    strongest.

    A tagged fact and a schedule still pre-empt a derivation. They are the
    stronger claims.
    """
    import inspect

    from app.pipeline.orchestrator import CLAIM_STRENGTH, PipelineOrchestrator, claim_rank

    # The ranking is by how much had to be inferred.
    assert claim_rank("xbrl_fact") < claim_rank("table") < claim_rank(
        "derived_from_period_total") < claim_rank("prose")
    # Both spellings resolve: candidates carry the reader's label, stored rows
    # carry the shorter one the export uses.
    assert claim_rank("table_fingerprint") == claim_rank("table")
    assert claim_rank("prose_sentence") == claim_rank("prose")
    # An unrecognised producer ranks below everything rather than above it.
    assert claim_rank("something_new") > max(CLAIM_STRENGTH.values())

    source = inspect.getsource(PipelineOrchestrator._extract_revenue)
    assert "strongest" in source and "derived_rank" in source, (
        "the derivation gate must compare claim strength, not mere presence"
    )
    assert "if candidate[\"period\"] in reported_periods" not in source
    assert "derivable" in source, (
        "the derivation's inputs must be filtered too - see the test below for "
        "why the gate alone is a no-op"
    )


def test_a_wrong_sentence_must_not_stop_a_quarter_being_derived():
    """`complete_series` reads any candidate for a period as that period answered.

    So a sentence misreading Q1 does not merely outrank the derivation - it
    stops the derivation being *computed*, because Q1 is no longer missing.
    That is upstream of any rule about which claim wins, which is why ranking
    the producers moved exactly zero rows over the corpus: there was never a
    derived candidate for the ranking to prefer.

    The numbers here are the real case. Nebulized Tyvaso 2013Q1 is 94.645 in
    gold and the prose reader emitted 1.0 for it.
    """
    from app.extraction.derive import complete_series

    def candidate(period, value, method="table_fingerprint", period_type="quarterly"):
        return {"period": period, "period_type": period_type, "value_reported": value,
                "value_normalized_usd_millions": value, "currency": "USD",
                "unit": "millions", "source_quote": f"{period} {value}",
                "extraction_method": method}

    year = candidate("2013", 400.0, period_type="annual")
    rest = [candidate("2013Q2", 100.0), candidate("2013Q3", 100.0),
            candidate("2013Q4", 105.355)]

    derived = complete_series({"X": [year, *rest]}, product="X")
    assert [(c["period"], c["value_normalized_usd_millions"]) for c in derived] == [
        ("2013Q1", 94.645)
    ]

    # The same series with a sentence's wrong figure standing in for Q1.
    blocked = complete_series(
        {"X": [year, *rest, candidate("2013Q1", 1.0, "prose_sentence")]}, product="X"
    )
    assert blocked == [], (
        "this is the defect: nothing is derived, so nothing can be ranked. "
        "The caller must keep weak readings out of the derivation's inputs."
    )


def test_a_sentence_naming_two_products_answers_for_neither():
    """One period, one amount, one product - the third was missing.

    A sentence was accepted whenever an alias appeared anywhere in it, so a
    sentence covering a brand and its new formulation answered a question about
    either of them with the same figure. In the quarter Tyvaso DPI went on
    sale, its $3.0m and nebulized Tyvaso's $198.0m were both read as the one
    number the sentence happened to carry.
    """
    from app.extraction.prose import read_prose

    catalog = ["Tyvaso", "Tyvaso DPI", "Nebulized Tyvaso", "Remodulin"]
    both = (
        "Tyvaso and Tyvaso DPI together generated revenues of $42.2 million "
        "in the second quarter of 2022."
    )
    for product in ("Tyvaso", "Tyvaso DPI", "Nebulized Tyvaso"):
        assert read_prose(both, product=product, catalog=catalog) == [], product


def test_the_longest_product_name_in_a_sentence_wins():
    """"Tyvaso DPI" names one product, and is not evidence of two.

    The same rule the member register resolves by: a shorter product name sits
    inside a longer one far more often than it is a second product.
    """
    from app.extraction.prose import read_prose

    catalog = ["Tyvaso", "Tyvaso DPI", "Nebulized Tyvaso"]
    sentence = "Tyvaso DPI revenues were $3.0 million in the second quarter of 2022."

    for_dpi = read_prose(sentence, product="Tyvaso DPI", catalog=catalog)
    assert [(v.period, v.value_as_reported) for v in for_dpi] == [("2022Q2", 3.0)]
    # The sentence is about the inhaler, so it says nothing about the nebulized
    # product or about the brand line as a whole.
    assert read_prose(sentence, product="Tyvaso", catalog=catalog) == []
    assert read_prose(sentence, product="Nebulized Tyvaso", catalog=catalog) == []


def test_a_sentence_about_one_product_still_reads():
    from app.extraction.prose import read_prose

    sentence = "Remodulin revenues were $120.8 million in the third quarter of 2012."
    values = read_prose(sentence, product="Remodulin", catalog=["Remodulin", "Tyvaso"])
    assert [(v.period, v.value_as_reported) for v in values] == [("2012Q3", 120.8)]


def test_tracking_nothing_loses_nothing():
    """Ambiguity is measured against the products we could confuse it with."""
    from app.extraction.prose import read_prose

    sentence = "Tyvaso and Tyvaso DPI generated $42.2 million in the second quarter of 2022."
    assert read_prose(sentence, product="Tyvaso", catalog=[]) != []


def test_a_change_in_revenue_is_not_revenue():
    """"increased revenues by $3.6 million" says how much it moved.

    The sentence names one product, one period and one amount, so it passes
    every other guard. An amount introduced by "by" is a difference; the same
    sentence saying "totaled", "were" or "grew to" states the figure itself.
    """
    from app.extraction.prose import read_prose

    delta = (
        "The impact of the price change was to increase revenues from Remodulin "
        "by approximately $3.6 million for the three months ended June 30, 2004."
    )
    assert read_prose(delta, product="Remodulin", catalog=["Remodulin"]) == []

    level = "Sales of Remodulin for the three months ended June 30, 2004 totaled $16.2 million."
    assert [v.value_as_reported for v in read_prose(level, product="Remodulin", catalog=["Remodulin"])] == [16.2]


def test_a_figure_dated_inside_its_period_is_not_that_period_s_total():
    """A running total is not the quarter's total.

    "As of November 9, 2002 ... for the fourth quarter of 2002" is forty days
    into a quarter with ten weeks still to run, and the $8.5m it reports is
    short of the $9.7m the quarter finished on.
    """
    from app.extraction.prose import read_prose

    running = (
        "As of November 9, 2002, sales of Remodulin for the fourth quarter of "
        "2002 totaled approximately $8.5 million."
    )
    assert read_prose(running, product="Remodulin", catalog=["Remodulin"]) == []


def test_a_period_that_has_ended_is_not_a_cutoff():
    """"the three months ended June 30" names a period, it does not truncate one."""
    from app.extraction.prose import read_prose

    sentence = "Sales of Remodulin totaled approximately $8.7 million in the three months ended June 30, 2002."
    assert [v.period for v in read_prose(sentence, product="Remodulin", catalog=["Remodulin"])] == ["2002Q2"]


# --- the adjudicator, replayed ----------------------------------------------
#
# These two ran from a script that also printed a report. The test imported
# them across the repo, which made a test depend on a harness; the harness is
# gone and the logic lives with the assertions that use it.

GOLD = pathlib.Path(__file__).resolve().parents[2] / "seed" / "gold"


def run_case(case: dict) -> tuple[str, str]:
    kind, inputs = case["kind"], case["inputs"]
    if kind == "reported_value":
        if "solutions" in inputs:
            verdict = adjudicate_positional_solutions(
                inputs["requested_scope"],
                [tuple(solution) for solution in inputs["solutions"]],
            )
        else:
            verdict = adjudicate_reported_value(
                inputs["requested_scope"],
                [Candidate(**candidate) for candidate in inputs["candidates"]],
            )
    elif kind == "total_against_parts":
        verdict = adjudicate_total_against_parts(
            inputs["total"], inputs["parts"], expected_parts=inputs["expected_parts"]
        )
    elif kind == "split_ownership":
        verdict = adjudicate_split_ownership_quarter(
            inputs["period"], inputs["components"]
        )
    else:
        raise ValueError(f"unknown fixture kind: {kind}")
    return verdict.status, verdict.code


def real_rows_that_trip() -> list[str]:
    """Every complete year in gold, put through the same checks.

    This is the false-positive guard. Each series is grouped into calendar
    years, and any year with all four quarters is checked against the total
    those quarters imply - the same call the pipeline makes when deriving. None
    of them may come back as anything other than resolved.
    """
    quarterly = [
        json.loads(line)
        for line in (GOLD / "quarterly_revenue.jsonl").read_text().splitlines()
        if line.strip()
    ]
    annual = [
        json.loads(line)
        for line in (GOLD / "annual_revenue.jsonl").read_text().splitlines()
        if line.strip()
    ]
    # Normalised USD on both sides, never as-reported. Tracleer's annual series
    # is Actelion's CHF and its quarterly series is J&J's own dollar conversion
    # of the same history: comparing 1,020 francs against 1,035 dollars reports
    # a contradiction that is only a currency. This is the category error the
    # adjudicator is meant to catch, and it caught it here first.
    totals = {
        (row["drug_name"], str(row["period"])): row["value_normalized_usd_millions"]
        for row in annual
        if row.get("value_normalized_usd_millions") is not None
    }

    by_year: dict[tuple[str, int], dict[str, float]] = {}
    for row in quarterly:
        key = (row["drug_name"], row["calendar_year"])
        usd = row.get("value_normalized_usd_millions")
        if usd is None:
            continue
        by_year.setdefault(key, {})[row["period"]] = usd

    tripped = []
    for (drug, year), quarters in sorted(by_year.items()):
        stated = totals.get((drug, str(year)))
        if stated is None:
            # No published year to check against; the quarters stand on their
            # own citations and there is nothing here to adjudicate.
            continue
        verdict = adjudicate_total_against_parts(
            stated, quarters, expected_parts=len(quarters)
        )
        if not verdict.resolved:
            tripped.append(f"{drug} {year}: {verdict.code} - {verdict.detail}")

    for row in quarterly:
        components = row.get("bridge_components")
        if components:
            verdict = adjudicate_split_ownership_quarter(row["period"], components)
            if not verdict.resolved:
                tripped.append(f"{row['drug_name']} {row['period']}: {verdict.code}")
    return tripped
