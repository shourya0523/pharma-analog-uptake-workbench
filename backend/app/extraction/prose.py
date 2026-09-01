"""Read a revenue figure stated in a sentence rather than a table.

Issuers disclose product sales narratively more often than the table-shaped
mental model suggests - older filings predate the product-sales exhibit
entirely, and smaller issuers never adopt one. A pipeline that only reads
tables simply has no data for those periods, which is why several years of a
product's early history go missing.

The same discipline applies here as to tables: the sentence has to say what the
number means. A sentence carries its unit and currency right next to the amount
("$8.7 million"), which is a stronger declaration than a table header, but it
must also name exactly one period and one amount. A sentence mentioning several
of either is ambiguous about which belongs to which, so it is refused rather
than resolved by proximity.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.extraction.extract import ExtractedValue
from app.parsing.evidence import product_aliases
from app.parsing.periods import MONTHS, quarter_of_month

_MAGNITUDE_TO_UNIT = {"billion": "billions", "million": "millions", "thousand": "thousands"}
_ORDINAL_TO_QUARTER = {"first": 1, "second": 2, "third": 3, "fourth": 4}

_SYMBOL_TO_CURRENCY = {"$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY"}

_MONEY_RE = re.compile(
    r"(?P<currency>\$|£|€|¥|\bCHF\b|\bUSD\b|\bGBP\b|\bEUR\b)?\s*"
    r"(?P<amount>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<magnitude>million|billion|thousand)s?",
    re.IGNORECASE,
)

# "quarter ended June 30, 2002" / "three months ended September 30, 2016"
_PERIOD_ENDED_RE = re.compile(
    r"\b(?:(?P<length>three|six|nine|twelve)\s+months?|(?P<quarter_word>quarter)|(?P<annual>year))\s+"
    r"ended\s+(?P<month>[A-Za-z]{3,9})\s+\d{1,2},?\s+(?P<year>(?:19|20)\d{2})",
    re.IGNORECASE,
)
# "first quarter 2003" / "fourth quarter of 2003"
_ORDINAL_QUARTER_RE = re.compile(
    r"\b(?P<ordinal>first|second|third|fourth)\s+quarter\s+(?:of\s+)?(?P<year>(?:19|20)\d{2})",
    re.IGNORECASE,
)
# "Q4 2016" / "2016Q4"
_COMPACT_QUARTER_RE = re.compile(
    r"\bQ(?P<q>[1-4])\s*(?P<year>(?:19|20)\d{2})\b|\b(?P<year2>(?:19|20)\d{2})\s*Q(?P<q2>[1-4])\b",
    re.IGNORECASE,
)
# "full-year 2002" / "fiscal year 2002" / "FY2002" / "for the year 2002".
# Issuers state annual figures this way at least as often as "year ended", and
# an annual total is what lets an unstated fourth quarter be derived.
_ANNUAL_WORD_RE = re.compile(
    r"\b(?:full[-\s]?year|fiscal\s+year|FY|for\s+the\s+year)\s*(?P<year>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;])\s+")


@dataclass(frozen=True)
class _Period:
    period: str
    period_type: str


def _periods_in(sentence: str) -> list[_Period]:
    """Every reporting period the sentence names, in order."""
    found: list[_Period] = []
    for match in _PERIOD_ENDED_RE.finditer(sentence):
        year = int(match.group("year"))
        month = MONTHS.get((match.group("month") or "").lower())
        if not month:
            continue
        if match.group("annual") or (match.group("length") or "").lower() == "twelve":
            found.append(_Period(str(year), "annual"))
        elif match.group("quarter_word") or (match.group("length") or "").lower() == "three":
            found.append(_Period(f"{year}Q{quarter_of_month(month)}", "quarterly"))
        else:
            months = {"six": "six_month", "nine": "nine_month"}[(match.group("length")).lower()]
            found.append(_Period(str(year), months))
    for match in _ORDINAL_QUARTER_RE.finditer(sentence):
        quarter = _ORDINAL_TO_QUARTER[match.group("ordinal").lower()]
        found.append(_Period(f"{int(match.group('year'))}Q{quarter}", "quarterly"))
    for match in _COMPACT_QUARTER_RE.finditer(sentence):
        quarter = match.group("q") or match.group("q2")
        year = match.group("year") or match.group("year2")
        found.append(_Period(f"{int(year)}Q{int(quarter)}", "quarterly"))
    for match in _ANNUAL_WORD_RE.finditer(sentence):
        found.append(_Period(str(int(match.group("year"))), "annual"))
    # One period named twice ("fourth quarter 2003" then "Q4 2003") is still one
    # period; only genuinely different periods make a sentence ambiguous.
    unique: list[_Period] = []
    for period in found:
        if period not in unique:
            unique.append(period)
    return unique


def _amounts_in(sentence: str) -> list[tuple[float, str, str]]:
    """(amount, unit, currency) for each money figure in the sentence."""
    amounts: list[tuple[float, str, str]] = []
    for match in _MONEY_RE.finditer(sentence):
        raw = match.group("currency") or "$"
        currency = _SYMBOL_TO_CURRENCY.get(raw, raw.upper())
        unit = _MAGNITUDE_TO_UNIT[match.group("magnitude").lower()]
        amounts.append((float(match.group("amount").replace(",", "")), unit, currency))
    return amounts


def read_prose(
    text: str,
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
) -> list[ExtractedValue]:
    """Revenue figures stated in sentences that name this product.

    A sentence contributes a value only when it names exactly one period and
    one amount, so which number belongs to which period is stated rather than
    inferred.
    """
    aliases = [alias.lower() for alias in product_aliases(product, generic, extra=extra_aliases)]
    values: list[ExtractedValue] = []

    for sentence in _SENTENCE_SPLIT_RE.split(text or ""):
        lowered = sentence.lower()
        if not any(alias in lowered for alias in aliases):
            continue
        periods = _periods_in(sentence)
        amounts = _amounts_in(sentence)
        if len(periods) != 1 or len(amounts) != 1:
            continue
        amount, unit, currency = amounts[0]
        period = periods[0]
        values.append(
            ExtractedValue(
                product_label=product,
                period=period.period,
                period_type=period.period_type,
                value_as_reported=amount,
                unit_label=unit,
                currency=currency,
                source_quote=sentence.strip(),
                fingerprint_signature="prose",
                value_index=0,
            )
        )
    return values
