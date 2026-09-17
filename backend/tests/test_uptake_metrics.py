"""Tests for the uptake curve and the time to reach a share of peak.

Two properties. The window is four distinct quarters rather than four rows, so
a series read several times per quarter still produces a curve. And the
numerator is a figure for the same thing the denominator is a figure for, so a
scope mismatch is refused by name instead of published as an early ramp.
"""

from datetime import date

from app.analytics.peak_sales import SalesObservation, SelectedPeak
from app.analytics.uptake import calculate_revenue_uptake, time_to_ninety_percent_peak


def _peak(
    value: float = 200,
    *,
    estimate_type: str = "observed",
    geography: str = "U.S.",
    revenue_scope: str = "Product family",
    reason: str | None = None,
) -> SelectedPeak:
    return SelectedPeak(
        estimate_type=None if reason else estimate_type,
        value=None if reason else value,
        currency=None if reason else "USD",
        geography=None if reason else geography,
        revenue_scope=None if reason else revenue_scope,
        as_of_date=date(2025, 1, 1),
        selection_method=None if reason else "mature_observed_annual_peak_v2",
        input_ids=[],
        reason=reason,
    )


def _quarter(
    index: int,
    value: float,
    *,
    row_id: str | None = None,
    geography: str = "U.S.",
    revenue_scope: str = "Product family",
    confidence: float | None = None,
) -> SalesObservation:
    year = 2024 + (index - 1) // 4
    quarter = (index - 1) % 4 + 1
    return SalesObservation(
        id=row_id or f"q{index}",
        period=f"{year}Q{quarter}",
        value=value,
        currency="USD",
        geography=geography,
        revenue_scope=revenue_scope,
        period_type="quarterly",
        period_basis="calendar",
        period_end=date(year, quarter * 3, 28),
        confidence=confidence,
    )


def test_rolling_four_quarter_uptake_marks_sparse_history():
    points = calculate_revenue_uptake(
        observations=[_quarter(1, 10), _quarter(2, 20), _quarter(3, 30), _quarter(4, 40)],
        selected_peak=_peak(),
        launch_date=date(2024, 1, 1),
    )

    assert [point.missing_reason for point in points[:3]] == ["insufficient_history"] * 3
    assert points[3].value == 0.5
    assert points[3].metric_type == "revenue_proxy_r4q"
    assert points[3].input_ids == ["q1", "q2", "q3", "q4"]


def test_the_window_is_four_quarters_not_four_rows():
    """A quarter read several times is one quarter.

    A series published once in a press release and again in a filing used to
    produce no window of four consecutive quarters and so no uptake points at
    all, although the quarters themselves were unbroken.
    """
    observations = []
    for index in range(1, 11):
        for copy in range(3):
            observations.append(
                _quarter(index, 10 * index, row_id=f"q{index}-{copy}", confidence=0.5 + copy / 10)
            )

    points = calculate_revenue_uptake(
        observations=observations,
        selected_peak=_peak(1000),
        launch_date=date(2024, 1, 1),
    )

    assert len(points) == 10
    assert sum(1 for point in points if point.value is not None) == 7
    # The highest-confidence reading of each quarter is the one summed.
    assert points[3].input_ids == ["q1-2", "q2-2", "q3-2", "q4-2"]


def test_quarters_in_one_scope_over_a_peak_in_another_are_refused():
    """A U.S. numerator over a worldwide denominator is not an early ramp."""
    observations = [_quarter(index, 40) for index in range(1, 9)]
    points = calculate_revenue_uptake(
        observations=observations,
        selected_peak=_peak(1000, geography="Worldwide", revenue_scope="Worldwide"),
        launch_date=date(2024, 1, 1),
    )

    assert {point.missing_reason for point in points} == {"incompatible_sales_scope"}
    assert all(point.value is None for point in points)


def test_one_place_spelled_two_ways_is_one_series():
    """A U.S. track is a U.S. track whether the filing writes `U.S.` or `US`.

    Compared as written, a quarter was refused for the punctuation of its
    label - so a track the issuer reported every quarter looked like one with
    a hole in it, in a scope the peak was not in.
    """
    observations = [
        _quarter(index, 40, row_id=f"q{index}", geography="US" if index % 2 else "U.S.")
        for index in range(1, 9)
    ]
    points = calculate_revenue_uptake(
        observations=observations,
        selected_peak=_peak(1000, geography="U.S."),
        launch_date=date(2024, 1, 1),
    )
    assert points[3].value == 0.16
    assert [point.missing_reason for point in points[3:]] == [None] * 5
    # The other answer: a different place is still a different place.
    elsewhere = calculate_revenue_uptake(
        observations=[
            _quarter(index, 40, row_id=f"q{index}", geography="Japan")
            for index in range(1, 9)
        ],
        selected_peak=_peak(1000, geography="U.S."),
        launch_date=date(2024, 1, 1),
    )
    assert {point.missing_reason for point in elsewhere} == {"incompatible_sales_scope"}


