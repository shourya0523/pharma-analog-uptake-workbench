"""How far two figures may differ is a thing the sources often state.

Both tolerances in `check.py` stood in for one quantity: the precision the
issuer rounded to before publishing. They approximated it as a fraction of the
value, and rounding does not work that way - a figure tagged `decimals="-6"` is
within half a million of the truth whether it reads 30 or 3,000.

That got it wrong in both directions at once. Against a derived fourth quarter
and the issuer's own printed one, both a million apart by construction:

    a large product   538 vs 537   old allowance 2.69   too loose to catch a gap
    a small one        30 vs  29   old allowance 0.15   tight enough to invent one

Where the datapoints declare their precision it is now summed and used. Where
any of them does not - a table cell has no equivalent of XBRL's `decimals` -
the old fraction still stands in, unchanged.
"""

from __future__ import annotations

from app.extraction.check import conflicting_values, quarters_sum_to_period_total
from app.extraction.process import Datapoint

HALF_A_MILLION = 0.5


def _point(period, value, *, period_type="quarterly", uncertainty=None, layout="a"):
    return Datapoint(
        product_label="Cordexa",
        period=period,
        period_type=period_type,
        value_normalized_usd_millions=value,
        value_as_reported=value,
        source_unit="millions",
        source_currency="USD",
        fx_rate_to_usd=None,
        source_quote=f"{period} {value}",
        fingerprint_signature=layout,
        normalization_status="reported",
        rounding_uncertainty_usd_millions=uncertainty,
    )


def _codes(findings):
    return [f.code for f in findings]


class TestTwoReadingsOfOnePeriod:
    def test_a_small_product_no_longer_conflicts_with_its_own_rounding(self):
        """A small product: 30 derived (+/-2) against 29 printed (+/-0.5).

        The old allowance was 0.5% of 30, or 0.15, so a gap rounding fully
        explains was reported as an error at severity `error`.
        """
        points = [
            _point("2022Q4", 30.0, uncertainty=2.0, layout="derived"),
            _point("2022Q4", 29.0, uncertainty=HALF_A_MILLION, layout="exhibit"),
        ]
        assert _codes(conflicting_values(points)) == []

    def test_a_large_product_now_catches_a_gap_the_fraction_waved_through(self):
        """Two exhibit readings of one product, each to the nearest million.

        They can differ by at most one million and agree. Two apart is a real
        disagreement - and 0.5% of 538 is 2.69, so it used to pass.
        """
        points = [
            _point("2022Q4", 538.0, uncertainty=HALF_A_MILLION, layout="a"),
            _point("2022Q4", 540.0, uncertainty=HALF_A_MILLION, layout="b"),
        ]
        assert _codes(conflicting_values(points)) == ["conflicting_values"]

    def test_the_same_pair_within_their_bounds_agrees(self):
        points = [
            _point("2022Q4", 538.0, uncertainty=HALF_A_MILLION),
            _point("2022Q4", 539.0, uncertainty=HALF_A_MILLION, layout="b"),
        ]
        assert _codes(conflicting_values(points)) == []

    def test_one_silent_source_falls_back_to_the_fraction(self):
        """A table cell says nothing about its precision, and must not be
        assumed exact - that would report rounding as a conflict."""
        points = [
            _point("2022Q4", 538.0, uncertainty=HALF_A_MILLION),
            _point("2022Q4", 540.0, uncertainty=None, layout="b"),
        ]
        assert _codes(conflicting_values(points)) == []  # 2.0 <= 0.5% of 540

    def test_a_duplicated_reading_does_not_double_its_own_bound(self):
        """Picking the compared pair by value equality summed the low bound
        twice when a reading appeared under two layouts."""
        points = [
            _point("2022Q4", 538.0, uncertainty=0.05, layout="a"),
            _point("2022Q4", 538.0, uncertainty=0.05, layout="b"),
            _point("2022Q4", 540.0, uncertainty=0.05, layout="c"),
        ]
        assert _codes(conflicting_values(points)) == ["conflicting_values"]

    def test_the_message_says_what_was_allowed(self):
        points = [
            _point("2022Q4", 538.0, uncertainty=HALF_A_MILLION),
            _point("2022Q4", 542.0, uncertainty=HALF_A_MILLION, layout="b"),
        ]
        message = conflicting_values(points)[0].message
        assert "differ by 4.000" in message
        assert "1.000 that rounding could explain" in message


class TestQuartersAgainstTheirTotal:
    def _year(self, quarters, total, *, uncertainty):
        points = [
            _point(f"2022Q{i}", v, uncertainty=uncertainty)
            for i, v in enumerate(quarters, start=1)
        ]
        return points + [
            _point("2022", total, period_type="annual", uncertainty=uncertainty)
        ]

    def test_four_quarters_each_rounded_to_a_million_may_miss_by_two(self):
        """44 + 54 + 43 + 59 = 200 exactly, but the same year off by two is
        still only rounding when every figure is to the nearest million."""
        points = self._year([44.0, 54.0, 43.0, 61.0], 200.0, uncertainty=HALF_A_MILLION)
        assert _codes(quarters_sum_to_period_total(points)) == []

    def test_and_flags_a_year_that_misses_by_more(self):
        points = self._year([44.0, 54.0, 43.0, 62.0], 200.0, uncertainty=HALF_A_MILLION)
        assert _codes(quarters_sum_to_period_total(points)) == [
            "quarters_sum_to_period_total"
        ]

    def test_the_declared_bound_is_tighter_than_two_percent(self):
        """2% of 2,184 is 43.7 - room for a whole misread quarter. The declared
        bound is 2.5, which is what four million-rounded figures can hide."""
        points = self._year(
            [571.0, 566.0, 545.0, 512.0], 2184.0, uncertainty=HALF_A_MILLION
        )
        assert _codes(quarters_sum_to_period_total(points)) == [
            "quarters_sum_to_period_total"
        ]
        # Unchanged behaviour where nothing declares a precision.
        silent = self._year([571.0, 566.0, 545.0, 512.0], 2184.0, uncertainty=None)
        assert _codes(quarters_sum_to_period_total(silent)) == []

    def test_the_message_says_the_gap_and_the_allowance(self):
        points = self._year([44.0, 54.0, 43.0, 62.0], 200.0, uncertainty=HALF_A_MILLION)
        message = quarters_sum_to_period_total(points)[0].message
        assert "a gap of 3.000 against 2.500 allowed" in message
