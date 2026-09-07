"""The described reader: values come off the rows the model named, and only where arithmetic agrees.

Every fixture is a hand-written description of a hand-written grid. Nothing
here exercises a header grammar or a label regex: the reader is given the
meaning and checks the numbers.
"""

from __future__ import annotations

from app.domain.models import ParsedDocument, ParsingStatus
from app.extraction.columns import ColumnLayout, ColumnSpec
from app.extraction.described import read_described_document, read_described_grid
from app.extraction.readers import Observation
from app.fingerprint.llm import Fingerprint, GridRegion, ProseRegion, RowDescription, SectionDescription


def _doc(tables, text="") -> ParsedDocument:
    return ParsedDocument(source_id="t", text_blocks=[text], tables=tables, parsing_status=ParsingStatus.SUCCESS)


def _layout(*columns, unit="millions"):
    return ColumnLayout(columns=tuple(columns), unit_label=unit, unit_declared=True, currency="USD", currency_declared=True, notes=("llm_fingerprint",))


Q3_25 = ColumnSpec("value", 3, 9, 2025)
Q3_24 = ColumnSpec("value", 3, 9, 2024)
CHG = ColumnSpec("change", label="change")

JNJ = [
    ["REPORTED SALES ($MM)"],
    ["THIRD QUARTER 2025 2024 % Change"],
    ["PULMONARY HYPERTENSION (4)"],
    ["US", "865", "819", "5.7%"],
    ["Intl", "319", "274", "16.4%"],
    ["WW", "1,184", "1,092", "8.4%"],
    ["OPSUMIT"],
    ["US", "458", "408", "12.3%"],
    ["Intl", "185", "162", "14.2%"],
    ["WW", "643", "570", "12.8%"],
    ["(4) Products acquired from Actelion acquisition on June 16, 2017"],
]


def _row(i, label, product, geo, line, **kw):
    return RowDescription(row_index=i, label_as_printed=label, label_width=1 if label else 0, product=product,
                          geography=geo, geography_as_printed=label if geo else None, line=line, **kw)


def test_geography_rows_and_a_member_verified_subtotal():
    region = GridRegion(
        grid_index=0, layout=_layout(Q3_25, Q3_24, CHG),
        sections=(SectionDescription(2, "PULMONARY HYPERTENSION (4)", "revenue"),),
        rows=(
            _row(7, "US", "Opsumit", "United States", "own_revenue"),
            _row(8, "Intl", "Opsumit", "International", "own_revenue"),
            _row(9, "WW", "Opsumit", "Worldwide", "subtotal_of_geographies", members=(7, 8)),
        ),
    )
    observations, failures = read_described_grid(_doc([JNJ]), region, product="Opsumit", aliases=["Opsumit"])
    assert not failures, [f.render() for f in failures]
    by_key = {(o.period, o.geography): o.value_as_reported for o in observations}
    assert by_key[("2025Q3", "United States")] == 458
    assert by_key[("2024Q3", "International")] == 162
    assert by_key[("2025Q3", "Worldwide")] == 643
    assert all("change_column" in o.verified for o in observations)
    assert all(o.line_item == "exact" and o.described_product == "Opsumit" for o in observations)


def test_a_subtotal_that_does_not_sum_its_members_is_a_failure_not_a_value():
    region = GridRegion(
        grid_index=0, layout=_layout(Q3_25, Q3_24, CHG),
        rows=(
            _row(3, "US", "Opsumit", "United States", "own_revenue"),      # the franchise row, mis-assigned
            _row(8, "Intl", "Opsumit", "International", "own_revenue"),
            _row(9, "WW", "Opsumit", "Worldwide", "subtotal_of_geographies", members=(3, 8)),
        ),
    )
    observations, failures = read_described_grid(_doc([JNJ]), region, product="Opsumit", aliases=["Opsumit"])
    assert [f.code for f in failures] == ["subtotal_not_sum_of_members"]
    assert not [o for o in observations if o.geography == "Worldwide"]


