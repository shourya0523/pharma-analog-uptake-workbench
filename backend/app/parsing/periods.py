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
from datetime import date, timedelta

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
_SPAN_PHRASE = (
    r"(?:three|six|nine|twelve)(?:\s+and\s+(?:three|six|nine|twelve))*"
    r"\s+months?|(?:fiscal\s+)?years?"
)
_PERIOD_PHRASE_RE = re.compile(rf"\b({_SPAN_PHRASE})\s+ended\b", re.IGNORECASE)
# The same phrase as a footnote writes it. A note says "the quarters ended
# March 31, 2026 and June 30, 2026" where the statements above it say "the
# three months ended": the span word is the only difference, and one heading
# can carry several end dates. Reading the document's own period does not use
# this form - a filing states its period in the statements, not in a note.
_NAMED_PHRASE_RE = re.compile(rf"\b({_SPAN_PHRASE}|quarters?)\s+ended\b", re.IGNORECASE)
_SPAN_WORD_RE = re.compile(r"three|six|nine|twelve", re.IGNORECASE)
_MONTH_DAY_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october"
    r"|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"\.?\s*(\d{1,2})?",
    re.IGNORECASE,
)


def _month_of(match: re.Match[str] | None, offset: int) -> tuple[int, int, bool] | None:
    """The month a matched end date falls in, where the date ends in the text,
    and whether the date sat in the first week of the month after."""
    if not match:
        return None
    month = MONTHS.get(match.group(1).lower())
    if not month:
        return None
    day = int(match.group(2)) if match.group(2) else None
    stated = month
    month, _ = fiscal_period_end(month, day)
    return month, offset + match.end(), month != stated


def _month_near(text: str, end: int) -> tuple[int, int, bool] | None:
    """The end date a period heading states, looked for just after the heading."""
    window = text[end : end + _MONTH_LOOKAHEAD]
    return _month_of(_MONTH_DAY_RE.search(window), end)


def _month_at(text: str, position: int) -> tuple[int, int, bool] | None:
    """The end date beginning exactly at ``position``, for a second date the
    same heading states: "the quarters ended March 31 and June 30, 2026"."""
    return _month_of(_MONTH_DAY_RE.match(text, position), 0)


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
    re.compile(r"\bQ([1-4])\s*[-/ ]?\s*((?:19|20)\d{2})\b", re.IGNORECASE),
    re.compile(r"\b((?:19|20)\d{2})\s*[-/ ]?\s*Q([1-4])\b", re.IGNORECASE),
    re.compile(r"\b(first|second|third|fourth)\s+quarter\s+(?:of\s+)?"
               r"((?:19|20)\d{2})\b", re.IGNORECASE),
    # The quarter number first: "2Q 2024", "2Q24", "1Q'26". A two-digit year
    # is this century's; no filing read this way predates it.
    re.compile(r"\b([1-4])Q\s*'?\s*((?:19|20)\d{2}|\d{2})(?!\d)", re.IGNORECASE),
    re.compile(r"\bQ([1-4])\s*'(\d{2})(?!\d)", re.IGNORECASE),
)


def _year_of_form(digits: str) -> int:
    return int(digits) if len(digits) == 4 else 2000 + int(digits)
_SPAN_FORMS = (
    # (regex, months, month the span ends in)
    (re.compile(r"\bH1\s*[-/ ]?\s*((?:19|20)\d{2})\b", re.IGNORECASE), 6, 6),
    (re.compile(r"\b9M\s*[-/ ]?\s*((?:19|20)\d{2})\b", re.IGNORECASE), 9, 9),
)
_QUARTER_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def _quarter_form_hits(text: str) -> list[tuple[int, int, tuple[int, int, int]]]:
    """Every "Q2 2024" notation in the text, as (start, end, (3, month, year)).

    Collected by position, because the forms overlap: in "Q2 2024 Q2 2024" the
    year-first pattern also matches the "2024 Q2" that spans the two, and
    counting both inflates whichever period a text happens to repeat
    adjacently. One mention is one mention wherever it is read from.
    """
    seen: list[tuple[int, int, tuple[int, int, int]]] = []
    for pattern in _QUARTER_FORMS:
        for match in pattern.finditer(text):
            first, second = match.group(1), match.group(2)
            if first.lower() in _QUARTER_WORDS:
                quarter, year = _QUARTER_WORDS[first.lower()], int(second)
            elif first.isdigit() and len(first) == 4:
                year, quarter = int(first), int(second)
            else:
                quarter, year = int(first), _year_of_form(second)
            seen.append((match.start(), match.end(), (3, quarter * 3, year)))
    return seen


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
    seen = _quarter_form_hits(text)

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
    # The period a document reports is named throughout it; so is the
    # comparative printed beside every figure, and often more times. Choosing
    # by count alone dated a release by its comparative. So among the periods
    # named throughout - at least half as often as the most-named one - the
    # latest is the document's own, as the phrase path already reasons; a
    # period named once or twice, next year's guidance, is not among them.
    most = max(counts.values())
    throughout = [key for key, n in counts.items() if n * 2 >= most]
    months, month, year = max(throughout, key=lambda key: (key[2], key[1]))
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
    # Then the latest period among those named *throughout* the document, on the
    # same reasoning as `_quarter_notation`: the comparative is printed beside
    # every figure and so is named about as often as the reporting period, while
    # a debt maturity or a milestone forecast - "the twelve months ended
    # December 31, 2040" in a filing that says 2022 on every statement - is
    # named once, and is not the period the document reports.
    at_framing = {key: n for key, n in counts.items() if key[0] == framing}
    most = max(at_framing.values())
    throughout = [key for key, n in at_framing.items() if n * 2 >= most]
    months, month, year = max(throughout, key=lambda key: (key[2], key[1]))
    return PeriodContext(months=months, month=month, year=year)


