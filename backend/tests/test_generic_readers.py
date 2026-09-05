"""Layouts the corpus does not contain, read by the generic stack.

Every fixture here was written from scratch rather than lifted from a filing
in ``seed/gold/corpus``, so a reader that only recognised the shapes it was
developed against would fail them. Each exercises one rule the readers claim
to be general: header vocabulary, blank-cell alignment under arithmetic
constraints, geography rows, one-cell-per-line grids, prose currency and
period forms, cross-document reconciliation.
"""

from __future__ import annotations

from app.domain.models import ParsedDocument, ParsingStatus
from app.extraction.columns import align_row, build_layouts, split_geography
from app.extraction.prose import read_prose
from app.extraction.readers import read_document
from app.extraction.series import assemble_series
from app.parsing.grids import parse_text_document, recover_text_grids


def _doc(text: str) -> ParsedDocument:
    blocks, tables = parse_text_document(text)
    return ParsedDocument(source_id="t", text_blocks=["\n\n".join(blocks)], tables=tables, parsing_status=ParsingStatus.SUCCESS)


def test_full_year_columns_in_euros_with_a_change_column():
    layouts = build_layouts("Product sales (EUR millions) Full year 2023 Full year 2022 Change %")
    assert len(layouts) == 1
    layout = layouts[0]
    assert layout.currency == "EUR" and layout.unit_label == "millions"
    assert [(c.period, c.kind) for c in layout.columns] == [("2023", "value"), ("2022", "value"), (None, "change")]
    alignments, reason = align_row(["1,204", "1,050", "14.7%"], layout)
    assert reason is None and len(alignments) == 1
    assert alignments[0].values == {0: 1204.0, 1: 1050.0}


def test_geography_nested_under_two_periods():
    header = "(in thousands of dollars) Three Months Ended June 30, 2024 2023 Domestic International Total Domestic International Total"
    layout = build_layouts(header)[0]
    assert [(c.period, c.geography) for c in layout.columns] == [
        ("2024Q2", "United States"), ("2024Q2", "International"), ("2024Q2", "Worldwide"),
        ("2023Q2", "United States"), ("2023Q2", "International"), ("2023Q2", "Worldwide"),
    ]
    alignments, _ = align_row(["900", "300", "1,200", "700", "250", "950"], layout)
    assert len(alignments) == 1 and "geography_sum" in alignments[0].verified
    # Parts that do not add to the total are refused.
    assert align_row(["900", "300", "1,500", "700", "250", "950"], layout)[0] == []


def test_a_blank_cell_with_nothing_to_check_it_against_is_ambiguous():
    layout = build_layouts("Three Months Ended March 31, 2021 2020 % Change")[0]
    # Two tokens for three columns and no arithmetic to say which cell is
    # blank: both placements survive, and the reader reports the ambiguity
    # rather than assuming the blank is the prior year.
    alignments, reason = align_row(["48.2", "NM"], layout)
    assert reason is None
    assert sorted((a.values for a in alignments), key=str) == [{0: 48.2}, {1: 48.2}]
    # A dash holds its column, so the same row printed the way issuers do is not ambiguous.
    alignments, _ = align_row(["48.2", "—", "NM"], layout)
    assert [a.values for a in alignments] == [{0: 48.2}]


def test_first_half_columns_are_six_month_periods():
    layout = build_layouts("First half 2025 First half 2024 (CHF million)")[0]
    assert [(c.period, c.period_type) for c in layout.columns] == [("2025", "six_month"), ("2024", "six_month")]
    assert layout.currency == "CHF"


