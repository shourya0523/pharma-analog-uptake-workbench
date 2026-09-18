"""Tests for peak-sales selection.

The behaviour worth protecting is the refusal. A maximum is easy; a peak is a
claim that nothing higher came before or after it, and the four shapes below
are the ones where that claim used to be made without the evidence for it.
"""

from datetime import date

from app.analytics.peak_sales import (
    PeakEstimate,
    SalesObservation,
    aggregate_comparable_sales,
    one_observation_per_period,
    select_peak_estimate,
)


def _annual(
    year: int,
    value: float,
    scope: str = "Product family",
    *,
    geography: str = "Worldwide",
    row_id: str | None = None,
) -> SalesObservation:
    return SalesObservation(
        id=row_id or f"a{year}",
        period=str(year),
        value=value,
        currency="USD",
        geography=geography,
        revenue_scope=scope,
        period_type="annual",
        period_basis="calendar",
    )


def _quarter(year: int, quarter: int, value: float, *, row_id: str | None = None, confidence=None):
    return SalesObservation(
        id=row_id or f"q{year}Q{quarter}",
        period=f"{year}Q{quarter}",
        value=value,
        currency="USD",
        geography="Worldwide",
        revenue_scope="Product family",
        period_type="quarterly",
        period_basis="calendar",
        period_end=date(year, quarter * 3, 28),
        confidence=confidence,
    )


def test_observed_peak_selected_for_mature_comparable_history():
    observations = [
        _annual(2020, 100),
        _annual(2021, 200),
        _annual(2022, 300),
        _annual(2023, 265),
        _annual(2024, 250),
    ]
    annual = aggregate_comparable_sales(observations)
    selected = select_peak_estimate(annual_sales=annual, estimates=[], as_of_date=date(2025, 1, 1))

    assert selected.estimate_type == "observed"
    assert selected.value == 300
    assert selected.input_ids == ["a2022"]
    assert selected.reason is None
    # The scope travels with the figure, so a caller cannot divide by it
    # without knowing what it is a figure for.
    assert (selected.currency, selected.geography, selected.revenue_scope) == (
        "USD",
        "Worldwide",
        "Product family",
    )


def test_a_series_that_begins_at_its_maximum_is_refused():
    """A declining series has a maximum; whether it is the peak is unknown."""
    annual = aggregate_comparable_sales([_annual(2020, 900), _annual(2021, 700), _annual(2022, 500)])
    selected = select_peak_estimate(annual_sales=annual, estimates=[], as_of_date=date(2023, 1, 1))

    assert selected.value is None
    assert selected.estimate_type is None
    assert selected.reason == "series_begins_at_its_maximum"


def test_a_launch_anchor_refuses_a_history_that_starts_after_the_launch():
    annual = aggregate_comparable_sales(
        [_annual(2020, 100), _annual(2021, 400), _annual(2022, 300), _annual(2023, 200)]
    )
    covered = select_peak_estimate(
        annual_sales=annual,
        estimates=[],
        as_of_date=date(2024, 1, 1),
        launch_date=date(2020, 5, 1),
    )
    uncovered = select_peak_estimate(
        annual_sales=annual,
        estimates=[],
        as_of_date=date(2024, 1, 1),
        launch_date=date(2014, 5, 1),
    )

    assert covered.estimate_type == "observed"
    assert covered.value == 400
    assert uncovered.value is None
    assert uncovered.reason == "history_begins_after_launch"


def test_a_hole_in_the_history_is_not_a_confirmation():
    """Two years seven years later do not confirm anything about 2011."""
    annual = aggregate_comparable_sales(
        [_annual(2010, 100), _annual(2011, 400), _annual(2018, 200), _annual(2019, 150)]
    )
    selected = select_peak_estimate(annual_sales=annual, estimates=[], as_of_date=date(2020, 1, 1))

    assert selected.value is None
    assert selected.reason == "annual_history_has_gaps"


def test_a_year_published_twice_is_one_year():
    """An annual figure and the four quarters of the same year are one year.

    The duplicate used to be the later, lower year that confirmed its own peak.
    """
    observations = [
        _annual(2020, 300),
        _annual(2021, 400),
        _annual(2021, 400, row_id="a2021-press"),
        _annual(2022, 200),
    ]
    annual = aggregate_comparable_sales(observations)
    selected = select_peak_estimate(annual_sales=annual, estimates=[], as_of_date=date(2023, 1, 1))

    assert [row.period for row in annual] == ["2020", "2021", "2022"]
    assert selected.value is None
    assert selected.reason == "peak_not_yet_observed"


