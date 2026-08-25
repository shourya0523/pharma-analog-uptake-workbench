from datetime import date

from app.analytics.peak_sales import (
    PeakEstimate,
    SalesObservation,
    aggregate_comparable_sales,
    select_peak_estimate,
)


def _annual(year: int, value: float, scope: str = "Product family") -> SalesObservation:
    return SalesObservation(
        id=f"a{year}",
        period=str(year),
        value=value,
        currency="USD",
        geography="Worldwide",
        revenue_scope=scope,
        period_type="annual",
        period_basis="calendar",
    )


def test_observed_peak_selected_for_mature_comparable_history():
    observations = [_annual(2020, 100), _annual(2021, 200), _annual(2022, 300), _annual(2023, 265), _annual(2024, 250)]
    annual = aggregate_comparable_sales(observations)
    selected = select_peak_estimate(annual_sales=annual, estimates=[], as_of_date=date(2025, 1, 1))

    assert selected.estimate_type == "observed"
    assert selected.value == 300
    assert selected.input_ids == ["a2022"]


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
        _annual(2024, 900, scope="Company total"),
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