def test_a_row_the_layout_cannot_place_is_reported_with_the_reason():
    region = GridRegion(
        grid_index=0, layout=_layout(Q3_25, Q3_24),   # no change column described, row has three cells
        rows=(_row(7, "US", "Opsumit", "United States", "own_revenue"),),
    )
    observations, failures = read_described_grid(_doc([JNJ]), region, product="Opsumit", aliases=["Opsumit"])
    assert not observations
    assert failures[0].code == "more_cells_than_columns" and "3 cells for 2" in failures[0].render()


def test_an_undescribed_product_row_is_a_failure_that_asks_for_repair():
    region = GridRegion(
        grid_index=0, layout=_layout(Q3_25, Q3_24, CHG),
        rows=(_row(7, "US", "Opsumit", "United States", "own_revenue"),),
    )
    grid = [row[:] for row in JNJ]
    grid[8] = ["Opsumit Intl", "185", "162", "14.2%"]
    _observations, failures = read_described_grid(_doc([grid]), region, product="Opsumit", aliases=["Opsumit"])
    assert any(f.code == "product_row_not_described" and f.row_index == 8 for f in failures)


def test_section_coverage_applies_to_rows_beneath_and_only_inside_the_period():
    q2_17 = ColumnSpec("value", 3, 6, 2017)
    q2_18 = ColumnSpec("value", 3, 6, 2018)
    grid = [
        ["SECOND QUARTER 2018 2017"],
        ["PULMONARY HYPERTENSION (4)"],
        ["OPSUMIT"],
        ["WW", "311", "45"],
        ["(4) Products acquired from Actelion acquisition on June 16, 2017"],
    ]
    region = GridRegion(
        grid_index=0, layout=_layout(q2_18, q2_17),
        sections=(SectionDescription(1, "PULMONARY HYPERTENSION (4)", "revenue", covers=("2017-06-16", "2017-06-30")),),
        rows=(_row(3, "WW", "Opsumit", "Worldwide", "own_revenue"),),
    )
    observations, failures = read_described_grid(_doc([grid]), region, product="Opsumit", aliases=["Opsumit"])
    assert not failures
    by_period = {o.period: o for o in observations}
    assert by_period["2018Q2"].covers is None, "a coverage span outside 2018Q2 must not attach to it"
    assert by_period["2017Q2"].covers == ("2017-06-16", "2017-06-30")
    assert "coverage_from_description" in by_period["2017Q2"].notes


def test_franchise_and_cost_lines_are_never_exact_values():
    grid = [
        ["Three Months Ended September 30, 2025 2024"],
        ["Total TTR: AMVUTTRA & ONPATTRO", "1,030", "544"],
        ["AMVUTTRA", "1,012", "492"],
        ["AMVUTTRA external R&D expenses", "9", "16"],
    ]
    region = GridRegion(
        grid_index=0, layout=_layout(Q3_25, Q3_24, unit="millions"),
        rows=(
            _row(1, "Total TTR: AMVUTTRA & ONPATTRO", "Amvuttra", None, "franchise_or_bundle"),
            _row(2, "AMVUTTRA", "Amvuttra", None, "own_revenue"),
            _row(3, "AMVUTTRA external R&D expenses", "Amvuttra", None, "cost_or_expense"),
        ),
    )
    observations, failures = read_described_grid(_doc([grid]), region, product="Amvuttra", aliases=["Amvuttra"])
    assert not failures
    kinds = {(o.line_kind, o.line_item) for o in observations}
    assert kinds == {("franchise_or_bundle", "qualified"), ("own_revenue", "exact")}
    assert {o.value_as_reported for o in observations if o.line_item == "exact"} == {1012, 492}


