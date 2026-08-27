from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.analytics.peak_sales import SalesObservation


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


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


def _quarter_index(period: str) -> int | None:
    match = re.fullmatch(r"(\d{4})Q([1-4])", period)
    if not match:
        return None
    return int(match.group(1)) * 4 + int(match.group(2)) - 1


def _is_consecutive_quarter_window(window: list[SalesObservation]) -> bool:
    indices = [_quarter_index(item.period) for item in window]
    return all(index is not None for index in indices) and indices == list(
        range(indices[0], indices[0] + len(indices))
    )


def _has_compatible_scope(window: list[SalesObservation]) -> bool:
    return (
        len(
            {
                (
                    item.currency,
                    item.geography,
                    item.revenue_scope,
                    item.period_basis,
                    item.formulation_scope,
                )
                for item in window
            }
        )
        == 1
    )


def calculate_revenue_uptake(
    *,
    observations: list[SalesObservation],
    selected_annual_peak: float | None,
    launch_date: date | None,
) -> list[UptakePoint]:
    rows = sorted(
        (row for row in observations if row.period_type == "quarterly"),
        key=lambda row: row.period,
    )
    points: list[UptakePoint] = []
    for index, row in enumerate(rows):
        period_end = row.period_end
        months = (
            _months_between(launch_date, period_end)
            if launch_date and period_end
            else None
        )
        window = rows[max(0, index - 3) : index + 1]
        reason = None
        if not launch_date:
            reason = "missing_launch_anchor"
        elif not selected_annual_peak or selected_annual_peak <= 0:
            reason = "missing_selected_peak"
        elif len(window) < 4:
            reason = "insufficient_history"
        elif not _is_consecutive_quarter_window(window):
            reason = "nonconsecutive_quarters"
        elif not _has_compatible_scope(window):
            reason = "incompatible_sales_scope"
        valid_window = len(window) == 4 and reason is None
        numerator = sum(item.value for item in window) if valid_window else None
        points.append(
            UptakePoint(
                period=row.period,
                metric_type="revenue_proxy_r4q",
                value=(numerator / selected_annual_peak)
                if numerator is not None and not reason
                else None,
                numerator=numerator,
                denominator=selected_annual_peak,
                months_since_launch=months,
                missing_reason=reason,
                input_ids=[item.id for item in window] if valid_window else [],
            )
        )
    return points


def time_to_ninety_percent_peak(
    observations: list[SalesObservation],
    *,
    selected_peak: float,
    launch_date: date,
) -> SalesObservation | None:
    threshold = selected_peak * 0.9
    eligible = sorted(
        (
            row
            for row in observations
            if row.period_end is None or row.period_end >= launch_date
        ),
        key=lambda row: (row.period_end or date.min, row.period),
    )
    candidates = [
        row
        for row in eligible
        if row.period_type == "annual" and row.value >= threshold
    ]
    quarterly = [row for row in eligible if row.period_type == "quarterly"]
    for index, row in enumerate(quarterly):
        window = quarterly[max(0, index - 3) : index + 1]
        if (
            len(window) == 4
            and _is_consecutive_quarter_window(window)
            and _has_compatible_scope(window)
            and sum(item.value for item in window) >= threshold
        ):
            candidates.append(row)
    return min(
        candidates,
        key=lambda row: (row.period_end or date.min, row.period),
        default=None,
    )
