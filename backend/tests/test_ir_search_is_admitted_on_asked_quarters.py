"""IR and search documents enter only for the quarters the run asked.

Coverage still expands asked quarters with annual keys when describing a
10-K. Admission does not: a document that carries ``2020`` does not answer
2020Q1, and a locator that names 2020Q4 is dropped from a Q1/Q2 window.

Invented: Calderon, Acme Pharma.
"""

from __future__ import annotations

from datetime import date

from app.connectors.coverage import admits_asked_quarters
from app.connectors.sources import REPORTING_LAG


def test_carried_asked_quarter_is_kept():
    assert admits_asked_quarters(
        asked=["2020Q1", "2020Q2"],
        carried=["2020Q1"],
        title="Acme",
        url="https://ir.acme.example/undated.htm",
    )


def test_an_annual_key_does_not_answer_a_quarter():
    """Coverage may carry 2020 for a 10-K; that is not 2020Q1."""
    assert not admits_asked_quarters(
        asked=["2020Q1", "2020Q2"],
        carried=["2020"],
        title="Acme",
        url="https://ir.acme.example/undated.htm",
    )


def test_a_q4_locator_is_dropped_from_a_q1_window():
    assert not admits_asked_quarters(
        asked=["2020Q1", "2020Q2"],
        title="Acme Q4 2020 earnings",
        url="https://ir.acme.example/earningsreleaseq42020.htm",
        source_date=date(2020, 6, 1),
        since=date(2020, 4, 1),
        until=date(2020, 8, 15),
    )


def test_a_date_inside_the_window_plus_lag_keeps_an_undated_locator():
    dated = date(2020, 5, 15)
    assert admits_asked_quarters(
        asked=["2020Q1", "2020Q2"],
        title="Acme earnings",
        url="https://ir.acme.example/results.htm",
        source_date=dated,
        since=date(2020, 4, 1),
        until=date(2020, 8, 15),
    )
    late = date(2020, 8, 15) + REPORTING_LAG + REPORTING_LAG
    assert not admits_asked_quarters(
        asked=["2020Q1", "2020Q2"],
        title="Acme earnings",
        url="https://ir.acme.example/results.htm",
        source_date=late,
        since=date(2020, 4, 1),
        until=date(2020, 8, 15),
    )
