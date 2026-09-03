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

    Merck states Winrevair as "$336 million and $615 million in the second
    quarter and first six months of 2025, respectively" every quarter. Refusing
    it as multi-period left a whole product unreadable even though the sentence
    states the correspondence outright. Neither half matches the single-period
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

    Remodulin went on sale in 2002Q2, so United Therapeutics' full-year 2002
    total is Q2 + Q3 + Q4. Requiring all four quarters made the launch year look
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

    Merck's XBRL product table splits each year into U.S. / Int'l / Total, so
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

    J&J writes one row per geography and keeps periods in the columns, so
    "OPSUMIT | U.S. | 373 | 328 | 729 | 601" is quarter, prior-year quarter,
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
    at the closing date and the buyer's first one starts there. Opsumit's and
    Uptravi's 2017Q2 exist only as two partial figures, and adding them is only
    safe if the parts are known to tile the quarter.
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
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from eval_adjudication import run_case

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
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from eval_adjudication import real_rows_that_trip

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

    # Merck's real 2025 Adempas: stated nine months 229, quarters sum 230.
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