def test_prose_keeps_actual_statements_that_are_grounded_and_counts_the_rest():
    text = (
        "Global Skyrizi net revenues were $4.708 billion, an increase of 46.8 percent.\n\n"
        "AbbVie now expects full-year Skyrizi revenue of approximately $17.1 billion.\n\n"
        "OrbiMed paid us $150.0 million in exchange for royalties on Skyrizi net sales."
    )
    fingerprint = Fingerprint(prose=[
        ProseRegion("Skyrizi", "2025Q3", "quarterly", 4.708, "billions", "USD", "Worldwide",
                    "Global Skyrizi net revenues were $4.708 billion, an increase of 46.8 percent.", statement="actual"),
        ProseRegion("Skyrizi", "2025", "annual", 17.1, "billions", "USD", None,
                    "AbbVie now expects full-year Skyrizi revenue of approximately $17.1 billion.", statement="guidance"),
        ProseRegion("Skyrizi", "2025", "annual", 150.0, "millions", "USD", None,
                    "OrbiMed paid us $150.0 million in exchange for royalties on Skyrizi net sales.", statement="payment_or_financing"),
        ProseRegion("Skyrizi", "2025Q3", "quarterly", 9.9, "billions", "USD", None,
                    "Global Skyrizi net revenues were $9.9 billion.", statement="actual"),
    ])
    report = read_described_document(_doc([], text), fingerprint, product="Skyrizi")
    assert [(o.period, o.value_as_reported, o.provisional) for o in report.observations] == [("2025Q3", 4.708, True)]
    assert report.dropped_prose == {"guidance": 1, "payment_or_financing": 1}
    assert "prose:2025Q3:quote_not_in_document" in report.skipped
    assert report.mode == "model"


def test_two_columns_described_as_the_same_figure_place_nothing_and_ask_for_repair():
    """A six-month column beside its quarter, both described as the quarter."""
    grid = [
        ["Three Months Ended June 30, Six Months Ended June 30,"],
        ["2025 2024 2025 2024"],
        ["US", "68,683", "63,793", "132,958", "120,142"],
    ]
    q2_25, q2_24 = ColumnSpec("value", 3, 6, 2025), ColumnSpec("value", 3, 6, 2024)
    region = GridRegion(
        grid_index=0, layout=_layout(q2_25, q2_24, q2_25, q2_24, unit="thousands"),
        rows=(_row(2, "US", "Arikayce", "United States", "own_revenue"),),
    )
    observations, failures = read_described_grid(_doc([grid]), region, product="Arikayce", aliases=["Arikayce"])
    assert not observations
    assert [f.code for f in failures] == ["duplicate_columns"]
    assert "c0 and c2 are both described as 2025Q2 quarterly" in failures[0].detail


def test_two_grids_that_state_one_figure_differently_are_both_sent_back():
    """A six-month grid described as the quarter contradicts the quarter's own grid."""
    quarter = [["Three Months Ended June 30, 2025 2024"], ["US", "68,683", "63,793"]]
    half = [["Six Months Ended June 30, 2025 2024"], ["US", "132,958", "120,142"]]
    q2_25, q2_24 = ColumnSpec("value", 3, 6, 2025), ColumnSpec("value", 3, 6, 2024)
    fingerprint = Fingerprint(grids=[
        GridRegion(grid_index=0, layout=_layout(q2_25, q2_24, unit="thousands"),
                   rows=(_row(1, "US", "Arikayce", "United States", "own_revenue"),)),
        GridRegion(grid_index=1, layout=_layout(q2_25, q2_24, unit="thousands"),
                   rows=(_row(1, "US", "Arikayce", "United States", "own_revenue"),)),
    ])
    report = read_described_document(_doc([quarter, half]), fingerprint, product="Arikayce")
    assert report.observations == [], "both years of both grids are in doubt"
    codes = {(f.grid_index, f.code) for f in report.failures}
    assert codes == {(0, "contradicted_within_document"), (1, "contradicted_within_document")}
    assert "132,958 thousands" in next(f.detail for f in report.failures if f.grid_index == 0)


