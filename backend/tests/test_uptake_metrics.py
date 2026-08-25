from datetime import date

from app.analytics.peak_sales import SalesObservation
from app.analytics.uptake import calculate_revenue_uptake, time_to_ninety_percent_peak


def _quarter(index: int, value: float) -> SalesObservation:
    year = 2024 + (index - 1) // 4
    quarter = (index - 1) % 4 + 1
    return SalesObservation(
        id=f"q{index}",
        period=f"{year}Q{quarter}",
        value=value,
        currency="USD",
        geography="U.S.",
        revenue_scope="Product family",
        period_type="quarterly",
        period_basis="calendar",
        period_end=date(year, quarter * 3, 28),
    )


def test_rolling_four_quarter_uptake_marks_sparse_history():
    points = calculate_revenue_uptake(
        observations=[
            _quarter(1, 10),
            _quarter(2, 20),
            _quarter(3, 30),
            _quarter(4, 40),
        ],
        selected_annual_peak=200,
        launch_date=date(2024, 1, 1),
    )

    assert [point.missing_reason for point in points[:3]] == [
        "insufficient_history"
    ] * 3
    assert points[3].value == 0.5
    assert points[3].metric_type == "revenue_proxy_r4q"
    assert points[3].input_ids == ["q1", "q2", "q3", "q4"]


def test_first_period_reaching_ninety_percent_of_peak():
    quarterly = [_quarter(1, 20), _quarter(2, 30), _quarter(3, 40), _quarter(4, 100)]
    reached = time_to_ninety_percent_peak(
        quarterly, selected_peak=200, launch_date=date(2024, 1, 1)
    )
    assert reached.period == "2024Q4"


def test_nonconsecutive_quarters_do_not_form_a_rolling_year():
    observations = [_quarter(1, 10), _quarter(2, 20), _quarter(4, 40), _quarter(5, 50)]
    points = calculate_revenue_uptake(
        observations=observations,
        selected_annual_peak=200,
        launch_date=date(2024, 1, 1),
    )
    assert points[-1].value is None
    assert points[-1].missing_reason == "nonconsecutive_quarters"


def test_missing_launch_anchor_returns_explicit_reason():
    points = calculate_revenue_uptake(
        observations=[_quarter(1, 10)],
        selected_annual_peak=200,
        launch_date=None,
    )
    assert points[0].missing_reason == "missing_launch_anchor"
