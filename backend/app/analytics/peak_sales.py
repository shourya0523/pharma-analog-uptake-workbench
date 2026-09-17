"""Pick a product's peak-year sales figure, or say why it cannot be picked.

An observed peak is a claim that the highest year we hold is the highest year
there was. That claim needs three things the raw maximum does not supply: a
series that reaches back to the launch, a series with no hole in it, and later
years that are actually lower. Where one of them is missing the answer is a
reason, not a number - a peak nobody can refuse is a peak nobody can trust.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from statistics import median

from app.domain.models import PeakEstimateType, RevenueScope

# The scopes a peak-sales estimate may be built from: a figure for the whole
# product, for one of its formulations, or for a geography that is the whole
# market a peak is claimed in. Named from the vocabulary rather than spelled
# out, so a renamed scope fails here instead of silently matching nothing.
ALLOWED_PRODUCT_SCOPES = frozenset({
    RevenueScope.PRODUCT_FAMILY.value,
    RevenueScope.FORMULATION_SPECIFIC.value,
    RevenueScope.US.value,
    RevenueScope.WORLDWIDE.value,
})

# `observed` is not a cited estimate - it is derived here from the annual
# series, so an estimate row carrying it would be read by nothing. The cited
# set is the vocabulary minus that one exclusion, so an estimate type added to
# PeakEstimateType is citable by default and has to be excluded deliberately.
DERIVED_ESTIMATE_TYPES = frozenset({PeakEstimateType.OBSERVED.value})
CITED_ESTIMATE_TYPES = frozenset(item.value for item in PeakEstimateType) - DERIVED_ESTIMATE_TYPES

# How old a cited estimate may be and still describe the present. Staleness is
# a property of the citation rather than of the method behind it, so consensus
# and modeled estimates are held to the same window.
RECENCY_WINDOW_DAYS = 365

# Why no peak was selected. Each names the thing that was missing, so the
# analyst is sent to the gap rather than to a null.
NO_ANNUAL_HISTORY = "no_annual_history"
INSUFFICIENT_ANNUAL_HISTORY = "insufficient_annual_history"
ANNUAL_HISTORY_HAS_GAPS = "annual_history_has_gaps"
SERIES_BEGINS_AT_ITS_MAXIMUM = "series_begins_at_its_maximum"
HISTORY_BEGINS_AFTER_LAUNCH = "history_begins_after_launch"
PEAK_NOT_YET_OBSERVED = "peak_not_yet_observed"
CONSENSUS_SCOPE_CONFLICT = "consensus_estimates_disagree_on_scope"
NO_CURRENT_PEAK_ESTIMATE = "no_current_peak_estimate"

OBSERVED_SELECTION_METHOD = "mature_observed_annual_peak_v2"


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
    confidence: float | None = None


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
    """A peak, or a refusal. ``reason`` is set on exactly one of the two.

    The scope travels with the value because a peak is a figure for a currency,
    a geography and a revenue scope - a caller that divides by the number
    without them is dividing by something else.
    """

    estimate_type: str | None
    value: float | None
    currency: str | None
    geography: str | None
    revenue_scope: str | None
    as_of_date: date
    selection_method: str | None
    input_ids: list[str]
    formulation_scope: str | None = None
    reason: str | None = None


def scope_key(
    row: SalesObservation | AnnualSales,
) -> tuple[str, str, str, str, str | None]:
    """What makes two figures comparable: currency, geography, scope, basis.

    The one place the comparability key is written, so aggregation, peak
    selection and the uptake denominator cannot end up disagreeing about what
    "the same series" means.
    """
    return (
        row.currency,
        row.geography,
        row.revenue_scope,
        row.period_basis,
        row.formulation_scope,
    )


def _preference(row: SalesObservation) -> tuple[float, str]:
    """Sort key for choosing between repeated readings of one period.

    Highest declared confidence wins; an observation that declares none ranks
    below one that declares any. The id only breaks ties, so the choice is
    deterministic rather than whichever reading happened to arrive first.
    """
    return (-(row.confidence if row.confidence is not None else -1.0), row.id)


def one_observation_per_period(observations: list[SalesObservation]) -> list[SalesObservation]:
    """Collapse repeated readings of the same period and scope to one figure.

    Layer 1 publishes a period more than once - the same quarter read from the
    press release and from the 10-Q - and a sum over rows counts the quarter
    twice. Readings that differ in scope are different series, not repeats, so
    the scope is part of the key.
    """
    best: dict[tuple[str, tuple[str, str, str, str, str | None]], SalesObservation] = {}
    for row in observations:
        key = (row.period, scope_key(row))
        current = best.get(key)
        if current is None or _preference(row) < _preference(current):
            best[key] = row
    return sorted(best.values(), key=lambda row: (row.period, row.id))


def aggregate_comparable_sales(
    observations: list[SalesObservation],
    *,
    currency: str = "USD",
) -> list[AnnualSales]:
    """Build annual sales without crossing scope, currency, geography, or basis.

    A year is emitted once. An issuer that publishes both the annual figure and
    the four quarters of the same year has published one year twice, so the
    annual figure is kept and the roll-up of its own quarters is dropped.
    """
    eligible = one_observation_per_period(
        [
            row
            for row in observations
            if row.currency == currency and row.revenue_scope in ALLOWED_PRODUCT_SCOPES
        ]
    )

    annual: dict[tuple[str, tuple[str, str, str, str, str | None]], AnnualSales] = {}
    for row in eligible:
        if row.period_type != "annual":
            continue
        annual[(row.period[:4], scope_key(row))] = AnnualSales(
            period=row.period[:4],
            value=row.value,
            currency=row.currency,
            geography=row.geography,
            revenue_scope=row.revenue_scope,
            period_basis=row.period_basis,
            formulation_scope=row.formulation_scope,
            input_ids=[row.id],
        )

    groups: dict[tuple[str, tuple[str, str, str, str, str | None]], list[SalesObservation]] = {}
    for row in eligible:
        if row.period_type != "quarterly":
            continue
        groups.setdefault((row.period[:4], scope_key(row)), []).append(row)
    for key, rows in groups.items():
        if len(rows) != 4 or key in annual:
            continue
        year, (_, geography, scope, basis, formulation) = key
        ordered = sorted(rows, key=lambda item: item.period)
        annual[key] = AnnualSales(
            period=year,
            value=sum(row.value for row in ordered),
            currency=currency,
            geography=geography,
            revenue_scope=scope,
            period_basis=basis,
            formulation_scope=formulation,
            input_ids=[row.id for row in ordered],
        )
    return sorted(annual.values(), key=lambda row: (row.period, scope_key(row)))


def peak_eligible(
    partition: list[AnnualSales],
    peak_index: int,
    *,
    launch_year: int | None,
) -> str | None:
    """Whether the series reaches far enough back for its maximum to be a peak.

    A series that begins after the real peak still has a maximum, and that
    maximum is the first year in it. Either a launch year covers the ramp - the
    history starts no later than the launch - or, with no launch anchor, the
    rise itself stands in for one and at least one lower year must precede the
    maximum. Returns the refusal, or None when the series is eligible.
    """
    first_year = int(partition[0].period)
    if launch_year is not None:
        return HISTORY_BEGINS_AFTER_LAUNCH if first_year > launch_year else None
    return SERIES_BEGINS_AT_ITS_MAXIMUM if peak_index == 0 else None


def _mature_observed_peak(
    partition: list[AnnualSales],
    *,
    launch_year: int | None,
) -> tuple[AnnualSales | None, str | None]:
    """The confirmed peak of one comparable series, or why there is none."""
    if len(partition) < 3:
        return None, INSUFFICIENT_ANNUAL_HISTORY

    years = [int(row.period) for row in partition]
    if years != list(range(years[0], years[0] + len(years))):
        return None, ANNUAL_HISTORY_HAS_GAPS

    maximum = max(partition, key=lambda row: row.value)
    peak_index = partition.index(maximum)
    ineligible = peak_eligible(partition, peak_index, launch_year=launch_year)
    if ineligible:
        return None, ineligible

    later = partition[peak_index + 1 :]
    if len(later) < 2:
        return None, PEAK_NOT_YET_OBSERVED
    # Two independent later years, both below the maximum. A year that repeats
    # the maximum does not confirm it; it is the maximum again.
    if later[0].value < maximum.value and later[1].value < maximum.value:
        return maximum, None
    return None, PEAK_NOT_YET_OBSERVED


def _partitions(annual_sales: list[AnnualSales]) -> list[list[AnnualSales]]:
    """The comparable series inside a mixed history, best-supported first.

    A product reporting both a U.S. and a worldwide line has two series, not
    one incoherent one. Support is the number of annual figures behind a
    series; the scope key breaks ties so the order does not depend on the input
    order.
    """
    grouped: dict[tuple[str, str, str, str, str | None], list[AnnualSales]] = {}
    for row in annual_sales:
        grouped.setdefault(scope_key(row), []).append(row)
    return [
        sorted(rows, key=lambda row: row.period)
        for _, rows in sorted(
            grouped.items(), key=lambda item: (-len(item[1]), item[0])
        )
    ]


def _refusal(as_of_date: date, reason: str) -> SelectedPeak:
    return SelectedPeak(
        estimate_type=None,
        value=None,
        currency=None,
        geography=None,
        revenue_scope=None,
        as_of_date=as_of_date,
        selection_method=None,
        input_ids=[],
        reason=reason,
    )


def select_peak_estimate(
    *,
    annual_sales: list[AnnualSales],
    estimates: list[PeakEstimate],
    as_of_date: date,
    launch_date: date | None = None,
) -> SelectedPeak:
    """The best peak available, or a refusal naming what is missing.

    An observed peak is preferred to a cited one, and the observed peak is
    taken from the best-supported comparable series that yields one - a
    worldwide line with eight years of history is a better answer than the
    refusal a mixed history used to produce.
    """
    launch_year = launch_date.year if launch_date else None
    partitions = _partitions(annual_sales)
    observed_reason = NO_ANNUAL_HISTORY
    for index, partition in enumerate(partitions):
        observed, reason = _mature_observed_peak(partition, launch_year=launch_year)
        if index == 0 and reason:
            observed_reason = reason
        if observed:
            return SelectedPeak(
                estimate_type=PeakEstimateType.OBSERVED.value,
                value=observed.value,
                currency=observed.currency,
                geography=observed.geography,
                revenue_scope=observed.revenue_scope,
                as_of_date=as_of_date,
                selection_method=OBSERVED_SELECTION_METHOD,
                input_ids=observed.input_ids,
                formulation_scope=observed.formulation_scope,
            )

    current = [
        item
        for item in estimates
        if item.estimate_type in CITED_ESTIMATE_TYPES
        and 0 <= (as_of_date - item.as_of_date).days <= RECENCY_WINDOW_DAYS
    ]

    consensus_conflict = False
    consensus = [
        item for item in current if item.estimate_type == PeakEstimateType.CONSENSUS.value
    ]
    if consensus:
        keys = {
            (item.currency, item.geography, item.revenue_scope, item.formulation_scope)
            for item in consensus
        }
        if len(keys) == 1:
            exemplar = consensus[0]
            return SelectedPeak(
                estimate_type=PeakEstimateType.CONSENSUS.value,
                value=float(median(item.value for item in consensus)),
                currency=exemplar.currency,
                geography=exemplar.geography,
                revenue_scope=exemplar.revenue_scope,
                as_of_date=max(item.as_of_date for item in consensus),
                selection_method="current_harmonized_consensus_median_v1",
                input_ids=sorted(item.id for item in consensus),
                formulation_scope=exemplar.formulation_scope,
            )
        consensus_conflict = True

    modeled = sorted(
        (item for item in current if item.estimate_type == PeakEstimateType.MODELED.value),
        key=lambda item: item.as_of_date,
        reverse=True,
    )
    if modeled:
        item = modeled[0]
        return SelectedPeak(
            estimate_type=PeakEstimateType.MODELED.value,
            value=item.value,
            currency=item.currency,
            geography=item.geography,
            revenue_scope=item.revenue_scope,
            as_of_date=item.as_of_date,
            selection_method="cited_patient_model_v1",
            input_ids=[item.id],
            formulation_scope=item.formulation_scope,
        )

    # A history that refused for a substantive reason - a hole, a series that
    # starts at its own maximum - sends the analyst somewhere. "No annual
    # history" does not, so where there is none the estimates get to explain
    # themselves instead.
    if observed_reason != NO_ANNUAL_HISTORY:
        return _refusal(as_of_date, observed_reason)
    if consensus_conflict:
        return _refusal(as_of_date, CONSENSUS_SCOPE_CONFLICT)
    return _refusal(as_of_date, NO_CURRENT_PEAK_ESTIMATE)