def test_one_cell_per_line_grid_with_geography_rows():
    text = "\n\n".join([
        "REPORTED SALES ($MM)", "FOURTH QUARTER", "2022", "2021", "% Change",
        "ZORBIX", "US", "410", "380", "7.9%", "Intl", "190", "150", "26.7%", "WW", "600", "530", "13.2%",
        "OTHER", "US", "10", "12", "-16.7%",
    ])
    tables = recover_text_grids(text)
    assert tables, "the dump should come back as a grid"
    doc = ParsedDocument(source_id="t", text_blocks=[text], tables=tables, parsing_status=ParsingStatus.SUCCESS)
    report = read_document(doc, product="Zorbix")
    by_key = {(o.period, o.geography): o.value_as_reported for o in report.observations if o.method == "grid"}
    assert by_key[("2022Q4", "United States")] == 410
    assert by_key[("2022Q4", "Worldwide")] == 600
    assert by_key[("2021Q4", "International")] == 150
    assert all(o.unit_label == "millions" and o.unit_declared for o in report.observations if o.method == "grid")


def test_geography_labels_split_from_product_names():
    assert split_geography("Zorbix – Rest of World") == ("Zorbix", "International")
    assert split_geography("ZORBIX US") == ("ZORBIX", "United States")
    assert split_geography("WW") == ("", "Worldwide")
    assert split_geography("Zorbix") == ("Zorbix", None)


def test_prose_reads_a_trailing_currency_and_a_bare_year():
    values = read_prose("Sales of Zorbix amounted to 1,020 million Swiss francs for 2016, a decrease of 18% at CER.", product="Zorbix")
    assert [(v.period, v.period_type, v.value_as_reported, v.currency) for v in values] == [("2016", "annual", 1020.0, "CHF")]


def test_prose_ignores_a_change_and_a_condition():
    text = (
        "Zorbix net product sales for the three months ended June 30, 2019 increased by $3.7 million. "
        "We will owe a milestone if annual net sales of Zorbix exceed $250.0 million. "
        "Zorbix revenues rose to $525 million in the first quarter of 2026."
    )
    values = read_prose(text, product="Zorbix")
    assert [(v.period, v.value_as_reported) for v in values] == [("2026Q1", 525.0)]


def test_prose_ties_the_amount_to_its_own_revenue_phrase():
    text = (
        "Total Family revenues decreased to $457.5 million in the first quarter of 2026, "
        "compared to $466.3 million in the first quarter of 2025, driven by lower Zorbix revenues."
    )
    assert read_prose(text, product="Zorbix") == []


def test_series_prefers_the_documents_own_period_over_a_restated_comparative():
    current = _doc(
        "The table below summarizes revenues (in thousands):\n\n"
        "| | Three Months Ended March 31, | |\n| --- | --- | --- |\n| | 2021 | 2020 |\n| Zorbix | 12,345 | 9,876 |\n"
    )
    later = _doc(
        "Revenues (in millions):\n\n"
        "| | Three Months Ended March 31, | |\n| --- | --- | --- |\n| | 2022 | 2021 |\n| Zorbix | 15.1 | 12.3 |\n"
    )
    observations = read_document(current, product="Zorbix", source_url="a").observations
    observations += read_document(later, product="Zorbix", source_url="b").observations
    series = assemble_series(observations, product="Zorbix")
    resolved = series.resolved()
    assert resolved["2021Q1"].value_usd_millions == 12.345
    assert resolved["2022Q1"].value_usd_millions == 15.1
    assert not series.verdicts


def test_series_derives_the_fourth_quarter_from_the_stated_year_to_date():
    doc = _doc(
        "Revenues (in millions):\n\n"
        "| | Three Months Ended September 30, | | Nine Months Ended September 30, | |\n"
        "| --- | --- | --- | --- | --- |\n| | 2023 | 2022 | 2023 | 2022 |\n| Zorbix | 30.0 | 20.0 | 80.0 | 55.0 |\n\n"
        "Zorbix revenues were $113.0 million for the year ended December 31, 2023.\n"
    )
    series = assemble_series(read_document(doc, product="Zorbix").observations, product="Zorbix")
    fourth = series.resolved()["2023Q4"]
    assert fourth.route == "derived" and fourth.value_usd_millions == 33.0
    assert "nine_month" in fourth.detail


