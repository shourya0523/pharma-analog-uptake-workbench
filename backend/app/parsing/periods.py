"""Canonical period labels for extracted revenue, read from a document's prose.

Models are reliable at reading which *year* column a figure sits in, but not at
naming the reporting period: given a table headed "Three Months Ended June 30,"
they will happily label a value with the press-release date instead. Earnings
releases state their own period in prose ("three months ended June 30, 2024"),
so the period length and quarter are derived from the document and only the year
is taken from the candidate.

This resolves **one** period for a whole document, which is the right answer
only for a value read out of prose. A figure in a table belongs to its own
column, and every prior-year comparative in every filing is a value this would
date wrongly - so for tables it is the fallback, behind the column geometry in
``app/extraction/fingerprint.py``. See docs/research/sec-table-period-context.md.
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


def fiscal_period_end(month: int, day: int | None, year: int | None = None) -> tuple[int, int | None]:
    """The month a period belongs to, given the date it is stated to end on.

    A filer on a 52/53-week calendar closes each quarter on the Saturday or
    Sunday nearest the calendar quarter's end, so its first quarter is stated
    as ending on April 1 or 2, and its year on January 3. Read by the month
    alone, that first quarter becomes the second and that year the next one.
    No fiscal period ends in the first week of a month and means that month,
    so a day that early names the month before - and the year before, when
    the month before is December.
    """
    if day and day <= 7:
        month -= 1
        if month == 0:
            month = 12
            year = year - 1 if year is not None else None
    return month, year


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
# A filing that covers two spans at once names them together: "the three and
# six months ended June 28, 2026". Matching a single word here read only the
# second of them, so a second-quarter exhibit was dated H1 and a third-quarter
# one M9 - the preference for the quarterly framing below could not fire,
# because the quarterly framing was never counted. Every span named by one
# phrase is captured, and they are separated after the match.
# A filing that reports a year names it as "fiscal year ended" or "year
# ended", and rarely as twelve months; without that form a 10-K names no
# period at all and is dated from whichever quarter its text mentions most.
_PERIOD_PHRASE_RE = re.compile(
    r"\b((?:three|six|nine|twelve)(?:\s+and\s+(?:three|six|nine|twelve))*"
    r"\s+months?|(?:fiscal\s+)?years?)\s+ended\b",
    re.I,
)
_SPAN_WORD_RE = re.compile(r"three|six|nine|twelve", re.I)
_MONTH_DAY_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october"
    r"|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"\.?\s*(\d{1,2})?",
    re.IGNORECASE,
)


def _month_near(text: str, end: int) -> tuple[int, int, bool] | None:
    """The month a period heading's end date falls in, where the date ends,
    and whether the date sat in the first week of the month after."""
    window = text[end : end + _MONTH_LOOKAHEAD]
    match = _MONTH_DAY_RE.search(window)
    if not match:
        return None
    month = MONTHS.get(match.group(1).lower())
    if not month:
        return None
    day = int(match.group(2)) if match.group(2) else None
    stated = month
    month, _ = fiscal_period_end(month, day)
    return month, end + match.end(), month != stated


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


# The other way a filing names its period. "Three months ended June 30, 2024" is
# a US convention; outside it, the same span is written "Q2 2024" and the phrase
# above never appears. A filing written that way states the quarter form on
# every schedule and "months ended" nowhere, so it is not datable by the phrase
# at all - which is what `fingerprint` refuses on, and what left the model
# reader guessing the quarter for figures it had read correctly.
#
# A bare four-digit year is required, never "FY2026". A filer that writes its
# year that way usually has a fiscal year that is not the calendar one, and the
# quarter number then says nothing about which months it covers - which is
# exactly the inference below.
_QUARTER_FORMS = (
    re.compile(r"\bQ([1-4])\s*[-/ ]?\s*((?:19|20)\d{2})\b", re.I),
    re.compile(r"\b((?:19|20)\d{2})\s*[-/ ]?\s*Q([1-4])\b", re.I),
    re.compile(r"\b(first|second|third|fourth)\s+quarter\s+(?:of\s+)?"
               r"((?:19|20)\d{2})\b", re.I),
)
_SPAN_FORMS = (
    # (regex, months, month the span ends in)
    (re.compile(r"\bH1\s*[-/ ]?\s*((?:19|20)\d{2})\b", re.I), 6, 6),
    (re.compile(r"\b9M\s*[-/ ]?\s*((?:19|20)\d{2})\b", re.I), 9, 9),
)
_QUARTER_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def _quarter_notation(text: str) -> PeriodContext | None:
    """The document's period from "Q2 2024" notation, when no phrase states one.

    A filing names several periods: the one it reports, the prior-year
    comparative printed beside every figure, and periods it only refers to -
    next year's guidance, an expected approval. Selection is by how often each
    is named, because a document states its own period throughout - title,
    headers, every table - and mentions the others once or twice.

    The year only breaks a tie. Leading with it instead dates a full-year
    release into the next year, since that is where the guidance is. Quarters
    are preferred over half-years, a filing that states a quarter being one
    that reports a quarter.

    What this accepts is a filing naming its comparative more often than its
    own period; that is the case to look at first if a document dates wrongly.
    """
    # Collected by position first, because the forms overlap: in "Q2 2024 Q2
    # 2024" the year-first pattern also matches the "2024 Q2" that spans the
    # two, and counting both inflates whichever period a document happens to
    # repeat adjacently. One mention is one mention wherever it is read from.
    seen: list[tuple[int, int, tuple[int, int, int]]] = []
    for pattern in _QUARTER_FORMS:
        for match in pattern.finditer(text):
            first, second = match.group(1), match.group(2)
            if first.lower() in _QUARTER_WORDS:
                quarter, year = _QUARTER_WORDS[first.lower()], int(second)
            elif first.isdigit() and len(first) == 4:
                year, quarter = int(first), int(second)
            else:
                quarter, year = int(first), int(second)
            seen.append((match.start(), match.end(), (3, quarter * 3, year)))

    counts: Counter[tuple[int, int, int]] = Counter()
    taken_to = -1
    for start, end, key in sorted(seen):
        if start < taken_to:
            continue
        counts[key] += 1
        taken_to = end
    if not counts:
        for pattern, months, month in _SPAN_FORMS:
            for match in pattern.finditer(text):
                counts[(months, month, int(match.group(1)))] += 1
    if not counts:
        return None
    months, month, year = max(counts, key=lambda key: (counts[key], key[2], key[1]))
    return PeriodContext(months=months, month=month, year=year)


def detect_period_context(text: str) -> PeriodContext | None:
    """Infer the document's own reporting period from the way it names one."""
    text = text or ""
    counts: Counter[tuple[int, int, int]] = Counter()
    for match in _PERIOD_PHRASE_RE.finditer(text):
        spans = [
            MONTH_WORDS[word.lower()]
            for word in _SPAN_WORD_RE.findall(match.group(1))
            if word.lower() in MONTH_WORDS
        ] or ([12] if "year" in match.group(1).lower() else [])
        found = _month_near(text, match.end())
        if not spans or not found:
            continue
        month, after_month, rolled_back = found
        year = _year_near(text, after_month)
        if not year:
            continue
        if rolled_back and month == 12:
            year -= 1
        # Both spans end on the same date and are equally stated; which one is
        # the document's own period is decided below, not here.
        for months in spans:
            counts[(months, month, year)] += 1
    if not counts:
        # No filing states its period both ways, so this is a different
        # convention rather than a second opinion, and it only ever runs where
        # there was no answer at all.
        return _quarter_notation(text)
    # The span the document reports is the one it names throughout. A
    # quarterly release names its quarter and its year-to-date span about
    # equally, and is read as the quarter; an annual report names the year on
    # every statement and a quarter only in passing, and is read as the year.
    # So the quarterly framing is preferred unless another span is named more
    # than twice as often.
    by_span: Counter[int] = Counter()
    for (months, _month, _year), n in counts.items():
        by_span[months] += n
    framing = 3 if by_span[3] * 2 >= max(by_span.values()) else max(by_span, key=by_span.get)
    # Then the latest year - never the most frequently repeated one. A
    # comparative year is always earlier than the year being reported, and it
    # is often named more often than the reporting year: a Q4 2005 release
    # mentions "three months ended December 31, 2004" five times in its
    # footnotes against four for 2005, which is how the document came to be
    # dated a year early.
    best = max((key for key in counts if key[0] == framing), key=lambda key: key[2])
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
        if month:
            month, year = fiscal_period_end(month, int(spelled.group(3)) if spelled.group(3) else None, year)
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