def test_a_series_keeps_the_issuers_currency_and_compares_in_it():
    """A DKK series is reconciled in DKK, annotated in USD, and scored against a DKK reference in DKK."""
    from app.benchmark.schema import compare, from_gold, from_series
    from app.extraction.series import assemble_series

    def obs(period, value, currency="DKK", unit="millions", url="a"):
        return Observation(
            product_label="Wegovy", period=period, period_type="quarterly", value_as_reported=value, unit_label=unit,
            currency=currency, unit_declared=True, geography=None, covers=None, source_quote=f"Wegovy {value}",
            method="grid", layout_signature="", verified=(), specificity=0, source_url=url,
        )

    series = assemble_series([obs("2025Q1", 17360), obs("2025Q2", 19528), obs("2025Q2", 19528, url="b"),
                              obs("2025Q2", 2900, currency="USD", url="c")], product="Wegovy")
    by_period = {(v.period, v.currency): v for v in series.values}
    assert by_period[("2025Q2", "DKK")].value_millions == 19528
    assert abs(by_period[("2025Q2", "DKK")].value_usd_millions - 19528 * 0.1511) < 0.01
    assert not series.verdicts, "a USD figure is its own series, not reconciled against the DKK ones"
    assert by_period[("2025Q2", "USD")].value_millions == 2900
    assert any("per currency" in note for note in series.notes)

    gold = from_gold({"drug_name": "Wegovy", "period": "2025Q2", "geography": "Worldwide", "value_reported": 19528,
                      "unit": "millions", "currency": "DKK", "source_value_reported": 19528, "source_unit": "millions",
                      "derivation": "direct_reported", "source_url": "a", "source_quote": "Wegovy 19,528"})
    result = compare(gold, [from_series(v) for v in series.values])
    assert result.outcome == "match", result.detail


def test_the_family_split_is_where_a_sibling_formulation_states_its_own_figure():
    from app.catalog.families import family_parent, family_siblings
    from app.extraction.series import formulation_split_periods

    assert family_parent("Nebulized Tyvaso") == "Tyvaso" and family_siblings("Nebulized Tyvaso") == ["Tyvaso DPI"]
    assert family_siblings("Tyvaso") == []

    def obs(period, method="grid", line_item="exact", provisional=False):
        return Observation(
            product_label="Tyvaso DPI", period=period, period_type="quarterly", value_as_reported=1.0, unit_label="millions",
            currency="USD", unit_declared=True, geography=None, covers=None, source_quote="Tyvaso DPI 1.0", method=method,
            layout_signature="", verified=(), specificity=0, line_item=line_item, provisional=provisional,
        )

    periods = formulation_split_periods([obs("2022Q3"), obs("2022Q2"), obs("2022Q1", method="prose"),
                                         obs("2021Q4", line_item="qualified"), obs("2021Q3", provisional=True)])
    assert periods == ["2022Q2", "2022Q3"], "a sentence, a qualified line and a provisional figure are not the split"


def test_two_rows_of_one_product_under_one_geography_are_sent_back():
    grid = [
        ["Three Months Ended September 30, 2024 2023"],
        ["US", "66,868", "59,203"],
        ["Japan", "20,983", "16,033"],
        ["Europe and rest of world", "5,574", "3,836"],
        ["International", "26,557", "19,869"],
        ["Total product revenues, net", "93,425", "79,072"],
    ]
    q3_24, q3_23 = ColumnSpec("value", 3, 9, 2024), ColumnSpec("value", 3, 9, 2023)
    region = GridRegion(
        grid_index=0, layout=_layout(q3_24, q3_23, unit="thousands"),
        rows=(
            _row(1, "US", "Arikayce", "United States", "own_revenue"),
            _row(2, "Japan", "Arikayce", "Japan", "own_revenue"),
            _row(3, "Europe and rest of world", "Arikayce", "International", "own_revenue"),
            _row(4, "International", "Arikayce", "International", "subtotal_of_geographies", members=(2, 3)),
            _row(5, "Total product revenues, net", "Arikayce", "Worldwide", "subtotal_of_geographies", members=(1, 4)),
        ),
    )
    observations, failures = read_described_grid(_doc([grid]), region, product="Arikayce", aliases=["Arikayce"])
    assert not observations
    assert [f.code for f in failures] == ["duplicate_geography_rows"]
    assert "r3" in failures[0].detail and "r4" in failures[0].detail and "International" in failures[0].detail