def test_rows_split_across_paragraphs_are_streamed_back_together():
    # A PDF sales schedule whose rows break after the first value, with the
    # rest of each row and a stray "$" landing in paragraphs of their own.
    text = "\n\n".join([
        "REPORTED SALES ($MM)", "THIRD QUARTER", "2024 2023 Reported Operational Currency",
        "VELDORA US 120 $", "110", "9.1% 9.1% -",
        "Intl 80", "60", "33.3% 30.0% 3.3%",
        "WW 200", "170", "17.6% 16.5% 1.1%",
        "OTHER US 5", "6", "(16.7)% (16.7)% -",
    ])
    tables = recover_text_grids(text)
    rows = {row[0]: row[1:] for table in tables for row in table if len(row) > 1}
    assert rows["VELDORA US"] == ["120", "110", "9.1%", "9.1%", "-"]
    assert rows["WW"] == ["200", "170", "17.6%", "16.5%", "1.1%"]
    assert rows["OTHER US"] == ["5", "6", "(16.7)%", "(16.7)%", "-"]
    doc = ParsedDocument(source_id="t", text_blocks=[text], tables=tables, parsing_status=ParsingStatus.SUCCESS)
    report = read_document(doc, product="Veldora")
    by_key = {(o.period, o.geography): o.value_as_reported for o in report.observations if o.method == "grid"}
    assert by_key[("2024Q3", "Worldwide")] == 200
    assert by_key[("2023Q3", "International")] == 60


def test_a_grids_own_header_outranks_the_page_header_it_also_fits():
    # The page header says 2005 / 2004; a later grid restates its own years.
    # Both read the row without blanks, and the grid's own header wins.
    text = "\n\n".join([
        "Revenues for the Year Ended (in thousands) December 31, 2005 December 31, 2004",
        "Veldora 90,000 70,000",
        "Total revenues 95,000 74,000",
        "Revenues for the Year Ended (in thousands) December 31, 2003 December 31, 2002",
        "Veldora 45,121 21,174",
        "Total revenues 53,341 30,120",
    ])
    report = read_document(_doc(text), product="Veldora")
    assert not [s for s in report.skipped if "ambiguous" in s], report.skipped
    values = {o.period: o.value_as_reported for o in report.observations if o.method == "grid"}
    assert values == {"2005": 90000, "2004": 70000, "2003": 45121, "2002": 21174}


def test_a_balance_grid_does_not_inherit_the_pages_period_columns():
    text = "\n\n".join([
        "Revenues Six Months Ended June 30, 2002 2001 (in thousands)",
        "Veldora 8,900 -",
        "4. INVENTORIES",
        "June 30, 2002 December 31, 2001",
        "Veldora 3,498 3,405",
        "Veldora delivery pumps 2,113 1,283",
        "Total inventories 6,619 6,025",
    ])
    report = read_document(_doc(text), product="Veldora")
    values = [(o.period, o.value_as_reported) for o in report.observations if o.method == "grid"]
    assert values == [("2002", 8900)]


def test_a_generic_product_line_yields_to_the_products_own_stated_figure():
    # A one-product issuer's "Product sales" line covers pumps and supplies
    # too; the sentence naming the product's own figure proves it.
    text = "\n\n".join([
        "Veldora Therapeutics sells Veldora, its only approved product, and related delivery pumps. "
        "Product sales consist of Veldora and delivery pumps.",
        "Three Months Ended September 30, 2002 2001 (in thousands)",
        "Product sales 4,358 505",
        "Sales of Veldora totaled approximately $2.6 million in the three months ended September 30, 2002.",
    ])
    report = read_document(_doc(text), product="Veldora", issuer_products=["Veldora"])
    assert not [o for o in report.observations if "generic_product_line" in o.notes]
    prose = [o for o in report.observations if o.method == "prose" and o.period == "2002Q3"]
    assert prose and prose[0].value_as_reported == 2.6