def test_the_annual_figure_wins_over_a_roll_up_of_its_own_quarters():
    observations = [_annual(2021, 400)] + [_quarter(2021, index, 100) for index in (1, 2, 3, 4)]
    annual = aggregate_comparable_sales(observations)

    assert [(row.period, row.value, row.input_ids) for row in annual] == [("2021", 400, ["a2021"])]


def test_a_quarter_read_twice_is_not_added_twice():
    observations = [_quarter(2021, index, 100) for index in (1, 2, 3, 4)]
    observations.append(_quarter(2021, 2, 100, row_id="q2021Q2-10q", confidence=0.9))
    annual = aggregate_comparable_sales(observations)

    assert [row.value for row in annual] == [400]
    # The higher-confidence reading is the one kept.
    assert "q2021Q2-10q" in annual[0].input_ids
    assert "q2021Q2" not in annual[0].input_ids


def test_two_scopes_give_a_partition_answer_not_a_shrug():
    """A U.S. line beside a worldwide one is two series, not one bad one."""
    observations = [
        _annual(2020, 100, geography="U.S.", scope="U.S.", row_id="us2020"),
        _annual(2021, 120, geography="U.S.", scope="U.S.", row_id="us2021"),
        _annual(2020, 300, row_id="ww2020"),
        _annual(2021, 500, row_id="ww2021"),
        _annual(2022, 400, row_id="ww2022"),
        _annual(2023, 350, row_id="ww2023"),
    ]
    selected = select_peak_estimate(
        annual_sales=aggregate_comparable_sales(observations),
        estimates=[],
        as_of_date=date(2024, 1, 1),
    )

    assert selected.estimate_type == "observed"
    assert selected.value == 500
    assert selected.geography == "Worldwide"
    assert selected.input_ids == ["ww2021"]


def test_growing_product_selects_current_consensus_median():
    observations = [_annual(2022, 100), _annual(2023, 150), _annual(2024, 225)]
    estimates = [
        PeakEstimate("c1", "consensus", 500, "USD", "Worldwide", "Product family", date(2024, 8, 1), "source-1"),
        PeakEstimate("c2", "consensus", 700, "USD", "Worldwide", "Product family", date(2024, 9, 1), "source-2"),
    ]
    selected = select_peak_estimate(
        annual_sales=aggregate_comparable_sales(observations),
        estimates=estimates,
        as_of_date=date(2025, 1, 1),
    )

    assert selected.estimate_type == "consensus"
    assert selected.value == 600
    assert selected.input_ids == ["c1", "c2"]


def test_incompatible_scope_and_currency_are_not_aggregated():
    observations = [
        _annual(2024, 100),
        _annual(2024, 900, scope="Company total", row_id="company"),
        SalesObservation("eur", "2024", 200, "EUR", "Worldwide", "Product family", "annual", "calendar"),
    ]
    annual = aggregate_comparable_sales(observations)

    assert len(annual) == 1
    assert annual[0].value == 100


def test_modeled_peak_remains_typed_fallback():
    modeled = PeakEstimate(
        "m1", "modeled", 420, "USD", "U.S.", "Formulation-specific", date(2025, 1, 1), "patient-model"
    )
    selected = select_peak_estimate(annual_sales=[], estimates=[modeled], as_of_date=date(2025, 2, 1))
    assert selected.estimate_type == "modeled"
    assert selected.value == 420


def test_a_stale_model_is_refused_like_a_stale_consensus():
    """Staleness belongs to the citation, so both cited types share the window."""
    modeled = PeakEstimate(
        "m1", "modeled", 420, "USD", "U.S.", "Formulation-specific", date(2021, 1, 1), "patient-model"
    )
    selected = select_peak_estimate(annual_sales=[], estimates=[modeled], as_of_date=date(2025, 2, 1))

    assert selected.value is None
    assert selected.reason == "no_current_peak_estimate"


def test_one_observation_per_period_keeps_different_scopes_apart():
    us = SalesObservation("us", "2021Q1", 40, "USD", "U.S.", "U.S.", "quarterly", "calendar")
    worldwide = SalesObservation("ww", "2021Q1", 100, "USD", "Worldwide", "Worldwide", "quarterly", "calendar")
    repeat = SalesObservation("ww2", "2021Q1", 100, "USD", "Worldwide", "Worldwide", "quarterly", "calendar")

    kept = one_observation_per_period([us, worldwide, repeat])

    assert sorted(row.id for row in kept) == ["us", "ww"]
