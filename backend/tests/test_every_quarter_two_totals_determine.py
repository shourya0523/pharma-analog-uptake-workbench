"""Derive every quarter two published totals determine.

The derivation did H1 - Q2 -> Q1 and the total-minus-every-other-quarter
rule. A filer that states only its six-, nine- and twelve-month figures
beside the quarters - or, for a product's early years, only six-month and
annual tables - determines the third and fourth quarters as differences of
spans, and nothing else will ever state them.

Every derived quarter was also held, correct or not: its quote was
arithmetic that never named the product, and the product-missing veto read
that as a quote about something else. The quote now names the product, and
a figure whose rounding bound exceeds a tenth of it is held on purpose.

Invented names: Calderon.
"""

from __future__ import annotations

from app.extraction.derive import (
    HELD_FOR_BOUND,
    complete_quarters_from_totals,
    complete_series,
    held_for_bound,
)
from app.extraction.process import Datapoint
from app.quality.candidate_filters import quote_mentions_product


def _point(period, period_type, value, precision=0.5):
    return Datapoint(
        product_label="Calderon", period=period, period_type=period_type,
        value_normalized_usd_millions=value, value_as_reported=value,
        source_unit="millions", source_currency="USD", fx_rate_to_usd=None,
        source_quote=f"Calderon {value}", fingerprint_signature="x",
        normalization_status="ok", rounding_uncertainty_usd_millions=precision,
    )


def test_the_fourth_quarter_is_the_year_less_the_nine_months():
    derived = complete_quarters_from_totals([
        _point("2023", "annual", 540.6), _point("2023M9", "nine_month", 409.6),
    ])
    assert [(p.period, p.value_normalized_usd_millions) for p in derived] == [("2023Q4", 131.0)]
    assert derived[0].rounding_uncertainty_usd_millions == 1.0


def test_the_third_quarter_is_the_nine_months_less_the_six():
    derived = complete_quarters_from_totals([
        _point("2023M9", "nine_month", 409.6), _point("2023H1", "six_month", 267.3),
    ])
    assert [(p.period, round(p.value_normalized_usd_millions, 3)) for p in derived] == [("2023Q3", 142.3)]


def test_a_stated_quarter_is_never_re_derived():
    derived = complete_quarters_from_totals([
        _point("2023", "annual", 540.6), _point("2023M9", "nine_month", 409.6),
        _point("2023Q4", "quarterly", 131.0),
    ])
    assert derived == []


def test_the_derived_quote_names_the_product():
    derived = complete_quarters_from_totals([
        _point("2023", "annual", 540.6), _point("2023M9", "nine_month", 409.6),
    ])
    assert derived[0].source_quote.startswith("Calderon: ")
    assert "+/- 1 from input rounding" in derived[0].source_quote


def test_a_figure_not_known_to_its_first_digit_is_held():
    """Two inputs rounded to the nearest million bound the difference by two
    million; a three-million quarter is then not known to its first digit."""
    coarse = complete_quarters_from_totals([
        _point("2023", "annual", 12.0, precision=1.0), _point("2023M9", "nine_month", 9.0, precision=1.0),
    ])
    assert held_for_bound(coarse[0])
    fine = complete_quarters_from_totals([
        _point("2023", "annual", 540.6), _point("2023M9", "nine_month", 409.6),
    ])
    assert not held_for_bound(fine[0])
    rows = {"Calderon": [
        {"period": "2023", "period_type": "annual", "value_reported": 12.0,
         "value_normalized_usd_millions": 12.0, "currency": "USD", "unit": "millions",
         "rounding_uncertainty_usd_millions": 1.0},
        {"period": "2023M9", "period_type": "nine_month", "value_reported": 9.0,
         "value_normalized_usd_millions": 9.0, "currency": "USD", "unit": "millions",
         "rounding_uncertainty_usd_millions": 1.0},
    ]}
    candidates = complete_series(rows, product="Calderon")
    assert [c["label_flags"] for c in candidates if c["period"] == "2023Q4"] == [[HELD_FOR_BOUND]]


def test_a_quarter_derived_from_a_tagged_total_still_names_the_product():
    """A figure the filer tagged carries no row label of its own.

    The derived quote took its name from the source row, so every quarter
    derived from a tagged annual total opened with a bare colon, and the
    judge's veto for a quote that does not name the product held all of
    them - correct or not.
    """
    tagged = [
        Datapoint(product_label="", period="2024", period_type="annual",
                  value_normalized_usd_millions=100.0, value_as_reported=100.0,
                  source_unit="millions", source_currency="USD", fx_rate_to_usd=None,
                  source_quote="us-gaap:RevenueFromContractWithCustomer 100000000",
                  fingerprint_signature="tagged",
                  normalization_status="ok", rounding_uncertainty_usd_millions=None),
    ] + [
        Datapoint(product_label="", period=f"2024Q{q}", period_type="quarterly",
                  value_normalized_usd_millions=value, value_as_reported=value,
                  source_unit="millions", source_currency="USD", fx_rate_to_usd=None,
                  source_quote="tagged", fingerprint_signature="tagged",
                  normalization_status="ok", rounding_uncertainty_usd_millions=None)
        for q, value in ((1, 20.0), (2, 25.0), (3, 30.0))
    ]

    derived = complete_quarters_from_totals(tagged, product="Calderon")

    assert [point.period for point in derived] == ["2024Q4"]
    assert derived[0].source_quote.startswith("Calderon: ")
    assert quote_mentions_product(derived[0].source_quote, "Calderon", None)

    # With no name to hand it says so, rather than opening with a colon.
    unnamed = complete_quarters_from_totals(tagged)
    assert unnamed[0].source_quote.startswith("the product: ")
