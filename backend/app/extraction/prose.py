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

The one exception is an explicit pairing. "Sales were $336 million and $615
million in the second quarter and first six months of 2025, respectively" is
not ambiguous - "respectively" states the correspondence, and issuers use this
construction constantly for quarter-plus-year-to-date. It is read only when the
counts match exactly and the pairing word is present, so the relationship is
still taken from what the sentence says rather than from word order alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # a type only; periods.py must not import this module back
    from app.parsing.periods import PeriodContext

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from app.extraction.extract import ExtractedValue
from app.extraction.members import load_products, words
from app.parsing.evidence import product_aliases
from app.parsing.periods import (
    MONTH_WORDS,
    MONTHS,
    MONTHS_TO_PERIOD_TYPE,
    fiscal_period_end,
    period_label,
    periods_named,
    quarter_of_month,
)
from app.quality.candidate_filters import _AGGREGATE_WORDS

_MAGNITUDE_TO_UNIT = {"billion": "billions", "million": "millions", "thousand": "thousands"}
_ORDINAL_TO_QUARTER = {"first": 1, "second": 2, "third": 3, "fourth": 4}

_SYMBOL_TO_CURRENCY = {"$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY"}

_MONEY_RE = re.compile(
    r"(?P<currency>\$|£|€|¥|\bCHF\b|\bUSD\b|\bGBP\b|\bEUR\b)?\s*"
    r"(?P<amount>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<magnitude>million|billion|thousand)?s?",
    re.IGNORECASE,
)