def test_an_other_region_compares_by_its_printed_label():
    from app.benchmark.schema import from_series
    from app.extraction.series import SeriesValue

    def value(geography, label):
        return SeriesValue(product="X", period="2025Q2", period_type="quarterly", geography=geography, value_millions=1.0,
                           value_as_reported=1.0, unit_label="millions", currency="USD", route="read", derivation="direct_reported",
                           status="resolved", detail="", source_urls=(), source_quote="", geography_label=label)

    assert from_series(value("Other", "Rest of World")).geography == "international"
    assert from_series(value("Other", "Europe and rest of world")).geography == "other"
    assert from_series(value("International", "Intl")).geography == "international"
    assert from_series(value("Japan", "Japan")).geography == "japan"


def test_printed_numbers_with_spaced_thousands_plus_signs_and_spaced_percents_parse():
    from app.extraction.columns import parse_cell
    from app.parsing.grids import is_number_token

    assert parse_cell("1 177").value == 1177 and parse_cell("2 133").value == 2133 and parse_cell("1'177").value == 1177
    assert parse_cell("+14.7 %").value == 14.7 and parse_cell("+14.7 %").percent
    assert parse_cell("(154)").value == -154 and parse_cell("3,476").value == 3476 and parse_cell("64").value == 64
    assert parse_cell("1 17") is None, "a space only separates groups of three digits"
    assert all(is_number_token(t) for t in ("1 177", "+29.2 %", "(154)", "3,476"))


def test_several_other_regions_in_one_grid_are_distinct_by_their_labels():
    from app.fingerprint.llm import duplicate_value_columns

    q3 = ColumnSpec("value", 3, 9, 2024)
    layout = _layout(
        ColumnSpec("value", 3, 9, 2024, geography="Worldwide", label="Total"),
        ColumnSpec("value", 3, 9, 2024, geography="Other", label="EMEA"),
        ColumnSpec("value", 3, 9, 2024, geography="Other", label="Rest of World"),
        ColumnSpec("value", 3, 9, 2024, geography="Other", label="Region China"),
    )
    assert duplicate_value_columns(layout) == []
    twice = _layout(q3, ColumnSpec("value", 3, 9, 2024, geography="Europe", label="EMEA"), ColumnSpec("value", 3, 9, 2024, geography="Europe", label="EMEA"))
    assert duplicate_value_columns(twice) == [(1, 2)]


def test_a_change_column_compares_the_same_quarter_a_year_earlier_when_the_row_runs_seven_quarters():
    grid = [["Q3 2024 Q2 2024 Q1 2024 Q4 2023 Q3 2023 Q2 2023 Q1 2023 % change"],
            ["Wegovy", "17,304", "11,659", "9,377", "9,591", "8,178", "7,522", "4,614", "112%"]]
    cols = [ColumnSpec("value", 3, m, y) for (m, y) in ((9, 2024), (6, 2024), (3, 2024), (12, 2023), (9, 2023), (6, 2023), (3, 2023))]
    region = GridRegion(grid_index=0, layout=_layout(*cols, CHG), rows=(_row(1, "Wegovy", "Wegovy", None, "own_revenue"),))
    observations, failures = read_described_grid(_doc([grid]), region, product="Wegovy", aliases=["Wegovy"])
    assert not failures, [f.render() for f in failures]
    assert {o.period: o.value_as_reported for o in observations}["2023Q3"] == 8178


