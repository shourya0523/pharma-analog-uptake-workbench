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
    re.IGNORECASE,
)
_YEAR_QUARTER_RE = re.compile(r"^(?:FY)?(\d{4})\s*[-/\s]?\s*Q([1-4])$", re.IGNORECASE)
_QUARTER_YEAR_RE = re.compile(r"^Q([1-4])\s*[-/\s]?\s*(?:FY)?(\d{4})$", re.IGNORECASE)
_YEAR_ONLY_RE = re.compile(r"^(?:FY)?(\d{4})$", re.IGNORECASE)
# Round-trip the non-quarterly labels this module emits
_YEAR_PART_RE = re.compile(r"^(?:FY)?(\d{4})(H1|H2|M9)$", re.IGNORECASE)
_ANY_YEAR_RE = re.compile(r"\b(19|20)(\d{2})\b")

QUARTERLY_TYPES = {"quarterly"}


def quarter_of_month(month: int) -> int:
    return (month - 1) // 3 + 1


MONTH_NAMES = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}
LENGTH_WORDS = {3: "three", 6: "six", 9: "nine", 12: "twelve"}


@dataclass(frozen=True)
class PeriodContext:
    """The reporting period a document states for itself."""

    months: int
    month: int
    year: int

    @property
    def quarter(self) -> int:
        return quarter_of_month(self.month)

    @property
    def comparative_year(self) -> int:
        """The prior-year column an earnings table shows beside the current period."""
        return self.year - 1

    def describe(self) -> str:
        length = LENGTH_WORDS.get(self.months, str(self.months))
        return f"{length} months ended {MONTH_NAMES[self.month]} {self.year}"


# How far past "... Ended <Month> <day>," to look for the year when the phrase
# does not carry one. Issuers routinely print the heading and the year columns
# on separate lines - "Three Months Ended | June 30, | Six Months Ended |
# June 30, | 2007 | 2006" - and the years follow within a few short tokens.
_YEAR_LOOKAHEAD = 120
# The month sits closer to its heading than the year columns do.
_MONTH_LOOKAHEAD = 40
_NEXT_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


# The phrase alone. Issuers stack the headings and print the month and the year
# columns below them - "Three Months Ended | Twelve Months Ended | December 31,
# | December 31, | 2012 | 2011" - so neither the month nor the year can be
# required to sit beside the words.
_PERIOD_PHRASE_RE = re.compile(r"\b(three|six|nine|twelve)\s+months?\s+ended\b", re.I)
_MONTH_DAY_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october"
    r"|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"\.?\s*(\d{1,2})?",
    re.IGNORECASE,
)


def _month_near(text: str, end: int) -> tuple[int, int] | None:
    """The first month named just after a period heading, and where it ends."""
    window = text[end : end + _MONTH_LOOKAHEAD]
    match = _MONTH_DAY_RE.search(window)
    if not match:
        return None
    month = MONTHS.get(match.group(1).lower())
    return (month, end + match.end()) if month else None


def _year_near(text: str, end: int) -> int | None:
    """The first four-digit year printed just after a period heading.

    Requiring the year to sit immediately after the month is what made a split
    heading unreadable: the three-month phrase captured no year at all, was
    discarded, and the document was then dated by whichever other framing
    happened to have a year beside it - usually the year-to-date one.
    """
    window = text[end : end + _YEAR_LOOKAHEAD]
    match = _NEXT_YEAR_RE.search(window)
    return int(match.group(1)) if match else None


def detect_period_context(text: str) -> PeriodContext | None:
    """Infer the document's own reporting period from its "months ended" prose."""
    text = text or ""
    counts: Counter[tuple[int, int, int]] = Counter()
    for match in _PERIOD_PHRASE_RE.finditer(text):
        months = MONTH_WORDS.get(match.group(1).lower())
        found = _month_near(text, match.end())
        if not months or not found:
            continue
        month, after_month = found
        year = _year_near(text, after_month)
        if not year:
            continue
        counts[(months, month, year)] += 1
    if not counts:
        return None
    # Prefer the quarterly framing, then the latest year - never the most
    # frequently repeated one. A comparative year is always earlier than the
    # year being reported, and it is often named more often than the reporting
    # year: a Q4 2005 release mentions "three months ended December 31, 2004"
    # five times in its footnotes against four for 2005, which is how the
    # document came to be dated a year early.
    best = max(counts, key=lambda key: (key[0] == 3, key[2]))
    months, month, year = best
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
    match = _YEAR_PART_RE.match(compact)
    if match:
        return f"{match.group(1)}{match.group(2).upper()}"

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
