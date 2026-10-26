"""The date window retrieval and completeness both ask about.

A run that declares ``earnings_since`` / ``earnings_until`` asks for those
bounds. A run that declares neither still asks for a window: the product's
FDA approval through today when the label pass wrote one, otherwise a fixed
lookback ending today. Without that, SEC falls back to "the newest N filings"
while completeness invents every quarter since approval - under-retrieval
disguised as coverage gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from dateutil.relativedelta import relativedelta


@dataclass(frozen=True)
class FilingWindow:
    """Inclusive bounds and how they were chosen."""

    since: date
    until: date
    # caller | approval | lookback — what produced the bounds when either end
    # was empty. A fully specified caller window still reports "caller".
    source: str


def resolve_filing_window(
    *,
    since: date | None,
    until: date | None,
    approval: date | None,
    today: date | None = None,
    lookback_years: int = 5,
) -> FilingWindow:
    """Derive the filing window a run will retrieve and score against.

    Caller bounds win when present. A missing ``since`` falls back to the
    approval date, then to ``today - lookback_years``. A missing ``until``
    falls back to ``today``. ``since`` is never after ``until``.
    """
    today = today or date.today()
    lookback_years = max(1, lookback_years)

    if since is not None and until is not None:
        start, end, origin = since, until, "caller"
    elif since is not None:
        start, end, origin = since, today, "caller"
    elif until is not None:
        if approval is not None:
            start, end, origin = approval, until, "approval"
        else:
            start, end, origin = until - relativedelta(years=lookback_years), until, "lookback"
    elif approval is not None:
        start, end, origin = approval, today, "approval"
    else:
        start, end, origin = today - relativedelta(years=lookback_years), today, "lookback"

    if start > end:
        start, end = end, start
    return FilingWindow(since=start, until=end, source=origin)