def test_descriptive_text_cells_between_the_label_and_the_figures_are_not_figures():
    grid = [["Product", "Therapeutic area", "Indication", "US", "% change", "Rest of world", "% change", "% cc", "Total", "% change", "% cc"],
            ["Kisqali", "Oncology", "HR+/HER2- metastatic breast cancer", "750", "100", "427", "25", "25", "1 177", "64", "64"]]
    q2 = lambda geo: ColumnSpec("value", 3, 6, 2025, geography=geo)  # noqa: E731
    chg = lambda geo: ColumnSpec("change", geography=geo, label="change")  # noqa: E731
    region = GridRegion(
        grid_index=0,
        layout=_layout(q2("United States"), chg("United States"), q2("Other"), chg("Other"), chg("Other"), q2("Worldwide"), chg("Worldwide"), chg("Worldwide")),
        rows=(_row(1, "Kisqali", "Kisqali", None, "own_revenue"),),
    )
    observations, failures = read_described_grid(_doc([grid]), region, product="Kisqali", aliases=["Kisqali"])
    assert not failures, [f.render() for f in failures]
    assert {o.geography: o.value_as_reported for o in observations} == {"United States": 750, "Other": 427, "Worldwide": 1177}


def test_regions_may_nest_inside_a_total_and_an_international_subtotal():
    from app.extraction.columns import _partitions

    assert _partitions(19528, [13767, 5761, 2000, 1500, 1261, 1000])          # US + Intl = Total; regions sum to Intl
    assert _partitions(100, [60, 40])                                          # flat
    assert not _partitions(100, [60, 50, 20])                                  # nothing accounts for the total


def test_a_column_header_with_a_unit_after_the_region_still_names_the_region():
    from app.benchmark.schema import canonical_geography

    assert canonical_geography("Rest of world USD m") == "international"
    assert canonical_geography("U.S. (in millions)") == "united_states"
    assert canonical_geography("Europe and rest of world") == "other"
    assert canonical_geography("Otherwise") == "other", "a word that merely begins with an alias is not the alias"


def test_a_sales_split_with_several_other_regions_is_verified_as_a_nested_partition():
    grid = [["Total", "US Operations", "International Operations", "EUCAN", "Emerging Markets", "APAC", "Region China"],
            ["Wegovy ®", "19,528", "13,767", "5,761", "2,000", "1,500", "1,261", "1,000"]]
    q2 = lambda geo, label: ColumnSpec("value", 3, 6, 2025, geography=geo, label=label)  # noqa: E731
    region = GridRegion(
        grid_index=0,
        layout=_layout(q2("Worldwide", "Total"), q2("United States", "US Operations"), q2("International", "International Operations"),
                       q2("Europe", "EUCAN"), q2("Other", "Emerging Markets"), q2("Other", "APAC"), q2("Other", "Region China"), unit="millions"),
        rows=(_row(1, "Wegovy ®", "Wegovy", None, "own_revenue"),),
    )
    observations, failures = read_described_grid(_doc([grid]), region, product="Wegovy", aliases=["Wegovy"])
    assert not failures, [f.render() for f in failures]
    assert sorted((o.geography, o.geography_label, o.value_as_reported) for o in observations) == sorted([
        ("Worldwide", "Total", 19528), ("United States", "US Operations", 13767), ("International", "International Operations", 5761),
        ("Europe", "EUCAN", 2000), ("Other", "Emerging Markets", 1500), ("Other", "APAC", 1261), ("Other", "Region China", 1000),
    ])


def test_a_full_row_is_placed_even_when_its_regions_do_not_sum_flat_or_nested():
    """North America beside its own United States: no column set sums cleanly, but there is nothing to place."""
    grid = [["Total", "North America Operations", "US Operations", "International Operations", "EMEA", "Region China", "Rest of World"],
            ["Wegovy ®", "17,304", "12,827", "12,488", "4,477", "2,185", "166", "2,126"]]
    q3 = lambda geo, label: ColumnSpec("value", 3, 9, 2024, geography=geo, label=label)  # noqa: E731
    region = GridRegion(
        grid_index=0,
        layout=_layout(q3("Worldwide", "Total"), q3("Other", "North America Operations"), q3("United States", "US Operations"),
                       q3("International", "International Operations"), q3("Europe", "EMEA"), q3("Other", "Region China"), q3("Other", "Rest of World")),
        rows=(_row(1, "Wegovy ®", "Wegovy", None, "own_revenue"),),
    )
    observations, failures = read_described_grid(_doc([grid]), region, product="Wegovy", aliases=["Wegovy"])
    assert not failures, [f.render() for f in failures]
    assert {o.geography_label: o.value_as_reported for o in observations}["US Operations"] == 12488
    assert all("geography_sum_unverified" in o.verified for o in observations)


