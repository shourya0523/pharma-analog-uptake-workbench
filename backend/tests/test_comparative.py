"""Prior-year columns are recovered only when a row's own arithmetic proves the layout.

Quotes below are verbatim from datapoints extracted during end-to-end runs against
United Therapeutics earnings exhibits, and the expected values are the gold rows in
seed/gold/quarterly_revenue.jsonl.
"""

import json
from pathlib import Path

from app.parsing.periods import PeriodContext
from app.quality.comparative import (
    comparative_period,
    derive_comparative_candidates,
    derive_comparative_value,
    parse_numbers,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
Q2_CONTEXT = PeriodContext(months=3, month=6, year=2024)


def _gold_value(drug: str, period: str) -> float:
    rows = [
        json.loads(line)
        for line in (REPO_ROOT / "seed" / "gold" / "quarterly_revenue.jsonl").read_text().splitlines()
        if line.strip()
    ]
    matches = [r for r in rows if r["drug_name"] == drug and r["period"] == period]
    preferred = [r for r in matches if r.get("revenue_scope") == "Product family"]
    return (preferred or matches)[0]["value_reported"]


def test_parse_numbers_handles_currency_and_negatives():
    assert parse_numbers("Total Tyvaso 398.2 318.9 79.3 25 %") == [398.2, 318.9, 79.3, 25.0]
    # A leading footnote marker would otherwise shift every column position
    assert parse_numbers("Tyvaso DPI ®(1) $ 258.3 $ 193.6 $ 64.7 33 %") == [
        258.3,
        193.6,
        64.7,
        33.0,
    ]
    assert parse_numbers("Nebulized Tyvaso (1) 130.2 9.7 139.9 119.6 5.7 125.3") == [
        130.2,
        9.7,
        139.9,
        119.6,
        5.7,
        125.3,
    ]
    assert parse_numbers("Nebulized Tyvaso 126.0 154.4 (28.4) (18) %") == [
        126.0,
        154.4,
        -28.4,
        -18.0,
    ]
    assert parse_numbers("no numbers here") == []


def test_change_row_recovers_the_prior_year_figure():
    # 398.2 - 318.9 = 79.3, and 79.3 / 318.9 = 25%, so the layout is confirmed
    assert derive_comparative_value("Total Tyvaso 398.2 318.9 79.3 25 %", 398.2) == 318.9
    assert derive_comparative_value("Total Tyvaso 433.8 325.8 108.0 33 %", 433.8) == 325.8


def test_recovered_values_match_the_gold_prior_year_quarters():
    assert derive_comparative_value("Total Tyvaso 398.2 318.9 79.3 25 %", 398.2) == _gold_value(
        "Tyvaso", "2023Q2"
    )
    assert derive_comparative_value("Total Tyvaso 433.8 325.8 108.0 33 %", 433.8) == _gold_value(
        "Tyvaso", "2023Q3"
    )


def test_parallel_segment_blocks_recover_the_matching_column():
    # "US 388.5, intl 9.7, total 398.2" then the same three for the prior year
    quote = "Total Tyvaso 388.5 9.7 398.2 313.2 5.7 318.9"
    assert derive_comparative_value(quote, 398.2) == 318.9
    assert derive_comparative_value(quote, 388.5) == 313.2


def test_rows_that_do_not_prove_their_layout_are_left_alone():
    # Arithmetic does not hold, so no column layout can be assumed
    assert derive_comparative_value("Total Tyvaso 398.2 318.9 12.0 25 %", 398.2) is None
    # Value is not the leading column
    assert derive_comparative_value("Total Tyvaso 398.2 318.9 79.3 25 %", 79.3) is None
    # A prose sentence with a single figure
    assert derive_comparative_value("Total Tyvaso revenues were $452.6 million", 452.6) is None


def test_comparative_period_steps_back_one_year():
    assert comparative_period("2024Q2") == "2023Q2"
    assert comparative_period("2024") == "2023"
    assert comparative_period("2024H1") == "2023H1"
    assert comparative_period("unknown") is None


def test_derive_candidates_flags_and_dedupes():
    candidates = [
        {
            "period": "2024Q2",
            "value_reported": 398.2,
            "revenue_scope": "Product family",
            "source_quote": "Total Tyvaso 398.2 318.9 79.3 25 %",
            "confidence": 0.9,
        },
        {
            "period": "2023Q2",
            "value_reported": 318.9,
            "revenue_scope": "Product family",
            "source_quote": "Total Tyvaso 398.2 318.9 79.3 25 %",
            "confidence": 0.9,
        },
    ]
    # The prior year is already present, so nothing is duplicated
    assert derive_comparative_candidates(candidates, context=Q2_CONTEXT) == []

    derived = derive_comparative_candidates(candidates[:1], context=Q2_CONTEXT)
    assert len(derived) == 1
    assert derived[0]["period"] == "2023Q2"
    assert derived[0]["value_reported"] == 318.9
    assert derived[0]["_derived_comparative"] is True
    # Quote still contains the derived value, so citation checks continue to hold
    assert "318.9" in derived[0]["source_quote"]
    assert derived[0]["confidence"] <= 0.6
