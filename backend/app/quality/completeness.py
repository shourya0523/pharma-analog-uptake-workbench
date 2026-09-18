from __future__ import annotations

import re
from typing import NamedTuple

from sqlalchemy.orm import Session

from app.connectors.sources import parse_filing_date
from app.db.models import DatapointORM, DrugJobORM, UnresolvedQuarterORM
from app.domain.models import (
    PUBLISHED_STATUS_VALUES,
    PeriodType,
    UnresolvedResolution,
)
from app.parsing.periods import normalize_period, quarters_reported_in

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


def quarter_labels(periods: object) -> set[str]:
    """The canonical single-quarter labels among the given period strings.

    A set, because two rows naming one quarter are two readings of one
    quarter and not two quarters, and the ratio counts quarters.
    """
    return {
        label
        for period in periods or ()
        if names_a_quarter(period) and (label := normalize_period(period))
    }


def quarters_the_run_asked_for(job: DrugJobORM) -> set[str]:
    """The quarters the job's own run set out to cover.

    A window bounds retrieval by filing date, and the quarters inside it are
    the ones the project's own parser derives from that window. That is the
    denominator coverage is a fraction of. A run that declared no window
    asked for nothing in particular, and the denominator then comes from
    what the job turned out to hold.
    """
    run = getattr(job, "run", None)
    options = (getattr(run, "options_json", None) or {}) if run is not None else {}
    since = parse_filing_date(options.get("earnings_since"))
    until = parse_filing_date(options.get("earnings_until"))
    return set(quarters_reported_in(since, until))


class Completeness(NamedTuple):
    """What the recount found, so a caller can report it without recounting."""

    pct: float
    quarters: int
    gaps: int


def refresh_completeness(db: Session, job: DrugJobORM) -> Completeness:
    """Recount what a job holds, and set the two fields derived from it.

    Coverage is the distinct quarters of the run's window carrying a figure
    the pipeline stands behind, over that window. Each half of that sentence
    corrects a count that read the other way. Counting rows rather than
    quarters let one quarter read as fourteen; counting only the gaps the
    pipeline had itself recorded let a product whose gaps it never noticed
    read as complete, which is the reading a blank series got.

    The denominator is every quarter the run asked about, every quarter it
    answered and every gap it recorded. A quarter is in it for any of those
    three reasons, so a figure the run found outside its own window is not
    thrown away, and a gap the pipeline noticed is a quarter it knows it did
    not answer wherever that quarter sits. The two numbers a reader sees side
    by side - the quarters held and the percentage - then come from one set.
    A run that declared no window asked nothing in particular, and what it
    holds and misses is the whole question.

    Both numbers are functions of rows that review changes: entering a value
    for a gap adds a quarter and closes the gap it came from, and rejecting a
    figure takes a quarter away. Computed once at the end of the run, they
    described the run rather than the job, and a reviewer could resolve every
    gap without either moving.

    Flushes first because the session does not autoflush: a caller that has
    added rows and not committed is asking about those rows too.
    """
    db.flush()
    published = (
        db.query(DatapointORM.period)
        .filter(
            DatapointORM.job_id == job.id,
            DatapointORM.period_type == PeriodType.QUARTERLY.value,
            DatapointORM.validation_status.in_(PUBLISHED_STATUS_VALUES),
        )
        .all()
    )
    open_gaps = [
        row
        for row in db.query(UnresolvedQuarterORM).filter_by(job_id=job.id).all()
        if not _is_answered(row.resolution)
    ]
    held = quarter_labels(period for (period,) in published)
    missing = quarter_labels(row.period for row in open_gaps)
    expected = quarters_the_run_asked_for(job) | held | missing
    job.unresolved_count = len(open_gaps)
    job.completeness_pct = round(100 * len(held) / len(expected), 1) if expected else 0.0
    return Completeness(job.completeness_pct, len(held), len(missing))


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
