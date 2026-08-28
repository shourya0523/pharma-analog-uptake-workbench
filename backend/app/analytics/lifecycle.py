"""Per-drug commercial lifecycle: every quarter from approval through as-of.

Analog peak sales cannot be observed from a sliding recent window. The unit of
coverage is the full launch-to-present quarter grid for one product.
"""

from __future__ import annotations

import re
from datetime import date

from app.parsing.periods import quarter_of_month

_QUARTER_RE = re.compile(r"^(\d{4})Q([1-4])$")


def parse_quarter_label(period: str | None) -> tuple[int, int] | None:
    match = _QUARTER_RE.fullmatch(str(period or "").strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def quarter_label(year: int, quarter: int) -> str:
    if quarter < 1 or quarter > 4:
        raise ValueError("quarter must be 1-4")
    return f"{year}Q{quarter}"


def quarter_of_date(value: date) -> str:
    return quarter_label(value.year, quarter_of_month(value.month))


def latest_completed_quarter(as_of: date) -> str:
    """Last calendar quarter whose period has fully elapsed as of ``as_of``."""

    quarter = quarter_of_month(as_of.month)
    if quarter == 1:
        return quarter_label(as_of.year - 1, 4)
    return quarter_label(as_of.year, quarter - 1)


def iter_quarters(start: str, end: str) -> list[str]:
    parsed_start = parse_quarter_label(start)
    parsed_end = parse_quarter_label(end)
    if parsed_start is None or parsed_end is None:
        return []
    if parsed_start > parsed_end:
        return []
    year, quarter = parsed_start
    out: list[str] = []
    while (year, quarter) <= parsed_end:
        out.append(quarter_label(year, quarter))
        quarter += 1
        if quarter > 4:
            quarter = 1
            year += 1
    return out


def lifecycle_start_quarter(approval_date: date) -> str:
    return quarter_of_date(approval_date)


def lifecycle_quarters(*, approval_date: date, as_of: date) -> list[str]:
    """Every commercial quarter from the approval quarter through the last completed one."""

    return iter_quarters(lifecycle_start_quarter(approval_date), latest_completed_quarter(as_of))


def expected_quarters_for_job(
    *,
    approval_date: date | None,
    known_periods: list[str],
    as_of: date,
    lifecycle_coverage: bool = True,
) -> list[str]:
    """Quarters the job must account for (reported or explicitly unresolved).

    With lifecycle coverage and an approval date, the grid is approval through
    the last completed quarter. Otherwise fall back to the extracted min/max
    span, still extended forward to the last completed quarter when possible.
    """

    end = latest_completed_quarter(as_of)
    known = [p for p in known_periods if parse_quarter_label(p)]
    if lifecycle_coverage and approval_date:
        return lifecycle_quarters(approval_date=approval_date, as_of=as_of)
    if not known:
        return []
    start = min(known)
    if not lifecycle_coverage:
        return iter_quarters(start, max(known))
    return iter_quarters(start, end)


def missing_expected_quarters(expected: list[str], known: set[str]) -> list[str]:
    return [period for period in expected if period not in known]


def coverage_pct(reported_count: int, expected_count: int) -> float:
    if expected_count <= 0:
        return 0.0
    return round(100.0 * reported_count / expected_count, 1)