# "quarter ended June 30, 2002" / "three months ended September 30, 2016"
_PERIOD_ENDED_RE = re.compile(
    r"\b(?:(?P<length>three|six|nine|twelve)\s+months?|(?P<quarter_word>quarter)|(?P<annual>year))\s+"
    r"ended\s+(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2}),?\s+(?P<year>(?:19|20)\d{2})",
    re.IGNORECASE,
)
# "first quarter 2003" / "fourth quarter of 2003"
_ORDINAL_QUARTER_RE = re.compile(
    r"\b(?P<ordinal>first|second|third|fourth)\s+quarter\s+(?:of\s+)?(?P<year>(?:19|20)\d{2})",
    re.IGNORECASE,
)
# "Q4 2016" / "2016Q4"
# "Q4 2016" / "2016Q4" / "1Q 2025". The last is the ordinary way a US issuer
# writes it in a release, and it was the one form not read here: a quote
# saying "1Q 2025" named no period at all, so nothing downstream could tell
# it apart from a quote that named the row's own quarter.
_COMPACT_QUARTER_RE = re.compile(
    r"\bQ(?P<q>[1-4])\s*(?P<year>(?:19|20)\d{2})\b"
    r"|\b(?P<year2>(?:19|20)\d{2})\s*Q(?P<q2>[1-4])\b"
    r"|\b(?P<q3>[1-4])Q\s*(?P<year3>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
# "full-year 2002" / "fiscal year 2002" / "FY2002" / "for the year 2002".
# Issuers state annual figures this way at least as often as "year ended", and
# an annual total is what lets an unstated fourth quarter be derived.
_ANNUAL_WORD_RE = re.compile(
    r"\b(?:full[-\s]?year|fiscal\s+year|FY|for\s+the\s+year)\s*(?P<year>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)

# "the second quarter and first six months of 2025" - one trailing year shared
# by a quarter and a year-to-date period. Issuers pair these constantly, and
# neither half matches the single-period patterns above because the quarter's
# year only appears after the second phrase.
_QUARTER_AND_YTD_RE = re.compile(
    r"\b(?P<ordinal>first|second|third|fourth)\s+quarter\s+and\s+"
    r"first\s+(?P<length>six|nine)\s+months\s+of\s+(?P<year>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)

# The word that turns several amounts and several periods from ambiguous into
# an explicit, ordered correspondence.
_PAIRING_RE = re.compile(r"\brespectively\b", re.IGNORECASE)

# A sentence ends at its terminator, and the closing quote, bracket or
# footnote mark that may follow it belongs to the sentence rather than to
# the next one. Requiring whitespace *immediately* after the terminator
# meant a paragraph ending `studies.”` never split, so a sentence naming a
# product ran on into the one after it: a release mentions a product in a
# sentence about clinical trials and states total company revenues in the next,
# and the reader paired the product from one with the amount from the other,
# which reads high because a company total is larger than any one product in it.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;])[\"'\u201d\u2019)\]]*\s+")


@dataclass(frozen=True)
class _Period:
    """A period a sentence names: its canonical key and the span it covers."""

    period: str
    months: int

    @property
    def period_type(self) -> str:
        return MONTHS_TO_PERIOD_TYPE[self.months]


# A four-digit year standing on its own. A table prints the span once in its
# heading and the years under it, one per column, so the years are read apart
# from the phrase that says what they are years of.
_YEAR_TOKEN_RE = re.compile(r"\b(?:19|20)\d{2}\b")
# The quarter `period_label` wrote into a key, read back out of it so the same
# span can be renamed for another year.
_QUARTER_OF_KEY_RE = re.compile(r"\d{4}Q([1-4])\Z")


def periods_named_in(text: str) -> set[str]:
    """Every period this text names, as period keys ("2025Q1", "2025H1", "2025M9").

    A period is named by a span and a year, and a table prints the two apart:

        For the Three Months Ended June 30, / For the Six Months Ended June 30,
        2024 / 2023 / 2024 / 2023
        Calderon XR $ 34,974 $ 22,209 $ 61,183 $ 40,776

    states four periods and no phrase in it states more than one. So the
    periods a block names are the spans it states, each ending in each of the
    years it states.

    Empty where the text states no span at all - a table row lifted away from
    its heading - which says nothing about which period its figures are for.
    """
    body = text or ""
    years = {int(year) for year in _YEAR_TOKEN_RE.findall(body)}
    named: set[str] = set()
    for key, months in _spans_named_in(body):
        if key:
            named.add(key)
        match = _QUARTER_OF_KEY_RE.search(key or "")
        quarter = int(match.group(1)) if match else 0
        for year in years:
            named.add(period_label(year, months, quarter))
    return named


def spans_named_in(text: str) -> set[int]:
    """The span in months of each period this text names.

    What a period is called and how long it is are different questions, and a
    heading can answer the second without answering the first.
    """
    return {months for _key, months in _spans_named_in(text or "")}


def _spans_named_in(text: str) -> list[tuple[str | None, int]]:
    """(period key, span in months) for every period the text names.

    Two grammars read it, and neither contains the other. `periods.periods_named`
    reads the heading a statement carries and the compact notation a note or a
    release uses; the two patterns `_prose_only_periods` asks are the forms only
    running prose writes - "full-year 2002", "the second quarter and first six
    months of 2025". This module's other period patterns are spellings the
    parsing grammar already reads, so they are not asked twice here.

    A phrase can state a span without stating a date - a heading whose years are
    printed in the columns below it - and such a period's key is None. It is
    kept: what a period is called and how long it is are different questions,
    and the caller that wants keys drops the ones that have none.
    """
    spans: list[tuple[str | None, int]] = [
        (period.key, period.months) for period in periods_named(text)
    ]
    spans += _prose_only_periods(text)
    return spans


def _prose_only_periods(text: str) -> list[tuple[str, int]]:
    """The periods named in forms the parsing grammar does not read.

    "the second quarter and first six months of 2025" states two periods
    sharing one trailing year, and neither half states a year of its own;
    "full-year 2002" states a span in a word the statement headings never use.
    """
    found: list[tuple[str, int]] = []
    for match in _QUARTER_AND_YTD_RE.finditer(text):
        year = int(match.group("year"))
        quarter = _ORDINAL_TO_QUARTER[match.group("ordinal").lower()]
        months = MONTH_WORDS[match.group("length").lower()]
        found.append((period_label(year, 3, quarter), 3))
        found.append((period_label(year, months, quarter), months))
    for match in _ANNUAL_WORD_RE.finditer(text):
        found.append((period_label(int(match.group("year")), 12, 0), 12))
    return found


def _periods_with_positions(sentence: str) -> list[tuple[int, _Period]]:
    """Every reporting period the sentence names, with where it names it.

    Textual order matters: when a sentence pairs several amounts with several
    periods using "respectively", the correspondence is positional, so periods
    discovered by different patterns still have to come back in reading order.
    """
    found: list[tuple[int, _Period]] = []
    # The combined "<quarter> and first <six|nine> months of <year>" form is
    # matched first and its span consumed, so the single-period patterns below
    # cannot also claim the shared trailing year.
    consumed: list[tuple[int, int]] = []
    for match in _QUARTER_AND_YTD_RE.finditer(sentence):
        year = int(match.group("year"))
        quarter = _ORDINAL_TO_QUARTER[match.group("ordinal").lower()]
        months = MONTH_WORDS[match.group("length").lower()]
        found.append((match.start(), _Period(period_label(year, 3, quarter), 3)))
        found.append((match.start("length"), _Period(period_label(year, months, quarter), months)))
        consumed.append(match.span())

    def claimed(position: int) -> bool:
        return any(start <= position < end for start, end in consumed)

    for match in _PERIOD_ENDED_RE.finditer(sentence):
        if claimed(match.start()):
            continue
        year = int(match.group("year"))
        month = MONTHS.get((match.group("month") or "").lower())
        if not month:
            continue
        month, year = fiscal_period_end(month, int(match.group("day")), year)
        if match.group("annual") or (match.group("length") or "").lower() == "twelve":
            months = 12
        elif match.group("quarter_word") or (match.group("length") or "").lower() == "three":
            months = 3
        else:
            months = MONTH_WORDS[(match.group("length")).lower()]
        quarter = quarter_of_month(month)
        found.append((match.start(), _Period(period_label(year, months, quarter), months)))
    for match in _ORDINAL_QUARTER_RE.finditer(sentence):
        if claimed(match.start()):
            continue
        quarter = _ORDINAL_TO_QUARTER[match.group("ordinal").lower()]
        year = int(match.group("year"))
        found.append((match.start(), _Period(period_label(year, 3, quarter), 3)))
    for match in _COMPACT_QUARTER_RE.finditer(sentence):
        if claimed(match.start()):
            continue
        quarter = match.group("q") or match.group("q2") or match.group("q3")
        year = match.group("year") or match.group("year2") or match.group("year3")
        found.append((match.start(), _Period(period_label(int(year), 3, int(quarter)), 3)))
    for match in _ANNUAL_WORD_RE.finditer(sentence):
        if claimed(match.start()):
            continue
        found.append((match.start(), _Period(period_label(int(match.group("year")), 12, 0), 12)))
    # One period named twice ("fourth quarter 2003" then "Q4 2003") is still one
    # period; only genuinely different periods make a sentence ambiguous.
    unique: list[_Period] = []
    positioned: list[tuple[int, _Period]] = []
    for position, period in sorted(found, key=lambda item: item[0]):
        if period not in unique:
            unique.append(period)
            positioned.append((position, period))
    return positioned


def _amounts_with_positions(sentence: str) -> list[tuple[int, tuple[float, str, str]]]:
    """Each money figure with where it appears, in reading order."""
    amounts: list[tuple[int, tuple[float, str, str]]] = []
    for match in _MONEY_RE.finditer(sentence):
        magnitude = match.group("magnitude")
        raw_amount = match.group("amount")
        if magnitude:
            unit = _MAGNITUDE_TO_UNIT[magnitude.lower()]
        else:
            # An amount too small to print in millions is written out in full:
            # a product's first quarter on sale is reported as "$205,000".
            # Without this the figure is invisible and the quarter looks
            # unreported rather than small.
            #
            # Two conditions keep this from swallowing every bare number in a
            # filing: a currency symbol must be attached, and the amount must be
            # written with thousands separators. That admits "$205,000" while
            # leaving bare years and "$25.0" alone.
            if not match.group("currency") or "," not in raw_amount:
                continue
            unit = "units"
        raw = match.group("currency") or "$"
        currency = _SYMBOL_TO_CURRENCY.get(raw, raw.upper())
        amounts.append(
            (match.start(), (float(raw_amount.replace(",", "")), unit, currency))
        )
    return amounts


def _amounts_in(sentence: str) -> list[tuple[float, str, str]]:
    """(amount, unit, currency) for each money figure in the sentence."""
    return [amount for _, amount in _amounts_with_positions(sentence)]


def _interleaved(
    amounts: list[tuple[int, tuple[float, str, str]]],
    periods: list[tuple[int, _Period]],
) -> bool:
    """True when the sentence alternates amount, period, amount, period, ...

    An enumeration in this shape pairs each amount with the period that follows
    it, with no ambiguity to resolve and no pairing word needed. Anything else -
    two amounts in a row, a period before its amount, unequal counts - is not
    this pattern and is left alone.
    """
    if len(amounts) < 2 or len(amounts) != len(periods):
        return False
    marks = sorted(
        [(position, "amount") for position, _ in amounts]
        + [(position, "period") for position, _ in periods]
    )
    expected = ["amount", "period"] * len(amounts)
    return [kind for _, kind in marks] == expected


@lru_cache(maxsize=1)
def _catalog() -> tuple[str, ...]:
    """The products this pipeline tracks, from its own reference data."""
    return tuple(load_products())


# An amount introduced by "by" is a difference, not a level: "increased
# revenues by $3.6 million" says how much revenue moved, not what it was.
# "totaled $8.5 million", "were $120.8 million" and "grew to $325.8 million"
# all state the figure itself.
_DIFFERENCE_RE = re.compile(r"\bby\s+(?:approximately\s+|about\s+|roughly\s+)?$", re.IGNORECASE)

# A figure stated to a date part-way through the period it names is that
# period's running total, not its total: "As of November 9, 2002, sales for the
# fourth quarter of 2002 totaled approximately $8.5 million" is forty days into
# a quarter that has ten weeks left to run.
_PART_PERIOD_RE = re.compile(
    r"\b(?:as of|through|as at|to date)\b(?![^.]*\bend(?:ed|ing)\b)", re.IGNORECASE
)


def _states_a_level(sentence: str, position: int) -> bool:
    """Whether the amount at ``position`` is revenue rather than a move in it."""
    return not _DIFFERENCE_RE.search(sentence[:position])


def _named_products(sentence: str, catalog: Iterable[str]) -> set[str]:
    """Which tracked products a sentence names.

    Names are matched as whole words and the longest wins where two overlap: a
    sentence saying "Calderon XR" names that product, and is not evidence that
    it also names Calderon. This is the rule the XBRL member register already
    resolves by, for the same reason - a shorter product name sits inside a
    longer one far more often than it is a second product.
    """
    tokens = words(sentence)
    spans: list[tuple[int, int, str]] = []
    for product in catalog:
        parts = words(product)
        if not parts:
            continue
        width = len(parts)
        spans += [
            (start, width, product)
            for start in range(len(tokens) - width + 1)
            if tokens[start : start + width] == parts
        ]
    return {
        product
        for start, width, product in spans
        if not any(
            other_start <= start
            and start + width <= other_start + other_width
            and other_width > width
            for other_start, other_width, _ in spans
        )
    }


def _introduced_as_an_aggregate(sentence: str, aliases: Iterable[str], position: int) -> bool:
    """Whether the amount at ``position`` is introduced as a total, not as ours.

    "Total revenues, comprised of net product sales from Calderon and NuVessa,
    were $242.0 million" states a figure that covers two products, and names
    ours among them - so neither the one-product rule nor the filer's marks
    refuse it, and the figure was published as Calderon's own.

    What the sentence does say is the order: the aggregate heads it and the
    product appears inside the thing being aggregated. Where our name comes
    first the aggregate is a total *of* our product - "Calderon total net
    sales across both regions" - and is this product's figure. So the test is
    position, not vocabulary: an aggregate word before the first mention of
    our product, and before the amount.
    """
    lowered = sentence.casefold()
    mentions = [lowered.find(alias) for alias in aliases if alias in lowered]
    first_mention = min(mentions) if mentions else len(sentence)
    for match in re.finditer(r"[a-z]+", lowered[:position]):
        if match.group() in _AGGREGATE_WORDS and match.start() < first_mention:
            return True
    return False


# A name the filer marks as its own: "Calderon(R)", "NuVessa (TM)". The mark
# is the filer saying this is a brand, so it names a product whether or not
# anyone tracks that product. Three characters or more, because a mark sits on
# brands rather than on initials.
_TRADEMARKED_RE = re.compile(r"([A-Za-z][A-Za-z0-9\-]{2,})\s*[\u00ae\u2122]")


def _other_trademarked_products(sentence: str, aliases: Iterable[str]) -> set[str]:
    """Marked names in this sentence that are not the product asked about.

    `_named_products` can only see what its catalogue holds, so a sibling
    nobody tracks is invisible to it and the sentence reads as unambiguous
    when it is not. The filer's own mark answers without a catalogue: a
    release naming two of its brands marks both, and a figure introduced
    beside them has not said which one it belongs to.
    """
    ours = {alias.casefold() for alias in aliases}
    return {
        name
        for name in _TRADEMARKED_RE.findall(sentence or "")
        if name.casefold() not in ours
        and not any(name.casefold() in alias or alias in name.casefold() for alias in ours)
    }


def _after_the_document(period: str, context: PeriodContext | None) -> bool:
    """Whether a period ends after the document's own reporting period.

    A filing reports its period and the ones before it. A later period in
    its text is a forecast - a payment the issuer "may receive in 2027" - and
    is never a quarter's revenue, however the sentence is shaped.
    """
    if context is None:
        return False
    match = re.fullmatch(r"(\d{4})(?:Q([1-4])|H([12])|M9)?", period or "")
    if not match:
        return False
    year = int(match.group(1))
    if match.group(2):
        end_month = int(match.group(2)) * 3
    elif match.group(3):
        end_month = int(match.group(3)) * 6
    elif period.endswith("M9"):
        end_month = 9
    else:
        end_month = 12
    return (year, end_month) > (context.year, context.month)


def read_prose(
    text: str,
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
    catalog: Iterable[str] | None = None,
    period_context: PeriodContext | None = None,
    products: Iterable[str] | None = None,
) -> list[ExtractedValue]:
    """Revenue figures stated in sentences that name this product.

    A sentence contributes a value only when it names exactly one period, one
    amount and one product, so what each number belongs to is stated rather
    than inferred.

    The third of those was missing, and it is the same principle as the other
    two. A sentence was accepted whenever an alias appeared anywhere in it, so
    a sentence naming two products answered a question about either of them
    with the same figure: a sentence stating a new formulation's first quarter
    on sale beside the established formulation's answered both with whichever
    amount it found. A sentence covering two products has not said which one its
    amount belongs to, exactly as a sentence carrying two amounts has not said
    which period each belongs to.

    ``catalog`` is what to count as a product, defaulting to the ones this
    pipeline tracks. Ambiguity is a property of the sentence against the things
    it could be confused with, so a caller tracking nothing loses nothing.
    """
    aliases = [alias.lower() for alias in product_aliases(product, generic, extra=extra_aliases)]
    # The caller's products as well as the tracked ones: a run is asked about
    # drugs the catalogue does not hold yet, and those are exactly the sentences
    # this reader is asked to read.
    known_products = tuple(catalog if catalog is not None else _catalog()) + tuple(products or ())
    values: list[ExtractedValue] = []

    for sentence in _SENTENCE_SPLIT_RE.split(text or ""):
        lowered = sentence.lower()
        if not any(alias in lowered for alias in aliases):
            continue
        known = _named_products(sentence, known_products)
        if len(known) > 1 or (known and product not in known):
            # The sentence covers more than this product, or the name it does
            # carry is a longer one belonging to something else.
            continue
        if _other_trademarked_products(sentence, aliases):
            # A brand the filer marks that is not ours. The catalogue above may
            # never have heard of it; the mark says what it is regardless.
            continue
        if _PART_PERIOD_RE.search(sentence):
            # The figure is stated to a date inside the period it names, so it
            # is what had been sold by then rather than what the period sold.
            continue
        located_periods = _periods_with_positions(sentence)
        located_amounts = [
            (position, amount)
            for position, amount in _amounts_with_positions(sentence)
            if _states_a_level(sentence, position)
            and not _introduced_as_an_aggregate(sentence, aliases, position)
        ]
        periods = [period for _, period in located_periods]
        amounts = [amount for _, amount in located_amounts]
        if len(periods) == 1 and len(amounts) == 1:
            pairs = [(periods[0], amounts[0])]
        elif _interleaved(located_amounts, located_periods):
            # Each amount is followed by its own period before the next amount
            # begins: "$205,000 in the three months ended March 31, 2002,
            # $8.7 million in the three months ended June 30, 2002, and ...".
            # The correspondence is stated by the sentence's structure, so this
            # needs no pairing word - and unlike proximity guessing, a sentence
            # that does not strictly alternate is rejected rather than assumed.
            pairs = list(zip(periods, amounts))
        elif (
            len(periods) > 1
            and len(periods) == len(amounts)
            and _PAIRING_RE.search(sentence)
        ):
            # "respectively" states the correspondence, so this is reading the
            # sentence rather than guessing from proximity. Equal counts are
            # required: if the sentence names three periods and two amounts,
            # the pairing word does not say which was dropped.
            pairs = list(zip(periods, amounts))
        else:
            continue
        for index, (period, (amount, unit, currency)) in enumerate(pairs):
            if _after_the_document(period.period, period_context):
                continue
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
                    value_index=index,
                )
            )
    return values