def test_each_printed_other_region_is_its_own_series():
    from app.extraction.series import assemble_series

    def obs(label, value):
        return Observation(
            product_label="Wegovy", period="2024Q3", period_type="quarterly", value_as_reported=value, unit_label="millions",
            currency="DKK", unit_declared=True, geography="Other", covers=None, source_quote=f"{label} {value}", method="grid",
            layout_signature="", verified=(), specificity=0, geography_label=label,
        )

    series = assemble_series([obs("EMEA", 2185), obs("Region China", 166), obs("Rest of World", 2126)], product="Wegovy")
    assert not series.verdicts
    assert {v.geography: v.value_millions for v in series.values} == {"Other: EMEA": 2185, "Other: Region China": 166, "Other: Rest of World": 2126}


def test_a_product_reported_in_two_currencies_is_assembled_once_per_currency():
    from app.extraction.series import assemble_series

    def obs(period, value, currency, url):
        return Observation(
            product_label="Tracleer", period=period, period_type="quarterly", value_as_reported=value, unit_label="millions",
            currency=currency, unit_declared=True, geography=None, covers=None, source_quote=f"Tracleer {value}", method="grid",
            layout_signature="", verified=(), specificity=0, source_url=url,
        )

    series = assemble_series([obs("2016Q4", 229, "CHF", "a"), obs("2016Q4", 229, "USD", "j"), obs("2017Q1", 224, "USD", "j")], product="Tracleer")
    assert not series.verdicts
    assert sorted((v.period, v.currency, v.value_millions) for v in series.values) == [("2016Q4", "CHF", 229), ("2016Q4", "USD", 229), ("2017Q1", "USD", 224)]


def test_a_revenue_section_with_no_product_line_beneath_it_is_sent_back():
    grid = [
        ["Three Months Ended June 30, 2026 2025"],
        ["Revenues:"],
        ["Product sales, net", "170,382", "6,470"],
        ["Total revenues", "170,382", "6,470"],
        ["Program expenses (1)"],
        ["YUTREPIA", "12,000", "9,000"],
    ]
    q2_26, q2_25 = ColumnSpec("value", 3, 6, 2026), ColumnSpec("value", 3, 6, 2025)
    region = GridRegion(
        grid_index=0, layout=_layout(q2_26, q2_25, unit="thousands"),
        sections=(SectionDescription(1, "Revenues:", "revenue"), SectionDescription(4, "Program expenses (1)", "cost_or_expense")),
        rows=(_row(5, "YUTREPIA", "Yutrepia", None, "cost_or_expense"),),
    )
    observations, failures = read_described_grid(_doc([grid]), region, product="Yutrepia", aliases=["Yutrepia"])
    assert not observations
    assert [f.code for f in failures] == ["revenue_section_without_product_rows"]
    assert "Product sales, net" in failures[0].detail


def test_two_other_regions_in_one_document_do_not_contradict_each_other():
    grid = [["Q2 2025 Total EMEA Region China"], ["Wegovy ®", "19,528", "3,500", "1,334"]]
    q2 = lambda geo, label: ColumnSpec("value", 3, 6, 2025, geography=geo, label=label)  # noqa: E731
    fingerprint = Fingerprint(grids=[GridRegion(
        grid_index=0, layout=_layout(q2("Worldwide", "Total"), q2("Other", "EMEA"), q2("Other", "Region China")),
        rows=(_row(1, "Wegovy ®", "Wegovy", None, "own_revenue"),),
    )])
    report = read_described_document(_doc([grid]), fingerprint, product="Wegovy")
    assert not report.failures, [f.render() for f in report.failures]
    assert sorted(o.value_as_reported for o in report.observations) == [1334, 3500, 19528]


