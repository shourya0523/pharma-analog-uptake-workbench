"""Stage 1: fingerprint a source table before reading any numbers out of it.

A fingerprint is what the table declares about itself: the unit its numbers are
stated in, the currency, and which column holds which reporting period. Reading
values without first establishing those three things is where extraction goes
wrong, and the failure is silent - the numbers still look like plausible
revenue, they are just off by 1000x, in the wrong currency, or attributed to the
wrong period.

Two real defects motivated this module, both found by auditing the gold dataset:

* United Therapeutics' earnings exhibits switch from whole-dollar thousands
  ("121,718") to one-decimal millions ("102.2") partway through 2016. Code that
  assumed a unit from the filing date divided four quarters by 1000 across five
  products and produced a fake 99.9% revenue collapse.
* Merck's prior-year comparison schedule lists 2024 as Q2/Q3/Q4 + FY, not
  Q1-Q4. Code that assumed a fixed number of quarter columns per block consumed
  the full-year total as if it were a quarter.

Both are prevented here by reading the table's own declarations instead of
inferring them from context, and by refusing to guess when the declaration is
absent - an unfingerprintable table yields no values rather than wrong ones.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.parsing.periods import MONTH_WORDS, MONTHS, quarter_of_month

# "Three months ended June 30," / "Nine Months Ended September 30" / "Year ended"
_PERIOD_PHRASE_RE = re.compile(
    r"\b(three|six|nine|twelve|year)s?\s*(?:months?\s*)?ended\s+([A-Za-z]{3,9})",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_PERCENT_RE = re.compile(r"%")

# A currency may sit between "in" and the magnitude word ("in CHF millions",
# "$ in millions", "in thousands of Swiss francs"), so unit detection has to
# read past it rather than require the two words to be adjacent.
_CURRENCY_TOKEN = (
    r"(?:[A-Z]{3}|US\$|U\.S\.|\$|£|€|dollars?|swiss\s+francs?|francs?|"
    r"pounds?(?:\s+sterling)?|euros?|yen)"
)


def _unit_pattern(word: str) -> re.Pattern[str]:
    """Match a magnitude declaration in any of the forms issuers print it."""
    return re.compile(
        rf"\b(?:in|of)\s+(?:{_CURRENCY_TOKEN}\s+){{0,2}}{word}\b"
        rf"|{_CURRENCY_TOKEN}\s*{word}\b"
        rf"|\b{word}\s+of\b",
        re.IGNORECASE,
    )


# Most specific first: "thousands" and "billions" before "millions", so a header
# reading "in thousands" is never matched by a stray later "million".
_UNIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("billions", _unit_pattern(r"billions?")),
    ("thousands", _unit_pattern(r"thousands?")),
    ("millions", _unit_pattern(r"millions?")),
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
            if pattern.search(scope):
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


def _period_phrases(rows: list[list[str]], limit: int = 8) -> list[tuple[int, int]]:
    """Ordered (months, end month) declared by the table header.

    Order matters: a 10-Q table reads "Three Months Ended June 30 ... Six Months
    Ended June 30", and the left-to-right order of those phrases is the
    left-to-right order of the value columns.
    """
    phrases: list[tuple[int, int]] = []
    for row in rows[:limit]:
        joined = " ".join(cell for cell in row if cell)
        for match in _PERIOD_PHRASE_RE.finditer(joined):
            word = match.group(1).lower()
            months = 12 if word == "year" else MONTH_WORDS.get(word, 3)
            month = MONTHS.get(match.group(2).lower())
            if month:
                phrases.append((months, month))
        if phrases:
            break
    return phrases


def _year_row(rows: list[list[str]], limit: int = 10) -> list[int]:
    """Years in column order, from the first header row listing at least two."""
    for row in rows[:limit]:
        years = [int(year) for cell in row for year in _YEAR_RE.findall(cell or "")]
        if len(years) >= 2:
            return years
    return []


def build_fingerprint(rows: list[list[str]], context: str = "") -> TableFingerprint:
    """Fingerprint one table: unit, currency, and period-to-column mapping."""
    unit_label, unit_declared = detect_unit(rows, context)
    currency, currency_declared = detect_currency(rows, context)
    header = _header_text(rows)
    has_change = bool(_PERCENT_RE.search(header))
    notes: list[str] = []

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
