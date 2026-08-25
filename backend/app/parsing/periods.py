"""Canonical period labels for extracted revenue.

Models are reliable at reading which *year* column a figure sits in, but not at
naming the reporting period: given a table headed "Three Months Ended June 30,"
they will happily label a value with the press-release date instead. Earnings
releases always state their own period in prose ("three months ended June 30,
2024"), so the period length and quarter are derived from the document and only
the year is taken from the candidate.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
MONTH_WORDS = {"three": 3, "six": 6, "nine": 9, "twelve": 12}

_MONTHS_ENDED_RE = re.compile(
    r"\b(three|six|nine|twelve)\s+months?\s+ended\s+"
    r"([A-Za-z]{3,9})\.?\s*(\d{1,2})?,?\s*(\d{4})?",
    re.I,
)
_YEAR_QUARTER_RE = re.compile(r"^(?:FY)?(\d{4})\s*[-/\s]?\s*Q([1-4])$", re.I)
_QUARTER_YEAR_RE = re.compile(r"^Q([1-4])\s*[-/\s]?\s*(?:FY)?(\d{4})$", re.I)
_YEAR_ONLY_RE = re.compile(r"^(?:FY)?(\d{4})$", re.I)
_ANY_YEAR_RE = re.compile(r"\b(19|20)(\d{2})\b")

QUARTERLY_TYPES = {"quarterly"}


def quarter_of_month(month: int) -> int:
    return (month - 1) // 3 + 1


@dataclass(frozen=True)
class PeriodContext:
    """The reporting period a document states for itself."""

    months: int
    month: int
    year: int

    @property
    def quarter(self) -> int:
        return quarter_of_month(self.month)


def detect_period_context(text: str) -> PeriodContext | None:
    """Infer the document's own reporting period from its "months ended" prose."""
    counts: Counter[tuple[int, int, int]] = Counter()
    for length_word, month_word, _day, year in _MONTHS_ENDED_RE.findall(text or ""):
        months = MONTH_WORDS.get(length_word.lower())
        month = MONTHS.get(month_word.lower())
        if not months or not month or not year:
            continue
        counts[(months, month, int(year))] += 1
    if not counts:
        return None
    # Prefer the quarterly framing, then the most frequently repeated statement
    best = max(counts.items(), key=lambda kv: (kv[0][0] == 3, kv[1]))
    months, month, year = best[0]
    return PeriodContext(months=months, month=month, year=year)


def _label(year: int, months: int, quarter: int) -> str:
    if months == 3:
        return f"{year}Q{quarter}"
    if months == 6:
        return f"{year}H1"
    if months == 9:
        return f"{year}M9"
    return str(year)


def normalize_period(
    raw: object,
    *,
    period_type: str | None = None,
    context: PeriodContext | None = None,
) -> str | None:
    """Return a canonical period label (``2024Q2``, ``2024H1``, ``2024``) or None.

    The year always comes from ``raw`` when present, because that reflects the
    column the figure was read from. The quarter prefers ``context`` since the
    document states its own period end.
    """
    label = str(raw or "").strip()
    if not label or label.lower() == "unknown":
        return None

    compact = re.sub(r"\s+", "", label)
    match = _YEAR_QUARTER_RE.match(compact)
    if match:
        return f"{match.group(1)}Q{match.group(2)}"
    match = _QUARTER_YEAR_RE.match(compact)
    if match:
        return f"{match.group(2)}Q{match.group(1)}"

    spelled = _MONTHS_ENDED_RE.search(label)
    if spelled:
        months = MONTH_WORDS.get(spelled.group(1).lower(), 3)
        month = MONTHS.get(spelled.group(2).lower())
        year_text = spelled.group(4)
        year = int(year_text) if year_text else (context.year if context else None)
        if year:
            # Trust the document's period end over a date the model may have taken
            # from the release headline; fall back to the month it reported.
            if context and context.months == months:
                quarter = context.quarter
            elif month:
                quarter = quarter_of_month(month)
            else:
                return None
            return _label(year, months, quarter)

    match = _YEAR_ONLY_RE.match(compact)
    if match:
        year = int(match.group(1))
        if (period_type or "").lower() in QUARTERLY_TYPES and context:
            return f"{year}Q{context.quarter}"
        return str(year)

    year_match = _ANY_YEAR_RE.search(label)
    if year_match:
        year = int(year_match.group(0))
        if (period_type or "").lower() in QUARTERLY_TYPES and context:
            return f"{year}Q{context.quarter}"
        if context:
            return _label(year, context.months, context.quarter)
        return str(year)
    return None