def test_the_same_printed_region_compares_whatever_canonical_name_each_side_gave_it():
    from app.benchmark.schema import compare, from_gold, from_series
    from app.extraction.series import SeriesValue

    gold = from_gold({"drug_name": "Wegovy", "period": "2025Q2", "geography": "EUCAN", "value_reported": 3500, "unit": "millions",
                      "currency": "DKK", "source_value_reported": 3500, "source_unit": "millions", "derivation": "direct_reported",
                      "source_url": "a", "source_quote": "Wegovy 3,500"})
    assert gold.geography == "other"
    value = SeriesValue(product="Wegovy", period="2025Q2", period_type="quarterly", geography="Europe", value_millions=3500.0,
                        value_as_reported=3500.0, unit_label="millions", currency="DKK", route="read", derivation="direct_reported",
                        status="resolved", detail="", source_urls=(), source_quote="", geography_label="EUCAN")
    total = SeriesValue(product="Wegovy", period="2025Q2", period_type="quarterly", geography="Worldwide", value_millions=19528.0,
                        value_as_reported=19528.0, unit_label="millions", currency="DKK", route="read", derivation="direct_reported",
                        status="resolved", detail="", source_urls=(), source_quote="", geography_label="Total")
    assert compare(gold, [from_series(total), from_series(value)]).outcome == "match"


def test_a_restated_figure_stays_visible_as_an_alternate_of_the_own_period_statement():
    from app.benchmark.schema import compare, from_gold, from_series
    from app.extraction.series import assemble_series

    def obs(value, url, notes=()):
        return Observation(
            product_label="Wegovy", period="2024Q3", period_type="quarterly", value_as_reported=value, unit_label="millions",
            currency="DKK", unit_declared=True, geography="International", covers=None, source_quote=f"Wegovy {value}",
            method="grid", layout_signature="", verified=(), specificity=0, source_url=url, notes=tuple(notes),
        )

    series = assemble_series([obs(4477, "q3", notes=("current_period_column",)), obs(4816, "q1-next"), obs(4816, "q2-next")], product="Wegovy")
    value = series.values[0]
    assert value.value_millions == 4477 and value.alternates == (4816,)
    gold = from_gold({"drug_name": "Wegovy", "period": "2024Q3", "geography": "International", "value_reported": 4816, "unit": "millions",
                      "currency": "DKK", "source_value_reported": 4816, "source_unit": "millions", "derivation": "direct_reported",
                      "source_url": "a", "source_quote": "Wegovy 4,816"})
    assert compare(gold, [from_series(value)]).outcome == "match"


def test_a_retrospective_table_inside_a_later_report_is_a_restatement_not_the_own_period_statement():
    from app.extraction.described import document_latest_period

    q1_25 = ColumnSpec("value", 3, 3, 2025)
    q3_24 = ColumnSpec("value", 3, 9, 2024)
    fingerprint = Fingerprint(grids=[
        GridRegion(grid_index=0, layout=_layout(q1_25, unit="millions"), rows=(_row(1, "Wegovy", "Wegovy", None, "own_revenue"),)),
        GridRegion(grid_index=1, layout=_layout(q3_24, unit="millions"), rows=(_row(1, "Wegovy", "Wegovy", None, "own_revenue"),)),
    ])
    doc = _doc([[["Q1 2025"], ["Wegovy", "17,360"]], [["Q3 2024 restated"], ["Wegovy", "17,304"]]])
    assert document_latest_period(fingerprint) == (2025, 3)
    report = read_described_document(doc, fingerprint, product="Wegovy")
    by_period = {o.period: o for o in report.observations}
    assert "current_period_column" in by_period["2025Q1"].notes
    assert "current_period_column" not in by_period["2024Q3"].notes