# The spans a reporting period is stated in, and what each one is called. A
# filer also tags facts over spans that are not a reporting period - a month,
# a week, the days between two events - and those name no quarter of anything.
MONTHS_TO_PERIOD_TYPE: dict[int, str] = {
    3: "quarterly",
    6: "six_month",
    9: "nine_month",
    12: "annual",
}

# The same map read the other way, for a reader that holds a period type and
# wants the span. Derived, so a span the grammar learns to read is covered
# here without being written down twice.
PERIOD_TYPE_TO_MONTHS: dict[str, int] = {
    period_type: months for months, period_type in MONTHS_TO_PERIOD_TYPE.items()
}


def period_label(year: int, months: int, quarter: int) -> str:
    """The canonical key for a period of ``months`` ending in ``quarter``.

    One producer for every reader that has to name a period, so a figure and a
    note about it are compared in one namespace: 2024Q2, 2024H1, 2024M9, 2024.
    """
    if months == 3:
        return f"{year}Q{quarter}"
    if months == 6:
        return f"{year}H1"
    if months == 9:
        return f"{year}M9"
    return str(year)


# Which span each suffix `period_label` writes stands for, read off the
# producer rather than restated: a span the label learns to spell is spelled
# here by the same call. The year is a placeholder - only the suffix is kept.
_SUFFIX_MONTHS: dict[str, int] = {
    period_label(0, months, quarter)[1:]: months
    for months in MONTHS_TO_PERIOD_TYPE
    for quarter in range(1, 5)
}


def period_months(key: str) -> int | None:
    """The span in months a canonical period key names, or None if it is not one.

    `period_label` writes the span into the key, so the key answers this on its
    own: `2024Q2` is three months, `2024H1` six, `2024` twelve. It is what
    `period_span` has to be told, which is why a reader holding only a key can
    now reach a span without parsing the key a second way.
    """
    match = re.fullmatch(r"\d{4}(.*)", key or "")
    return _SUFFIX_MONTHS.get(match.group(1)) if match else None


@dataclass(frozen=True)
class NamedPeriod:
    """A period a piece of text names.

    ``position`` is where in the text it is named, ``months`` the span the
    phrase states, and ``key`` the canonical label where the phrase states a
    date as well. A phrase can state a span and no date - "for the six months
    ended" with the year in a column heading - and then ``key`` is None.
    """

    position: int
    months: int
    key: str | None


# What may stand between two end dates the same heading covers: a comma, the
# first date's own year, and the word that joins them.
_DATE_JOIN_RE = re.compile(
    r"[\s,]*(?:(?:19|20)\d{2})?[\s,]*\b(?:and|through|to)\b[\s,]*", re.IGNORECASE
)


def _spans_of(phrase: str) -> list[int]:
    """The span in months each word of a period heading names."""
    spans = [
        MONTH_WORDS[word.lower()]
        for word in _SPAN_WORD_RE.findall(phrase)
        if word.lower() in MONTH_WORDS
    ]
    if spans:
        return spans
    lowered = phrase.lower()
    if "quarter" in lowered:
        return [3]
    if "year" in lowered:
        return [12]
    return []


def _dates_after(text: str, end: int) -> list[tuple[int, int]]:
    """(month, year) for each end date one period heading states.

    A heading covers several dates when the text joins them - "the quarters
    ended March 31, 2026 and June 30, 2026" states two periods, and reading
    only the first loses the second.
    """
    dates: list[tuple[int, int]] = []
    found = _month_near(text, end)
    while found is not None:
        month, after_day, rolled_back = found
        year = _year_near(text, after_day)
        if year is None:
            break
        if rolled_back and month == 12:
            year -= 1
        dates.append((month, year))
        join = _DATE_JOIN_RE.match(text, after_day)
        found = _month_at(text, join.end()) if join else None
    return dates


