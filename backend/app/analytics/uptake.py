"""Express a product's sales as a share of its peak, or refuse to.

A share is a ratio, and a ratio between two figures of different scope is not a
small error - it is a number with no meaning at all, reading as an early ramp.
So the peak arrives here with its currency, geography and revenue scope
attached, and the numerator is built only from readings that match it. A
quarter that exists in another scope is refused by name rather than counted, or
worse, mistaken for a missing quarter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.analytics.peak_sales import (
    ALLOWED_PRODUCT_SCOPES,
    SalesObservation,
    SelectedPeak,
    one_observation_per_period,
)
from app.domain.models import PeakEstimateType

# Why a period has no value. Ordered here as they are tested: a mismatch
# between numerator and denominator is checked before anything about the shape
# of the window, because a window built from the wrong scope is not a window
# with a gap in it - it is the wrong series.
MISSING_LAUNCH_ANCHOR = "missing_launch_anchor"
MISSING_SELECTED_PEAK = "missing_selected_peak"
INCOMPATIBLE_SALES_SCOPE = "incompatible_sales_scope"
INSUFFICIENT_HISTORY = "insufficient_history"
NONCONSECUTIVE_QUARTERS = "nonconsecutive_quarters"

# Why no period reached the threshold.
PEAK_IS_NOT_OBSERVED = "peak_is_not_observed"
UNDATED_OBSERVATIONS = "undated_observations"
THRESHOLD_NOT_REACHED = "threshold_not_reached"

WINDOW_QUARTERS = 4
THRESHOLD_SHARE = 0.9


@dataclass(frozen=True)
class UptakePoint:
    period: str
    metric_type: str
    value: float | None
    numerator: float | None
    denominator: float | None
    months_since_launch: int | None
    missing_reason: str | None
    input_ids: list[str]


@dataclass(frozen=True)
class TimeToThreshold:
    """How long after launch a product first reached a share of its peak.

    A time, not an observation: the question is how long the ramp took, and an
    observation answers a different one.
    """

    months_since_launch: int | None
    period: str | None
    threshold: float | None
    reason: str | None
    input_ids: list[str]


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


def _quarter_index(period: str) -> int | None:
    match = re.fullmatch(r"(\d{4})Q([1-4])", period)
    if not match:
        return None
    return int(match.group(1)) * 4 + int(match.group(2)) - 1


def _matches_peak_scope(row: SalesObservation, peak: SelectedPeak) -> bool:
    """Whether a reading is a figure for the same thing the peak is a figure for.

    ``ALLOWED_PRODUCT_SCOPES`` is applied here as well as in peak selection,
    because a cited estimate carries whatever revenue scope its source stated -
    a company total is not a denominator for a product.
    """
    return (
        row.revenue_scope in ALLOWED_PRODUCT_SCOPES
        and peak.revenue_scope in ALLOWED_PRODUCT_SCOPES
        and row.currency == peak.currency
        and row.geography == peak.geography
        and row.revenue_scope == peak.revenue_scope
        and row.formulation_scope == peak.formulation_scope
    )


def _usable_peak(peak: SelectedPeak | None) -> bool:
    return peak is not None and peak.reason is None and bool(peak.value) and peak.value > 0


def calculate_revenue_uptake(
    *,
    observations: list[SalesObservation],
    selected_peak: SelectedPeak | None,
    launch_date: date | None,
) -> list[UptakePoint]:
    """A trailing-four-quarter share of peak for each quarter, or a reason.

    The window is four distinct quarters, not four rows. A quarter read twice
    is one quarter, and a quarter reported in a scope the peak is not in is not
    part of this series at all.
    """
    quarterly = one_observation_per_period(
        [row for row in observations if row.period_type == "quarterly"]
    )
    periods = sorted({row.period for row in quarterly})
    in_scope = (
        {
            row.period: row
            for row in quarterly
            if _matches_peak_scope(row, selected_peak)
        }
        if _usable_peak(selected_peak)
        else {}
    )
    scoped_periods = sorted(in_scope)

    points: list[UptakePoint] = []
    for period in periods:
        row = in_scope.get(period)
        period_end = row.period_end if row else None
        months = (
            _months_between(launch_date, period_end) if launch_date and period_end else None
        )

        window: list[SalesObservation] = []
        reason = None
        if not launch_date:
            reason = MISSING_LAUNCH_ANCHOR
        elif not _usable_peak(selected_peak):
            reason = MISSING_SELECTED_PEAK
        elif row is None:
            # The quarter was reported, but not for the thing the peak is a
            # figure for. That is a mismatch, not a hole.
            reason = INCOMPATIBLE_SALES_SCOPE
        else:
            position = scoped_periods.index(period)
            if position + 1 < WINDOW_QUARTERS:
                reason = INSUFFICIENT_HISTORY
            else:
                wanted = scoped_periods[position + 1 - WINDOW_QUARTERS : position + 1]
                indices = [_quarter_index(item) for item in wanted]
                if any(index is None for index in indices) or indices != list(
                    range(indices[0], indices[0] + WINDOW_QUARTERS)
                ):
                    reason = NONCONSECUTIVE_QUARTERS
                else:
                    window = [in_scope[item] for item in wanted]

        numerator = sum(item.value for item in window) if window else None
        points.append(
            UptakePoint(
                period=period,
                metric_type="revenue_proxy_r4q",
                value=(numerator / selected_peak.value) if numerator is not None else None,
                numerator=numerator,
                denominator=selected_peak.value if _usable_peak(selected_peak) else None,
                months_since_launch=months,
                missing_reason=reason,
                input_ids=[item.id for item in window],
            )
        )
    return points


def time_to_ninety_percent_peak(
    observations: list[SalesObservation],
    *,
    selected_peak: SelectedPeak | None,
    launch_date: date,
) -> TimeToThreshold:
    """Months from launch to the first period reaching the threshold share.

    Only an observed peak can be crossed. A consensus or modeled peak is a
    forecast of a number nothing has reported yet, so "never reached" would be
    a statement about the forecast rather than about the product - it is
    refused by name instead.
    """
    if not _usable_peak(selected_peak):
        return TimeToThreshold(None, None, None, MISSING_SELECTED_PEAK, [])
    if selected_peak.estimate_type != PeakEstimateType.OBSERVED.value:
        return TimeToThreshold(None, None, None, PEAK_IS_NOT_OBSERVED, [])

    threshold = selected_peak.value * THRESHOLD_SHARE
    scoped = [row for row in observations if _matches_peak_scope(row, selected_peak)]
    if not scoped:
        return TimeToThreshold(None, None, threshold, INCOMPATIBLE_SALES_SCOPE, [])

    dated = one_observation_per_period(
        [row for row in scoped if row.period_end is not None and row.period_end >= launch_date]
    )
    if not dated:
        return TimeToThreshold(None, None, threshold, UNDATED_OBSERVATIONS, [])

    candidates: list[tuple[date, str, list[str]]] = [
        (row.period_end, row.period, [row.id])
        for row in dated
        if row.period_type == "annual" and row.value >= threshold
    ]

    quarterly = sorted(
        (row for row in dated if row.period_type == "quarterly"), key=lambda row: row.period
    )
    for index in range(WINDOW_QUARTERS - 1, len(quarterly)):
        window = quarterly[index + 1 - WINDOW_QUARTERS : index + 1]
        indices = [_quarter_index(item.period) for item in window]
        if any(item is None for item in indices):
            continue
        if indices != list(range(indices[0], indices[0] + WINDOW_QUARTERS)):
            continue
        if sum(item.value for item in window) >= threshold:
            last = window[-1]
            candidates.append((last.period_end, last.period, [item.id for item in window]))

    if not candidates:
        return TimeToThreshold(None, None, threshold, THRESHOLD_NOT_REACHED, [])

    period_end, period, input_ids = min(candidates, key=lambda item: (item[0], item[1]))
    return TimeToThreshold(
        months_since_launch=_months_between(launch_date, period_end),
        period=period,
        threshold=threshold,
        reason=None,
        input_ids=input_ids,
    )
