from __future__ import annotations

import re
from typing import NamedTuple

from sqlalchemy.orm import Session

from app.db.models import DatapointORM, DrugJobORM, UnresolvedQuarterORM
from app.domain.models import PeriodType, UnresolvedResolution, ValidationStatus
from app.parsing.periods import normalize_period

# The canonical shape normalize_period returns for a single quarter, as its
# docstring states it: 2024Q2. Asking the project's own parser, rather than
# testing the raw label for a "Q", is what lets a gap recorded as "FY2024 Q2"
# and a datapoint labelled "2024Q2" be recognised as the same quarter.
_CANONICAL_QUARTER = re.compile(r"^\d{4}Q[1-4]$")


def names_a_quarter(period: object) -> bool:
    """Whether a period label names one quarter rather than a longer span.

    Both sides of the ratio ask this, so that the quarters counted as present
    and the gaps counted as missing cannot disagree about what a quarter is.
    """
    label = normalize_period(period)
    return bool(label and _CANONICAL_QUARTER.match(label))


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


class Completeness(NamedTuple):
    """What the recount found, so a caller can report it without recounting."""

    pct: float
    quarters: int
    gaps: int


def refresh_completeness(db: Session, job: DrugJobORM, *, llm_pct: object = None) -> Completeness:
    """Recount what a job holds, and set the two fields derived from it.

    Both numbers are functions of rows that review changes: entering a value
    for a gap adds a quarter and closes the gap it came from, and rejecting a
    figure takes a quarter away. Computed once at the end of the run, they
    described the run rather than the job, and a reviewer could resolve every
    gap without either moving.

    ``llm_pct`` is the model's own figure, which only the run has to offer.
    A reviewer acting on the job postdates it, so from then on the count is
    the only honest answer and the default of None asks for it.

    Flushes first because the session does not autoflush: a caller that has
    added rows and not committed is asking about those rows too.
    """
    db.flush()
    quarters_held = (
        db.query(DatapointORM)
        .filter(
            DatapointORM.job_id == job.id,
            DatapointORM.period_type == PeriodType.QUARTERLY.value,
            # A figure the reviewer rejected is not an answer to its quarter.
            DatapointORM.validation_status != ValidationStatus.REJECTED.value,
        )
        .all()
    )
    open_gaps = [
        row
        for row in db.query(UnresolvedQuarterORM).filter_by(job_id=job.id).all()
        if not _is_answered(row.resolution)
    ]
    quarters = sum(1 for row in quarters_held if names_a_quarter(row.period))
    gaps = sum(1 for row in open_gaps if names_a_quarter(row.period))
    job.unresolved_count = len(open_gaps)
    job.completeness_pct = resolve_completeness_pct(
        llm_pct, quarterly_count=quarters, unresolved_quarter_count=gaps
    )
    return Completeness(job.completeness_pct, quarters, gaps)


def _is_answered(resolution: str | None) -> bool:
    """Whether a stored resolution string closes its quarter.

    A value that is not one the enum knows is treated as leaving the quarter
    open, for the same reason the enum lists its answers rather than its
    exceptions.
    """
    if not resolution:
        return False
    try:
        return UnresolvedResolution(resolution).answers_the_quarter
    except ValueError:
        return False