def periods_named(text: str) -> list[NamedPeriod]:
    """Every period a piece of text names, in reading order.

    Both the ways a filer names one: the heading a statement carries ("the
    six months ended June 30, 2023", "the quarters ended March 31, 2026 and
    June 30, 2026") and the notation a note or a release uses ("Q1 2026",
    "the second quarter of 2025").
    """
    named: list[NamedPeriod] = []
    for match in _NAMED_PHRASE_RE.finditer(text or ""):
        spans = _spans_of(match.group(1))
        if not spans:
            continue
        dates = _dates_after(text, match.end())
        for months in spans:
            if not dates:
                named.append(NamedPeriod(match.start(), months, None))
                continue
            for month, year in dates:
                named.append(
                    NamedPeriod(
                        match.start(), months, period_label(year, months, quarter_of_month(month))
                    )
                )
    for start, _end, (months, month, year) in _quarter_form_hits(text or ""):
        named.append(
            NamedPeriod(start, months, period_label(year, months, quarter_of_month(month)))
        )
    return sorted(named, key=lambda period: period.position)


# The year that completes a date, printed right after the day.
_DATE_YEAR_RE = re.compile(r"[\s,]*((?:19|20)\d{2})\b")


def dates_named(text: str) -> list[date]:
    """Every full calendar date the text writes out, in reading order.

    A date is a month, a day and a year written together - "March 13, 2024".
    A month and a year with nothing between them names a month rather than a
    day in it, and is not one of these.
    """
    found: list[date] = []
    for match in _MONTH_DAY_RE.finditer(text or ""):
        month = MONTHS.get(match.group(1).lower())
        day = match.group(2)
        if not month or not day:
            continue
        year = _DATE_YEAR_RE.match(text, match.end())
        if not year:
            continue
        try:
            found.append(date(int(year.group(1)), month, int(day)))
        except ValueError:
            continue
    return found


def period_span(key: str, months: int) -> tuple[date, date] | None:
    """The first and last day the period ``key`` covers over ``months``.

    The key names the year and, for a quarter, which quarter of it; a key for
    a longer period carries no month at all, so the span is what says where in
    the year it ends - which is the same pairing `period_key` reads and
    `period_label` writes. None where the key is not one of those.
    """
    match = re.fullmatch(r"(\d{4})(?:Q([1-4])|H1|M9)?", key or "")
    if not match or months not in MONTHS_TO_PERIOD_TYPE:
        return None
    year = int(match.group(1))
    end_month = int(match.group(2)) * 3 if match.group(2) else months
    if end_month < months:
        return None
    end = quarter_end(year, quarter_of_month(end_month))
    return date(year, end_month - months + 1, 1), end


def period_key(label: str, months: int) -> str | None:
    """The canonical key for a figure of ``months`` span in a column labelled
    ``label``, or None where the label states no year.

    A quarterly column names its quarter; a longer column is labelled by its
    year alone, and the span is what says which part of the year it covers, so
    both are needed to name the period a figure is for.
    """
    canonical = normalize_period(label)
    if not canonical:
        return None
    match = _YEAR_QUARTER_RE.match(canonical)
    if match:
        return period_label(int(match.group(1)), months, int(match.group(2)))
    match = _YEAR_PART_RE.match(canonical) or _YEAR_ONLY_RE.match(canonical)
    if match:
        return period_label(int(match.group(1)), months, 0)
    return None


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
            return period_label(year, months, quarter)

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
            return period_label(year, context.months, context.quarter)
        return str(year)
    return None


# How long after a quarter ends a filer reports it: an earnings release or a
# 10-Q within about two months, the fourth quarter with the annual report.
# And the soonest any of them comes.
_REPORT_LAG_DAYS = {1: 60, 2: 60, 3: 60, 4: 90}
_REPORT_EARLIEST_DAYS = 20


def quarter_end(year: int, quarter: int) -> date:
    """The last day of a calendar quarter."""
    month = quarter * 3
    first_of_next = date(year + (month == 12), month % 12 + 1, 1)
    return first_of_next - timedelta(days=1)


def quarters_reported_in(since: date | None, until: date | None) -> list[str]:
    """The quarters whose results a filer would report inside a window.

    A window bounds retrieval by filing date, so the quarters it covers are
    the ones whose reports fall in it: a quarter is in if a report at its
    usual lag would land after the window opens and one at the earliest
    would land before it closes. No window covers nothing.
    """
    if since is None and until is None:
        return []
    start = (since or until) - timedelta(days=max(_REPORT_LAG_DAYS.values()) + 10)
    stop = until or (since + timedelta(days=366))
    year, quarter = start.year, quarter_of_month(start.month)
    found: list[str] = []
    while True:
        end = quarter_end(year, quarter)
        if end > stop:
            break
        reported_by = end + timedelta(days=_REPORT_LAG_DAYS[quarter])
        reported_from = end + timedelta(days=_REPORT_EARLIEST_DAYS)
        if (since is None or reported_by >= since) and (until is None or reported_from <= until):
            found.append(f"{year}Q{quarter}")
        quarter += 1
        if quarter > 4:
            quarter, year = 1, year + 1
    return found
