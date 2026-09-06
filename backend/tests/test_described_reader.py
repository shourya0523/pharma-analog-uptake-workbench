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
    by_period = {v.period: v for v in series.values}
    assert by_period["2025Q2"].value_millions == 19528 and by_period["2025Q2"].currency == "DKK"
    assert abs(by_period["2025Q2"].value_usd_millions - 19528 * 0.1511) < 0.01
    assert not series.verdicts, "a USD figure is set aside, not reconciled against the DKK ones"
    assert any("set aside" in note and "USD" in note for note in series.notes)

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
