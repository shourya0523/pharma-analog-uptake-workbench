from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from statistics import median

ALLOWED_PRODUCT_SCOPES = {"Product family", "Formulation-specific", "U.S.", "Worldwide"}


@dataclass(frozen=True)
class SalesObservation:
    id: str
    period: str
    value: float
    currency: str
    geography: str
    revenue_scope: str
    period_type: str
    period_basis: str
    formulation_scope: str | None = None
    period_end: date | None = None


@dataclass(frozen=True)
class AnnualSales:
    period: str
    value: float
    currency: str
    geography: str
    revenue_scope: str
    period_basis: str
    formulation_scope: str | None
    input_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PeakEstimate:
    id: str
    estimate_type: str
    value: float
    currency: str
    geography: str
    revenue_scope: str
    as_of_date: date
    source_url: str
    formulation_scope: str | None = None


@dataclass(frozen=True)
class SelectedPeak:
    estimate_type: str
    value: float
    currency: str
    geography: str
    revenue_scope: str
    as_of_date: date
    selection_method: str
    input_ids: list[str]


def aggregate_comparable_sales(
    observations: list[SalesObservation],
    *,
    currency: str = "USD",
) -> list[AnnualSales]:
    """Build annual sales without crossing scope, currency, geography, or period basis."""

    eligible = [
        row
        for row in observations
        if row.currency == currency and row.revenue_scope in ALLOWED_PRODUCT_SCOPES
    ]
    annual: list[AnnualSales] = []
    for row in eligible:
        if row.period_type == "annual":
            annual.append(
                AnnualSales(
                    period=row.period[:4],
                    value=row.value,
                    currency=row.currency,
                    geography=row.geography,
                    revenue_scope=row.revenue_scope,
                    period_basis=row.period_basis,
                    formulation_scope=row.formulation_scope,
                    input_ids=[row.id],
                )
            )

    groups: dict[tuple[str, str, str, str, str | None], list[SalesObservation]] = {}
    for row in eligible:
        if row.period_type != "quarterly":
            continue
        key = (
            row.period[:4],
            row.geography,
            row.revenue_scope,
            row.period_basis,
            row.formulation_scope,
        )
        groups.setdefault(key, []).append(row)
    for (year, geography, scope, basis, formulation), rows in groups.items():
        if len({row.period for row in rows}) != 4:
            continue
        annual.append(
            AnnualSales(
                period=year,
                value=sum(row.value for row in rows),
                currency=currency,
                geography=geography,
                revenue_scope=scope,
                period_basis=basis,
                formulation_scope=formulation,
                input_ids=[row.id for row in sorted(rows, key=lambda item: item.period)],
            )
        )
    return sorted(annual, key=lambda row: row.period)


def _mature_observed_peak(annual_sales: list[AnnualSales]) -> AnnualSales | None:
    if len(annual_sales) < 3:
        return None
    keys = {
        (row.currency, row.geography, row.revenue_scope, row.period_basis, row.formulation_scope)
        for row in annual_sales
    }
    if len(keys) != 1:
        return None
    maximum = max(annual_sales, key=lambda row: row.value)
    peak_index = annual_sales.index(maximum)
    later = annual_sales[peak_index + 1 :]
    if len(later) < 2:
        return None
    if all(row.value <= maximum.value * 0.9 for row in later[:2]) or later[1].value <= later[0].value:
        return maximum
    return None


def comparable_key(row: AnnualSales | SalesObservation) -> tuple:
    return (
        row.currency,
        row.geography,
        row.revenue_scope,
        row.period_basis,
        row.formulation_scope,
    )


def group_annual_sales(annual_sales: list[AnnualSales]) -> dict[tuple, list[AnnualSales]]:
    groups: dict[tuple, list[AnnualSales]] = {}
    for row in annual_sales:
        groups.setdefault(comparable_key(row), []).append(row)
    return {key: sorted(rows, key=lambda item: item.period) for key, rows in groups.items()}


def sales_observation_from_payload(row: dict) -> SalesObservation:
    """Map a gold/datapoint payload onto the typed observation used by peak selection."""

    value = row.get("value_normalized_usd_millions")
    if value is None:
        value = row.get("value_reported")
    return SalesObservation(
        id=str(row.get("id") or row.get("gold_id") or row.get("period") or ""),
        period=str(row["period"]),
        value=float(value),
        currency=str(row.get("currency") or "USD"),
        geography=str(row.get("geography") or "Worldwide"),
        revenue_scope=str(row.get("revenue_scope") or "Unknown"),
        period_type=str(row.get("period_type") or "quarterly"),
        period_basis=str(row.get("period_basis") or "calendar"),
        formulation_scope=row.get("formulation") or row.get("formulation_scope"),
    )


def select_peak_from_observations(
    observations: list[SalesObservation],
    *,
    estimates: list[PeakEstimate] | None = None,
    as_of_date: date,
) -> SelectedPeak | None:
    """Pick a peak from the longest comparable series that can support the policy."""

    annual = aggregate_comparable_sales(observations)
    groups = group_annual_sales(annual)
    ranked = sorted(groups.values(), key=lambda rows: (-len(rows), rows[0].revenue_scope))
    fallback: SelectedPeak | None = None
    for series in ranked:
        selected = select_peak_estimate(
            annual_sales=series,
            estimates=estimates or [],
            as_of_date=as_of_date,
        )
        if selected is None:
            continue
        if selected.estimate_type == "observed":
            return selected
        if fallback is None:
            fallback = selected
    return fallback


def complete_comparable_years(observations: list[SalesObservation]) -> int:
    annual = aggregate_comparable_sales(observations)
    if not annual:
        return 0
    longest = max(group_annual_sales(annual).values(), key=len)
    return len(longest)


def select_peak_estimate(
    *,
    annual_sales: list[AnnualSales],
    estimates: list[PeakEstimate],
    as_of_date: date,
) -> SelectedPeak | None:
    observed = _mature_observed_peak(annual_sales)
    if observed:
        return SelectedPeak(
            "observed",
            observed.value,
            observed.currency,
            observed.geography,
            observed.revenue_scope,
            as_of_date,
            "mature_observed_annual_peak_v1",
            observed.input_ids,
        )

    current_consensus = [
        item
        for item in estimates
        if item.estimate_type == "consensus"
        and 0 <= (as_of_date - item.as_of_date).days <= 365
    ]
    if current_consensus:
        keys = {
            (item.currency, item.geography, item.revenue_scope, item.formulation_scope)
            for item in current_consensus
        }
        if len(keys) == 1:
            exemplar = current_consensus[0]
            return SelectedPeak(
                "consensus",
                float(median(item.value for item in current_consensus)),
                exemplar.currency,
                exemplar.geography,
                exemplar.revenue_scope,
                max(item.as_of_date for item in current_consensus),
                "current_harmonized_consensus_median_v1",
                sorted(item.id for item in current_consensus),
            )

    modeled = sorted(
        (item for item in estimates if item.estimate_type == "modeled"),
        key=lambda item: item.as_of_date,
        reverse=True,
    )
    if modeled:
        item = modeled[0]
        return SelectedPeak(
            "modeled",
            item.value,
            item.currency,
            item.geography,
            item.revenue_scope,
            item.as_of_date,
            "cited_patient_model_v1",
            [item.id],
        )
    return None