def test_a_scope_mismatch_outranks_a_gap_in_the_quarters():
    """Precedence: the wrong series is not a series with a hole in it.

    A product that reports a U.S. and a worldwide line every quarter has
    perfectly consecutive quarters. Labelling that `nonconsecutive_quarters`
    sends the analyst to look for filings that already exist.
    """
    observations = []
    for index in range(1, 9):
        observations.append(_quarter(index, 40, row_id=f"us{index}"))
        observations.append(
            _quarter(
                index, 100, row_id=f"ww{index}", geography="Worldwide", revenue_scope="Worldwide"
            )
        )

    worldwide = calculate_revenue_uptake(
        observations=observations,
        selected_peak=_peak(1000, geography="Worldwide", revenue_scope="Worldwide"),
        launch_date=date(2024, 1, 1),
    )

    # The worldwide series is complete, so it yields points rather than a
    # refusal about consecutiveness.
    assert [point.missing_reason for point in worldwide[:3]] == ["insufficient_history"] * 3
    assert worldwide[3].value == 0.4
    assert worldwide[3].input_ids == ["ww1", "ww2", "ww3", "ww4"]

    # With a quarter of the worldwide line genuinely missing, the refusal is
    # about the gap, and only then.
    thinned = [row for row in observations if row.id != "ww3"]
    gapped = calculate_revenue_uptake(
        observations=thinned,
        selected_peak=_peak(1000, geography="Worldwide", revenue_scope="Worldwide"),
        launch_date=date(2024, 1, 1),
    )
    by_period = {point.period: point for point in gapped}
    assert by_period["2024Q3"].missing_reason == "incompatible_sales_scope"
    assert by_period["2025Q2"].missing_reason == "nonconsecutive_quarters"


def test_nonconsecutive_quarters_do_not_form_a_rolling_year():
    observations = [_quarter(1, 10), _quarter(2, 20), _quarter(4, 40), _quarter(5, 50)]
    points = calculate_revenue_uptake(
        observations=observations,
        selected_peak=_peak(),
        launch_date=date(2024, 1, 1),
    )
    assert points[-1].value is None
    assert points[-1].missing_reason == "nonconsecutive_quarters"


def test_missing_launch_anchor_returns_explicit_reason():
    points = calculate_revenue_uptake(
        observations=[_quarter(1, 10)],
        selected_peak=_peak(),
        launch_date=None,
    )
    assert points[0].missing_reason == "missing_launch_anchor"


def test_a_refused_peak_is_not_a_denominator():
    points = calculate_revenue_uptake(
        observations=[_quarter(1, 10)],
        selected_peak=_peak(reason="annual_history_has_gaps"),
        launch_date=date(2024, 1, 1),
    )
    assert points[0].missing_reason == "missing_selected_peak"
    assert points[0].denominator is None


def test_time_to_ninety_percent_is_a_time_not_an_observation():
    quarterly = [_quarter(1, 20), _quarter(2, 30), _quarter(3, 40), _quarter(4, 100)]
    reached = time_to_ninety_percent_peak(
        quarterly, selected_peak=_peak(200), launch_date=date(2024, 1, 1)
    )

    assert reached.period == "2024Q4"
    assert reached.months_since_launch == 11
    assert reached.reason is None
    assert reached.input_ids == ["q1", "q2", "q3", "q4"]


def test_time_to_ninety_percent_refuses_a_forecast_peak():
    """A consensus peak has not been reported, so nothing can cross it."""
    quarterly = [_quarter(index, 100) for index in range(1, 5)]
    reached = time_to_ninety_percent_peak(
        quarterly,
        selected_peak=_peak(200, estimate_type="consensus"),
        launch_date=date(2024, 1, 1),
    )

    assert reached.months_since_launch is None
    assert reached.reason == "peak_is_not_observed"


def test_time_to_ninety_percent_applies_the_product_scopes():
    """A franchise line is not a figure for one product, whatever its value."""
    franchise = [
        _quarter(index, 500, revenue_scope="Franchise", row_id=f"f{index}")
        for index in range(1, 5)
    ]
    reached = time_to_ninety_percent_peak(
        franchise, selected_peak=_peak(200), launch_date=date(2024, 1, 1)
    )

    assert reached.months_since_launch is None
    assert reached.reason == "incompatible_sales_scope"


def test_an_undated_row_is_not_the_earliest_period():
    """An undated row used to sort first and be returned as the earliest."""
    undated = SalesObservation(
        id="undated",
        period="2024Q1",
        value=1000,
        currency="USD",
        geography="U.S.",
        revenue_scope="Product family",
        period_type="annual",
        period_basis="calendar",
    )
    dated = SalesObservation(
        id="dated",
        period="2025",
        value=1000,
        currency="USD",
        geography="U.S.",
        revenue_scope="Product family",
        period_type="annual",
        period_basis="calendar",
        period_end=date(2025, 12, 31),
    )

    reached = time_to_ninety_percent_peak(
        [undated, dated], selected_peak=_peak(200), launch_date=date(2024, 1, 1)
    )

    assert reached.input_ids == ["dated"]
    assert reached.months_since_launch == 23


def test_a_threshold_nobody_reaches_says_so():
    quarterly = [_quarter(index, 1) for index in range(1, 5)]
    reached = time_to_ninety_percent_peak(
        quarterly, selected_peak=_peak(200), launch_date=date(2024, 1, 1)
    )

    assert reached.months_since_launch is None
    assert reached.reason == "threshold_not_reached"
