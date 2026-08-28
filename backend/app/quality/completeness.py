from __future__ import annotations

from datetime import date

from app.analytics.lifecycle import expected_quarters_for_job, missing_expected_quarters


def lifecycle_gaps(
    *,
    approval_date: date | None,
    known_periods: list[str],
    as_of: date,
    lifecycle_coverage: bool = True,
) -> tuple[list[str], list[str]]:
    """Return (expected quarters, missing quarters) for a job."""

    expected = expected_quarters_for_job(
        approval_date=approval_date,
        known_periods=known_periods,
        as_of=as_of,
        lifecycle_coverage=lifecycle_coverage,
    )
    return expected, missing_expected_quarters(expected, set(known_periods))


def resolve_completeness_pct(
    llm_pct: object,
    *,
    quarterly_count: int,
    unresolved_quarter_count: int,
) -> float:
    """Pick a trustworthy completeness percentage for a drug job.

    The completeness prompt ships a JSON skeleton containing ``"completeness_pct": 0``,
    and models frequently echo that placeholder back. Treat a missing, non-numeric, or
    zero response as "no answer" and fall back to the deterministic coverage ratio.
    """
    try:
        pct = float(llm_pct)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        pct = 0.0
    if not pct:
        pct = 100 * quarterly_count / max(quarterly_count + unresolved_quarter_count, 1)
    return round(min(max(pct, 0.0), 100.0), 1)
