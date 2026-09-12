"""Stage 1: fingerprint a source table before reading any numbers out of it.

A fingerprint is what the table declares about itself: the unit its numbers are
stated in, the currency, and which column holds which reporting period. Reading
values without first establishing those three things is where extraction goes
wrong, and the failure is silent - the numbers still look like plausible
revenue, they are just off by 1000x, in the wrong currency, or attributed to the
wrong period.

Two real defects motivated this module, both found by auditing the gold dataset:

* An issuer's earnings exhibits switch from whole-dollar thousands ("121,718")
  to one-decimal millions ("102.2") partway through a year. Code that assumed a
  unit from the filing date divided those quarters by 1000 and produced a
  revenue collapse that never happened.
* A prior-year comparison schedule lists a year as Q2/Q3/Q4 + FY, not Q1-Q4.
  Code that assumed a fixed number of quarter columns per block consumed the
  full-year total as if it were a quarter.

Both are prevented here by reading the table's own declarations instead of
inferring them from context, and by refusing to guess when the declaration is
absent - an unfingerprintable table yields no values rather than wrong ones.

Where the period comes from, in order of preference:

1. **The columns.** Given the table as a rectangle, a column's period is what
   the headings covering it say - the length phrase, the month, the year - so a
   heading split over three rows reads as one statement and a prior-year column
   is distinguishable from a current one. This is what the table states.
2. **The ragged rows.** Without a rectangle, the period phrases and the year row
   are read as two ordered lists and one is divided into the other. That is an
   inference, and it is wrong when a filing prints an uneven number of columns
   per period, so it reports ``unmapped_columns`` rather than guessing.

Geometry is preferred but not trusted blindly, because a filer can span its
headings and not span its body, and the rectangle then describes a layout the
numbers are not in. Two checks catch that and send the table back to (2): a
period may not cover a column the body puts a row label in, and a row putting
two of its figures under one period condemns the reading for the whole table -
the rows that did not trip it were read against the same headings and are right
only by luck.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.parsing.periods import MONTH_WORDS, MONTHS, fiscal_period_end, quarter_of_month

# A filing names a column's period in one of two ways. Either it anchors the
# period to a date - "Three months ended June 30," - or it names the period
# itself: "FOURTH QUARTER", "NINE MONTHS", "Full Year". Both forms are read,
# the anchored one first, because it says more.
#
# The unanchored form carries an assumption the anchored one does not: it names
# the issuer's own fourth quarter, and calling that the calendar year's fourth
# quarter is right only for a filer whose year ends in December. It is used
# because for such filings there is nothing else - a sales schedule prints
# "FOURTH QUARTER" over "TWELVE MONTHS" and names no month anywhere on the page
# - and a reader that refuses the unanchored form reads those schedules as
# stating no period at all.
_PERIOD_PHRASE_RE = re.compile(
    r"\b(three|six|nine|twelve|year)s?\s*(?:months?\s*)?ended\s+([A-Za-z]{3,9})\.?\s*(\d{1,2})?",
    re.IGNORECASE,
)
_ORDINAL_QUARTERS = {"first": 3, "second": 6, "third": 9, "fourth": 12}
# "months" not followed by "ended", so a heading broken before its date still
# carries forward to the row holding the date instead of being read here.
_NAMED_PERIOD_RE = re.compile(
    r"\b(first|second|third|fourth)\s+quarter\b"
    r"|\b(three|six|nine|twelve)\s+months\b(?!\s*ended)"
    r"|\b(?:full|fiscal)\s+year\b",
    re.IGNORECASE,
)
# The same heading with its date on the next line: "Three Months Ended" alone.
_DANGLING_PHRASE_RE = re.compile(
    r"\b(?:three|six|nine|twelve|year)s?\s*(?:months?\s*)?ended\s*[,:]?\s*$",
    re.IGNORECASE,
)
_FIGURE_RE = re.compile(r"\(?-?[\d,]+(?:\.\d+)?\)?%?")
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_PERCENT_RE = re.compile(r"%")

# A currency may sit between "in" and the magnitude word ("in CHF millions",
# "$ in millions", "in thousands of Swiss francs"), so unit detection has to
# read past it rather than require the two words to be adjacent.
#
# The ISO-code alternative is a whole uppercase word, and says so twice over: it
# opts out of the pattern's IGNORECASE, because case-folded ``[A-Z]{3}`` matches
# any three-letter word at all, and it carries its own boundaries, because three
# unanchored letters match inside a longer one. Without the first, "a revenue run
# rate of one billion dollars" reads as "of <currency> billion"; without the
# second, an all-caps dateline "...FINANCIAL RESULTS THOUSAND OAKS, Calif."
# reads as "<currency> thousand". Both scale a whole table by the mistake.
_CURRENCY_TOKEN = (
    r"(?:\b(?-i:[A-Z]{3})\b|US\$|U\.S\.|\$|£|€|dollars?|swiss\s+francs?|francs?|"
    r"pounds?(?:\s+sterling)?|euros?|yen)"
)

# Quantities. A magnitude word does two unrelated jobs in a filing: "(dollars in
# thousands)" declares the scale of the numbers printed below it, while "one
# billion dollars", "hundreds of millions of dollars" and "tens of thousands of
# patients" are amounts, which say nothing about any table. What separates them
# is what comes immediately before - an amount is counted, so a quantity word
# leads it. Reading an amount as a declaration is the same silent 1000x defect
# this module exists to prevent, just arriving through the prose instead of the
# header.
_QUANTITY_BEFORE_RE = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"couple|few|several|many|dozens?|scores?|"
    r"tens|hundreds?|thousands?|millions?|billions?)\s*\Z",
    re.IGNORECASE,
)


def _unit_pattern(word: str) -> re.Pattern[str]:
    """Match a magnitude declaration in any of the forms issuers print it.

    The trailing "millions of <currency>" form is what issuers who head a column
    "Millions of CHF" print. Requiring the currency keeps it a declaration about
    money: "thousands of patients" is a count, not a scale.
    """
    return re.compile(
        rf"\b(?:in|of)\s+(?:{_CURRENCY_TOKEN}\s+){{0,2}}{word}\b"
        rf"|{_CURRENCY_TOKEN}\s*{word}\b"
        rf"|\b{word}\s+of\s+{_CURRENCY_TOKEN}\b",
        re.IGNORECASE,
    )


def _declares(scope: str, pattern: re.Pattern[str]) -> bool:
    """True when this scope declares that magnitude rather than counting in it."""
    return any(
        not _QUANTITY_BEFORE_RE.search(scope[: match.start()])
        for match in pattern.finditer(scope)
    )


# Most specific first: "thousands" and "billions" before "millions", so a header
# reading "in thousands" is never matched by a stray later "million".
# The abbreviations, which a schedule uses where a press release writes the
# word: "($MM)", "$bn". Held out, these appear nowhere - the four issuers write
# "millions" in full - so unlike the rest of this module's vocabulary they rest
# on the convention being unambiguous rather than on a corpus, and they are
# written narrowly for that reason: MM only beside a currency sign or "in", and
# never a bare M, which means thousands as often as millions.
_ABBREVIATED_UNITS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("billions", re.compile(r"(?:[$€£¥]\s*|\bin\s+)(?:BN|B)\b")),
    ("millions", re.compile(r"(?:[$€£¥]\s*|\bin\s+)MM\b")),
)

_UNIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("billions", _unit_pattern(r"billions?")),
    ("thousands", _unit_pattern(r"thousands?")),
    ("millions", _unit_pattern(r"millions?")),
    *_ABBREVIATED_UNITS,
)

# Scale to reach the canonical unit (millions).
UNIT_SCALE_TO_MILLIONS: dict[str, float] = {
    "billions": 1000.0,
    "millions": 1.0,
    "thousands": 0.001,
    "units": 0.000001,
}

_CURRENCY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("CHF", re.compile(r"\bCHF\b|\bSwiss\s+francs?\b", re.I)),
    ("GBP", re.compile(r"£|\bGBP\b|\bpounds?\s+sterling\b|\bsterling\b", re.I)),
    ("EUR", re.compile(r"€|\bEUR\b|\beuros?\b", re.I)),
    ("JPY", re.compile(r"¥|\bJPY\b|\byen\b", re.I)),
    ("USD", re.compile(r"\bUS\$|\bUSD\b|\bU\.S\.\s+dollars?\b|\bdollars?\b|\$", re.I)),
)

MONTHS_TO_PERIOD_TYPE: dict[int, str] = {
    3: "quarterly",
    6: "six_month",
    9: "nine_month",
    12: "annual",
}


@dataclass(frozen=True)
class PeriodBlock:
    """One reporting period occupying one value column of a product row."""

    months: int
    end_month: int
    year: int
    value_index: int

    @property
    def period_type(self) -> str:
        return MONTHS_TO_PERIOD_TYPE.get(self.months, "unknown")

    @property
    def period(self) -> str:
        """Canonical period label: 2024Q2 for a quarter, 2024 for anything longer."""
        if self.months == 3:
            return f"{self.year}Q{quarter_of_month(self.end_month)}"
        return str(self.year)


@dataclass(frozen=True)
class TableFingerprint:
    """What a table declares about its own numbers, before any are read."""

    unit_label: str
    currency: str
    blocks: tuple[PeriodBlock, ...]
    has_change_columns: bool
    unit_declared: bool
    currency_declared: bool
    notes: tuple[str, ...] = field(default_factory=tuple)
    by_column: bool = False
    """Whether ``value_index`` is a grid column rather than a position in a row.

    Read off a rectangle, a block's index is the column the heading covers, and
    a value belongs to it because it sits in that column. Read off ragged rows,
    the index is the n-th number in the row and the mapping is an inference.
    """

    @property
    def unit_scale_to_millions(self) -> float:
        return UNIT_SCALE_TO_MILLIONS.get(self.unit_label, 1.0)

    @property
    def usable(self) -> bool:
        """A fingerprint is usable only if the table said what its numbers mean.

        Refusing to extract from an undeclared table is deliberate: a missing
        unit is exactly the condition that produced 1000x-wrong values.
        """
        return bool(self.blocks) and self.unit_declared

    @property
    def signature(self) -> str:
        """Stable id for this table shape, so repeat runs recognize it.

        Covers the structure (unit, currency, period layout, change columns) but
        not the values, so the same issuer exhibit in a later quarter keeps the
        same signature with the years shifted.
        """
        shape = "|".join(
            f"{block.months}m@{block.end_month}:{block.value_index}" for block in self.blocks
        )
        raw = f"{self.unit_label}/{self.currency}/{shape}/change={self.has_change_columns}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _header_text(rows: list[list[str]], limit: int = 8) -> str:
    return " ".join(" ".join(cell for cell in row if cell) for row in rows[:limit])


def detect_unit(rows: list[list[str]], context: str = "") -> tuple[str, bool]:
    """(unit label, whether the document actually declared it).

    Header cells win over surrounding prose, since a table that states its own
    unit is authoritative even inside a document that mentions another.
    """
    for scope in (_header_text(rows), context):
        if not scope:
            continue
        for label, pattern in _UNIT_PATTERNS:
            if _declares(scope, pattern):
                return label, True
    return "millions", False


def detect_currency(rows: list[list[str]], context: str = "") -> tuple[str, bool]:
    """(ISO currency, whether it was declared). Defaults to USD undeclared."""
    for scope in (_header_text(rows), context):
        if not scope:
            continue
        for code, pattern in _CURRENCY_PATTERNS:
            if pattern.search(scope):
                return code, True
    return "USD", False


def _named_periods(text: str) -> list[tuple[int, int]]:
    """(months, end month) for each period this text names without a date."""
    found: list[tuple[int, int]] = []
    for match in _NAMED_PERIOD_RE.finditer(text):
        ordinal, counted = match.group(1), match.group(2)
        if ordinal:
            found.append((3, _ORDINAL_QUARTERS[ordinal.lower()]))
        elif counted:
            months = MONTH_WORDS.get(counted.lower(), 3)
            found.append((months, months))
        else:
            found.append((12, 12))
    return found


def _periods_named_in(text: str) -> list[tuple[int, int]]:
    """Every period this heading names, in the order it names them."""
    anchored: list[tuple[int, int]] = []
    for match in _PERIOD_PHRASE_RE.finditer(text):
        word = match.group(1).lower()
        months = 12 if word == "year" else MONTH_WORDS.get(word, 3)
        month = MONTHS.get(match.group(2).lower())
        if month:
            month, _ = fiscal_period_end(month, int(match.group(3)) if match.group(3) else None)
            anchored.append((months, month))
    return anchored or _named_periods(text)


def _period_phrases(rows: list[list[str]], limit: int = 8) -> list[tuple[int, int]]:
    """Ordered (months, end month) declared by the table header.

    Order matters: a 10-Q table reads "Three Months Ended June 30 ... Six Months
    Ended June 30", and the left-to-right order of those phrases is the
    left-to-right order of the value columns.

    A heading that ends mid-phrase continues on the next row: a press release
    prints "Three Months Ended" on one line and "June 30," on the next, and
    neither line alone names a period. Only such a row carries forward -
    joining rows wholesale would read two headings stacked above two blocks of
    figures as one heading over one block.
    """
    phrases: list[tuple[int, int]] = []
    carry: list[str] = []
    for row in rows[:limit]:
        cells = [cell.strip() for cell in row if cell and cell.strip()]
        if carry and len(carry) == len(cells):
            # The heading kept its columns when it broke, so each cell of this
            # row finishes the cell above it: "Three Months Ended" | "Six Months
            # Ended" over "June 30," | "June 30," is two headings, not one.
            cells = [f"{above} {below}" for above, below in zip(carry, cells, strict=True)]
        elif carry:
            cells = [" ".join(carry), *cells]
        phrases.extend(_periods_named_in(" ".join(cells)))
        if phrases:
            break
        dangling = bool(cells) and all(_DANGLING_PHRASE_RE.search(cell) for cell in cells)
        carry = cells if dangling else []
    return phrases


def _year_row(rows: list[list[str]], limit: int = 10) -> list[int]:
    """Years in column order, from the first row listing at least two."""
    for row in rows[:limit]:
        years = [int(year) for cell in row for year in _YEAR_RE.findall(cell or "")]
        if len(years) >= 2:
            return years
    return []


def _covering(grid: list[list[str | None]], row: int, column: int) -> str:
    """What the cell covering this column in this row says.

    A heading occupies the columns it spans: its text sits at the column it
    starts in and the rest hold None, so the covering text is found by walking
    left to the nearest origin.
    """
    while column >= 0:
        cell = grid[row][column]
        if cell is not None:
            return cell
        column -= 1
    return ""


def stated_periods(grid: list[list[str | None]]) -> tuple[int, dict[int, tuple[int, int, int]]]:
    """(months, end month, year) per column, read from the headings above it.

    This is what a table states about itself and what flattening destroys. The
    heading rows are the leading rows that carry no figures; everything they say
    over a column - the length phrase, the month, the year - is that column's
    period, so a heading split across three rows reads as one statement again
    and a prior-year column is distinguishable from a current one.

    A year standing alone in a heading row is a heading, not a figure: refusing
    that distinction is what made the year row look like data and cut the
    headings short.
    """
    if not grid:
        return 0, {}
    header_depth = 0
    for row in grid:
        if any(_is_figure(cell) for cell in row):
            break
        header_depth += 1
    if not header_depth:
        return 0, {}

    width = max(len(row) for row in grid)
    everything = " ".join(
        cell for row in grid[:header_depth] for cell in row if cell
    )
    fallback = _periods_named_in(everything)[:1]

    periods: dict[int, tuple[int, int, int]] = {}
    for column in range(width):
        stacked = " ".join(
            text
            for row in range(header_depth)
            if column < len(grid[row]) and (text := _covering(grid, row, column))
        )
        year_hit = _YEAR_RE.search(stacked)
        if not year_hit:
            continue
        named = _periods_named_in(stacked) or fallback
        if not named:
            continue
        months, month = named[0]
        periods[column] = (months, month, int(year_hit.group(0)))
    return header_depth, periods


def column_periods(grid: list[list[str | None]]) -> dict[int, tuple[int, int, int]]:
    """(months, end month, year) per column, where the headings describe the body."""
    header_depth, periods = stated_periods(grid)
    if not periods or not _headings_describe_the_body(grid, header_depth, periods):
        return {}
    return periods


def _headings_describe_the_body(
    grid: list[list[str | None]],
    header_depth: int,
    periods: dict[int, tuple[int, int, int]],
) -> bool:
    """Whether the headings' columns are the same columns the body uses.

    A table can span its headings and not span its body, and then the two are
    laid out in different columns that only line up on screen. A press release
    does exactly that: the heading row spans "Three Months Ended" over
    five columns and the year row spans each year over two, while every product
    row is written as plain cells, so the label lands in a column the headings
    say is 2016 and its four figures land under 2016, 2015, 2015, 2016.

    The tell is that a period covers a column the body puts a row label in.
    Where the two agree, the labels sit to the left of every period. Where they
    disagree the geometry is describing a layout the numbers are not in, and
    saying so is a guess dressed as a fact - the ragged reading, which infers
    the columns and can refuse, is the safer answer.
    """
    for row in grid[header_depth:]:
        origins = [(column, cell) for column, cell in enumerate(row) if cell]
        if not any(_is_figure(cell) for _column, cell in origins):
            # Not a row of the body: a section heading, a repeated title. It
            # states no figures, so it says nothing about where the figures are.
            continue
        column, cell = origins[0]
        if _is_figure(cell) or cell in {"$", "%"}:
            continue
        if column in periods:
            return False
    return True


def _is_figure(cell: str | None) -> bool:
    """A reported number, as opposed to a year naming a column."""
    text = (cell or "").strip().replace("$", "").strip()
    if not text or not _FIGURE_RE.fullmatch(text):
        return False
    return not _YEAR_RE.fullmatch(text)


def build_fingerprint(
    rows: list[list[str]],
    context: str = "",
    grid: list[list[str | None]] | None = None,
    period_context: "PeriodContext | None" = None,
) -> TableFingerprint:
    """Fingerprint one table: unit, currency, and period-to-column mapping.

    Given the table as a rectangle, the mapping is read from the headings that
    cover each column - what the table states. Given only ragged rows, it falls
    back to reading the period phrases and the year row as two ordered lists and
    dividing one into the other, which is an inference and can be wrong when a
    filing prints an uneven number of columns per period.

    ``period_context`` is the span the *document* says it covers, and it is
    read only when the table states no period of its own. Some issuers never
    put one in the table - a product schedule headed by geography and then by
    year says "months ended" nowhere - so a reader that requires the table to
    date itself skips them entirely.

    It is a last resort, and it is refused when the year row repeats a year.
    A repeated year means the columns are divided by something other than time
    - three geography bands, or a quarter beside its year-to-date - and one
    document period spread across them would file several different values
    under the same quarter. That is the failure this whole module exists to
    prevent, so an ambiguous table stays unread and says why.
    """
    unit_label, unit_declared = detect_unit(rows, context)
    currency, currency_declared = detect_currency(rows, context)
    header = _header_text(rows)
    has_change = bool(_PERCENT_RE.search(header))
    notes: list[str] = []

    if grid:
        by_column = column_periods(grid)
        if by_column:
            blocks = tuple(
                PeriodBlock(months=months, end_month=end_month, year=year, value_index=column)
                for column, (months, end_month, year) in sorted(by_column.items())
            )
            if not unit_declared:
                notes.append("unit_not_declared")
            if not currency_declared:
                notes.append("currency_not_declared")
            return TableFingerprint(
                unit_label=unit_label,
                currency=currency,
                blocks=blocks,
                has_change_columns=has_change,
                unit_declared=unit_declared,
                currency_declared=currency_declared,
                notes=tuple(notes),
                by_column=True,
            )

    phrases = _period_phrases(rows)
    years = _year_row(rows)
    blocks: tuple[PeriodBlock, ...] = ()

    if phrases and years:
        if len(years) % len(phrases) == 0:
            per_block = len(years) // len(phrases)
            built: list[PeriodBlock] = []
            for phrase_index, (months, end_month) in enumerate(phrases):
                for offset in range(per_block):
                    year = years[phrase_index * per_block + offset]
                    built.append(
                        PeriodBlock(
                            months=months,
                            end_month=end_month,
                            year=year,
                            value_index=phrase_index * per_block + offset,
                        )
                    )
            blocks = tuple(built)
        else:
            # Header years do not divide evenly across the declared periods, so
            # any column mapping would be a guess. Report it instead.
            notes.append(
                f"unmapped_columns years={len(years)} periods={len(phrases)}"
            )
    elif not phrases and period_context is not None and years:
        if len(set(years)) == len(years):
            blocks = tuple(
                PeriodBlock(
                    months=period_context.months,
                    end_month=period_context.month,
                    year=year,
                    value_index=index,
                )
                for index, year in enumerate(years)
            )
            notes.append("period_from_document")
        else:
            notes.append(f"period_from_document_ambiguous years={years}")
    elif not phrases:
        notes.append("no_period_header")
    elif not years:
        notes.append("no_year_header")

    if not unit_declared:
        notes.append("unit_not_declared")
    if not currency_declared:
        notes.append("currency_not_declared")

    return TableFingerprint(
        unit_label=unit_label,
        currency=currency,
        blocks=blocks,
        has_change_columns=has_change,
        unit_declared=unit_declared,
        currency_declared=currency_declared,
        notes=tuple(notes),
    )
