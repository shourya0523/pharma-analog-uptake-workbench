"""A check groups readings by what they are readings of; so does its rejection.

`conflicting_values` groups on (period, period type, scope), because a
nine-month total and a region's row are not rival claims about one quarter's
worldwide figure. The `Finding` it produced then named only the period label,
and the rejection that consumes it dropped every cell printed under that
label - undoing the grouping on the way out.

The other half is who gets to vote. A row whose label the reader could not
account for carries a number and no claim about whose number it is: an
income-statement line in the same table states a figure for the period as
surely as the product's row does. Read as a rival claim it puts the cell into
conflict, jumps the unit-continuity check by two orders of magnitude, and
fails "the number is in its own quote" - and each of those takes the correct
reading down beside it.

Invented names: Calderon, NuVessa.
"""

from __future__ import annotations

from app.extraction.candidates import extract_revenue_candidates
from app.extraction.check import (
    Finding,
    conflicting_values,
    run_checks,
    scale_continuity,
)
from app.extraction.process import Datapoint


def _point(period, value, *, period_type="quarterly", scope=None, flags=(),
           quote=None, label="Calderon", layout="a"):
    return Datapoint(
        product_label=label,
        period=period,
        period_type=period_type,
        value_normalized_usd_millions=value,
        value_as_reported=value,
        source_unit="millions",
        source_currency="USD",
        fx_rate_to_usd=None,
        source_quote=quote if quote is not None else f"{label} {period} {value}",
        fingerprint_signature=layout,
        normalization_status="reported",
        scope=scope,
        flags=tuple(flags),
    )


def _codes(findings):
    return sorted(f.code for f in findings)


def test_a_finding_names_the_cell_it_is_about():
    """Not the period label, which several cells share."""
    finding = Finding(
        code="conflicting_values", severity="error", message="",
        cells=(("2025Q1", "quarterly", "United States"),),
    )
    assert finding.cells == (("2025Q1", "quarterly", "United States"),)
    assert finding.periods == ("2025Q1",)


def test_a_conflict_in_one_cell_leaves_the_other_cells_of_that_period():
    """The quarter and the nine months that end it share a period label."""
    points = [
        _point("2025Q3", 30.714),
        _point("2025Q3", 77.535, period_type="nine_month"),
        _point("2025Q3", 60.0, scope="United States"),
        _point("2025Q3", 20.0, scope="United States"),
    ]
    findings = [f for f in run_checks(points) if f.severity == "error"]
    rejected = {cell for f in findings for cell in f.cells}
    assert ("2025Q3", "quarterly", "United States") in rejected
    assert ("2025Q3", "quarterly", "") not in rejected
    assert ("2025Q3", "nine_month", "") not in rejected


def test_a_label_the_reader_could_not_account_for_does_not_vote():
    """Three income-statement rows and the product's own row, one quarter."""
    understood = _point("2025Q1", 21.005)
    noise = [
        _point("2025Q1", 0.812, flags=("label_not_understood",), label="Interest income"),
        _point("2025Q1", -3.641, flags=("label_not_understood",), label="Income tax expense"),
        _point("2025Q1", -18.045, flags=("label_not_understood",), label="Loss before income tax"),
    ]
    assert _codes(conflicting_values([understood, *noise])) == []
    assert _codes(conflicting_values([understood, _point("2025Q1", 9.0)])) == [
        "conflicting_values"
    ]


def test_a_label_not_understood_does_not_break_the_unit_continuity_of_a_series():
    """A tax line two orders of magnitude off the product's quarters is not
    the product switching from thousands to millions."""
    series = [_point("2025Q1", 21.005), _point("2025Q2", 22.500)]
    noise = _point("2025Q1", 0.097, flags=("label_not_understood",), label="Income tax expense")
    assert _codes(scale_continuity([*series, noise])) == []
    assert _codes(scale_continuity([*series, _point("2025Q1", 0.097)])) != []


def test_a_label_not_understood_is_not_asked_whether_its_number_is_in_its_quote():
    """It is held for a person either way; what it may not do is reject the
    cell the product's own row was read into."""
    points = [
        _point("2025Q1", 21.005),
        _point("2025Q1", -3.641, flags=("label_not_understood",),
               label="Income tax expense", quote="Income tax expense (3,641)"),
    ]
    assert _codes(run_checks(points)) == []


def test_the_product_row_survives_the_income_statement_around_it():
    """End to end through the reader: one table, the product's row and the
    lines beneath it, and the correct reading comes out a candidate."""
    table = [
        ["", "Three months ended March 31, 2025", "Three months ended March 31, 2024"],
        ["Total Calderon sales", "21,005", "8,963"],
        ["Interest income", "812", "97"],
        ["Income tax expense", "(3,641)", "(72)"],
        ["Loss before income tax", "(18,045)", "(13,975)"],
    ]
    candidates, findings, _skipped = extract_revenue_candidates(
        [table], product="Calderon", units=["thousands"],
    )
    values = [c["value_normalized_usd_millions"] for c in candidates]
    assert 21.005 in values, [f.code for f in findings]
