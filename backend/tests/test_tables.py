"""Revenue tables are read structurally, independent of the extraction model.

The rows below are the parsed form of the revenue table in United Therapeutics'
uthrq12024-ex991.htm exhibit, and the expected figures are gold rows from
seed/gold/quarterly_revenue.jsonl.
"""

import json
from pathlib import Path

from app.parsing.tables import clean_label, extract_revenue_rows

REPO_ROOT = Path(__file__).resolve().parents[2]

# Verbatim output of DocumentParser for the Q1 2024 earnings exhibit revenue table
Q1_2024_TABLE = [
    ["", "", "", "", "", "", "", ""],
    ["", "Three Months Ended March 31,", "", "Dollar Change", "", "Percentage Change"],
    ["", "2024", "", "2023", "", ""],
    ["Net product sales:", "", "", "", "", "", "", ""],
    ["Tyvaso DPI ®(1)", "$", "227.5", "", "", "$", "118.7", "", "", "$", "108.8", "", "", "92", "%"],
    ["Nebulized Tyvaso ®(1)", "145.0", "", "", "119.7", "", "", "25.3", "", "", "21", "%"],
    ["Total Tyvaso", "372.5", "", "", "238.4", "", "", "134.1", "", "", "56", "%"],
    ["Remodulin ®(2)", "128.0", "", "", "121.4", "", "", "6.6", "", "", "5", "%"],
    ["Adcirca ®", "6.4", "", "", "7.3", "", "", "(0.9)", "", "", "(12)", "%"],
    ["Total revenues", "$", "677.7", "", "", "$", "506.9", "", "", "$", "170.8", "", "", "34", "%"],
]


def _gold(drug: str, period: str) -> float:
    rows = [
        json.loads(line)
        for line in (REPO_ROOT / "seed" / "gold" / "quarterly_revenue.jsonl").read_text().splitlines()
        if line.strip()
    ]
    return next(r["value_reported"] for r in rows if r["drug_name"] == drug and r["period"] == period)


def _by_period(rows: list[dict], scope: str) -> dict[str, float]:
    return {r["period"]: r["value_reported"] for r in rows if r["revenue_scope"] == scope}


def test_clean_label_strips_trademarks_and_footnotes():
    assert clean_label("Tyvaso DPI ®(1)") == "Tyvaso DPI"
    assert clean_label("Nebulized Tyvaso ®(1)") == "Nebulized Tyvaso"
    assert clean_label("Total Tyvaso") == "Total Tyvaso"
    assert clean_label("Net product sales:") == "Net product sales"


def test_table_yields_both_period_columns_matching_gold():
    rows = extract_revenue_rows([Q1_2024_TABLE], product="Tyvaso", generic="treprostinil")
    family = _by_period(rows, "Product family")
    assert family["2024Q1"] == _gold("Tyvaso", "2024Q1") == 372.5
    assert family["2023Q1"] == _gold("Tyvaso", "2023Q1") == 238.4


def test_formulation_rows_are_scoped_separately():
    rows = extract_revenue_rows([Q1_2024_TABLE], product="Tyvaso", generic="treprostinil")
    formulations = {r["formulation"] for r in rows if r["revenue_scope"] == "Formulation-specific"}
    assert formulations == {"Tyvaso DPI", "Nebulized Tyvaso"}
    dpi = [r for r in rows if r["formulation"] == "Tyvaso DPI"]
    assert {r["period"]: r["value_reported"] for r in dpi} == {"2024Q1": 227.5, "2023Q1": 118.7}


def test_other_products_and_company_totals_are_excluded():
    rows = extract_revenue_rows([Q1_2024_TABLE], product="Tyvaso", generic="treprostinil")
    values = {r["value_reported"] for r in rows}
    assert 128.0 not in values, "Remodulin is a different product"
    assert 6.4 not in values, "Adcirca is a different product"
    assert 677.7 not in values, "company total is out of scope"


def test_change_columns_are_never_read_as_periods():
    rows = extract_revenue_rows([Q1_2024_TABLE], product="Tyvaso", generic="treprostinil")
    values = {r["value_reported"] for r in rows}
    assert 134.1 not in values, "dollar change column"
    assert 56.0 not in values, "percentage change column"
    assert {r["period"] for r in rows} == {"2024Q1", "2023Q1"}


def test_rows_carry_a_quote_containing_their_value():
    rows = extract_revenue_rows([Q1_2024_TABLE], product="Tyvaso", generic="treprostinil")
    for row in rows:
        assert str(row["value_reported"]) in row["source_quote"]
        assert row["_from_table"] is True
        assert row["extraction_method"] == "table"


def test_table_without_a_period_header_is_skipped():
    orphan = [["Total Tyvaso", "372.5", "238.4"]]
    assert extract_revenue_rows([orphan], product="Tyvaso") == []


def test_annual_table_labels_years_without_a_quarter():
    annual = [
        ["", "Year Ended December 31,", "", "Dollar Change", "", "Percentage Change"],
        ["", "2024", "", "2023", "", ""],
        ["Total Tyvaso", "1,620.4", "", "", "1,233.7", "", "", "386.7", "", "", "31", "%"],
    ]
    rows = extract_revenue_rows([annual], product="Tyvaso")
    assert _by_period(rows, "Product family") == {"2024": 1620.4, "2023": 1233.7}
    assert {r["period_type"] for r in rows} == {"annual"}
