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
    r"(?P<magnitude>million|billion|thousand)?s?"
    r"(?:\s+(?P<currency_after>swiss\s+francs?|francs?|euros?|pounds?(?:\s+sterling)?|(?:u\.?s\.?\s+)?dollars?|CHF|USD|EUR|GBP|JPY|yen))?",
    re.IGNORECASE,
)
_CURRENCY_WORDS = {
    "swiss franc": "CHF", "swiss francs": "CHF", "franc": "CHF", "francs": "CHF",
    "euro": "EUR", "euros": "EUR", "pound": "GBP", "pounds": "GBP", "pounds sterling": "GBP",
    "dollar": "USD", "dollars": "USD", "u.s. dollars": "USD", "us dollars": "USD", "yen": "JPY",
}

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
    r"\b(?:full[-\s]?year|fiscal\s+year|FY|for\s+the\s+(?:full\s+)?year|for\s+the\s+twelve\s+months\s+of)\s*(?P<year>(?:19|20)\d{2})\b"
    r"|\b(?:for|during|in)\s+(?P<year_bare>(?:19|20)\d{2})\b(?!\s*Q[1-4])",
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

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;])\s+")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")

# A figure that is a movement, not a level: "increased by $3.7 million",
# "grew $65.2 million", "a decrease of $80.7 million".
_CHANGE_BEFORE_RE = re.compile(
    r"\b(?:increas\w*|decreas\w*|grew|growth|declin\w*|rose|fell|dropp\w*|up|down|higher|lower|"
    r"improv\w*|reduc\w*|change[sd]?)\s+(?:by\s+|of\s+)?(?:approximately\s+|about\s+)?$",
    re.IGNORECASE,
)
_CHANGE_AFTER_RE = re.compile(
    r"^\s*(?:increase|decrease|growth|decline|improvement|reduction|change)\b", re.IGNORECASE
)
# The product is mentioned only to be set aside, or as one of a list.
_EXCLUDED_BEFORE_RE = re.compile(
    r"\b(?:excluding|except(?:\s+for)?|other\s+than|net\s+of|besides|apart\s+from)\s+(?:sales\s+of\s+)?$",
    re.IGNORECASE,
)
_REVENUE_NOUN = r"(?:net\s+)?(?:product\s+)?(?:sales|revenues?)"


@dataclass(frozen=True)
class _Period:
    period: str
    period_type: str


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
        length = {"six": "six_month", "nine": "nine_month"}[match.group("length").lower()]
        found.append((match.start(), _Period(f"{year}Q{quarter}", "quarterly")))
        found.append((match.start("length"), _Period(str(year), length)))
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
        if match.group("annual") or (match.group("length") or "").lower() == "twelve":
            found.append((match.start(), _Period(str(year), "annual")))
        elif match.group("quarter_word") or (match.group("length") or "").lower() == "three":
            found.append(
                (match.start(), _Period(f"{year}Q{quarter_of_month(month)}", "quarterly"))
            )
        else:
            months = {"six": "six_month", "nine": "nine_month"}[(match.group("length")).lower()]
            found.append((match.start(), _Period(str(year), months)))
    for match in _ORDINAL_QUARTER_RE.finditer(sentence):
        if claimed(match.start()):
            continue
        quarter = _ORDINAL_TO_QUARTER[match.group("ordinal").lower()]
        found.append((match.start(), _Period(f"{int(match.group('year'))}Q{quarter}", "quarterly")))
    for match in _COMPACT_QUARTER_RE.finditer(sentence):
        if claimed(match.start()):
            continue
        quarter = match.group("q") or match.group("q2")
        year = match.group("year") or match.group("year2")
        found.append((match.start(), _Period(f"{int(year)}Q{int(quarter)}", "quarterly")))
    for match in _ANNUAL_WORD_RE.finditer(sentence):
        if claimed(match.start()):
            continue
        year = match.group("year") or match.group("year_bare")
        found.append((match.start(), _Period(str(int(year)), "annual")))
    # One period named twice ("fourth quarter 2003" then "Q4 2003") is still one
    # period; only genuinely different periods make a sentence ambiguous.
    unique: list[_Period] = []
    positioned: list[tuple[int, _Period]] = []
    for position, period in sorted(found, key=lambda item: item[0]):
        if period not in unique:
            unique.append(period)
            positioned.append((position, period))
    return positioned


def _periods_in(sentence: str) -> list[_Period]:
    """Every reporting period the sentence names, in reading order."""
    return [period for _, period in _periods_with_positions(sentence)]


_AMOUNT_ENDS: dict[tuple[int, int], int] = {}


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
            # United Therapeutics reported Remodulin's first quarter on sale as
            # "$205,000". Without this the figure is invisible and the quarter
            # looks unreported rather than small.
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
        after = (match.group("currency_after") or "").lower()
        if after:
            after = re.sub(r"\s+", " ", after)
            currency = _CURRENCY_WORDS.get(after, after.upper() if len(after) == 3 else currency)
        amounts.append(
            (match.start(), (float(raw_amount.replace(",", "")), unit, currency))
        )
        _AMOUNT_ENDS[(id(sentence), match.start())] = match.end()
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


# A figure that is a condition or a threshold, not a statement of what was
# earned: "if annual net sales exceed $25.0 million", "a milestone of $10 million".
_CONDITIONAL_RE = re.compile(
    r"\b(?:if|exceeds?|exceeding|in\s+excess\s+of|threshold|milestone|royalt\w*|would|could|may|might|"
    r"expects?\s+to|anticipat\w*|up\s+to|at\s+least|guidance|target\w*|forecast\w*|project\w*)\b",
    re.IGNORECASE,
)


def _is_change_amount(sentence: str, position: int, end: int) -> bool:
    before = sentence[max(0, position - 40) : position]
    after = sentence[end : end + 30]
    return bool(_CHANGE_BEFORE_RE.search(before) or _CHANGE_AFTER_RE.match(after))


def _is_conditional(sentence: str, position: int) -> bool:
    """The amount sits in a clause that states a condition rather than a result."""
    clause_start = max(sentence.rfind(",", 0, position), sentence.rfind(";", 0, position), 0)
    window = sentence[max(clause_start, position - 80) : position]
    return bool(_CONDITIONAL_RE.search(window))


def _product_mentions(sentence: str, aliases: list[str]) -> list[tuple[int, int]]:
    """Spans where the product is named as a subject, not set aside or listed."""
    lowered = sentence.lower()
    spans: list[tuple[int, int]] = []
    for alias in aliases:
        for match in re.finditer(re.escape(alias), lowered):
            before = lowered[max(0, match.start() - 30) : match.start()]
            if _EXCLUDED_BEFORE_RE.search(before):
                continue
            # Inside a parenthesised list of products.
            open_paren = lowered.rfind("(", 0, match.start())
            close_paren = lowered.find(")", match.end())
            if open_paren != -1 and close_paren != -1 and "," in lowered[open_paren:close_paren]:
                continue
            spans.append(match.span())
    return spans


_SUBJECT_RE = re.compile(_REVENUE_NOUN, re.IGNORECASE)


def _subject_names_product(sentence: str, position: int, aliases: list[str]) -> bool | None:
    """Does the revenue phrase governing the amount at ``position`` name the product?

    "Total Tyvaso revenues decreased to $457.5 million ... driven by nebulized
    Tyvaso" ties the amount to Total Tyvaso, whatever the sentence says
    later. The governing phrase is the last revenue noun before the amount
    in the same clause; None when there is none to judge by.
    """
    clause_start = max(sentence.rfind(";", 0, position), 0)
    before = sentence[clause_start:position]
    nouns = list(_SUBJECT_RE.finditer(before))
    if not nouns:
        return None
    noun = nouns[-1]
    window = before[max(0, noun.start() - 60) : noun.end() + 40].lower()
    return any(alias in window for alias in aliases)


def _revenue_tied(sentence: str, aliases: list[str]) -> bool:
    """True when a revenue noun is attached to the product's name.

    "Remodulin revenues", "sales of Remodulin", "revenues from Remodulin",
    "Tyvaso net product sales" all tie the figure to the product's own
    revenue line. "sold $8.5 million of Remodulin to distributors" does not:
    it is a statement about shipments, and is kept but ranked behind.
    """
    lowered = sentence.lower()
    for alias in aliases:
        escaped = re.escape(alias)
        if re.search(rf"{_REVENUE_NOUN}\s+(?:of|from|for|on)\s+(?:the\s+)?{escaped}", lowered):
            return True
        if re.search(rf"{escaped}(?:['’]s)?\s+(?:\w+\s+){{0,2}}{_REVENUE_NOUN}", lowered):
            return True
        if re.search(rf"{_REVENUE_NOUN}\s+(?:\w+\s+){{0,3}}(?:of|from|for)\s+(?:the\s+)?{escaped}", lowered):
            return True
    return False


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
    inferred. A sentence that states an amount for the product but no period
    takes the period its paragraph established, the way MD&A prose does
    ("Revenues for the three months ended June 30, 2002 were ... The increase
    was due primarily to $8.7 million in sales of Remodulin"), and is marked
    as having done so.
    """
    aliases = [alias.lower() for alias in product_aliases(product, generic, extra=extra_aliases)]
    # A sentence about "treprostinil-based products" is about the molecule's
    # family, not this brand; the generic name alone does not name the product.
    if generic and generic.strip().lower() != product.strip().lower():
        aliases = [a for a in aliases if a != generic.strip().lower()] or aliases
    values: list[ExtractedValue] = []

    for paragraph in _PARAGRAPH_SPLIT_RE.split(text or ""):
        paragraph_period: _Period | None = None
        for sentence in _SENTENCE_SPLIT_RE.split(paragraph):
            located_periods = _periods_with_positions(sentence)
            # The paragraph's current period is the most recent one it has
            # named so far; a comparative sentence names the current period
            # first and the prior one after "compared to".
            if located_periods:
                quarterly = [p for _, p in located_periods if p.period_type == "quarterly"]
                candidates = quarterly or [p for _, p in located_periods]
                paragraph_period = max(candidates, key=lambda p: p.period)
            if not _product_mentions(sentence, aliases):
                continue
            # A sentence that never says "sales" or "revenue" is not stating
            # revenue, whatever product it names: expenses, milestones and
            # inventory all come with dollar amounts and periods too.
            if not re.search(_REVENUE_NOUN, sentence, re.IGNORECASE):
                continue
            located_amounts = [
                (position, amount)
                for position, amount in _amounts_with_positions(sentence)
                if not _is_change_amount(sentence, position, _AMOUNT_ENDS.get((id(sentence), position), position))
                and not _is_conditional(sentence, position)
            ]
            periods = [period for _, period in located_periods]
            amounts = [amount for _, amount in located_amounts]
            inherited = False
            if not periods and len(amounts) == 1 and paragraph_period is not None and _revenue_tied(sentence, aliases):
                periods = [paragraph_period]
                located_periods = [(0, paragraph_period)]
                inherited = True
            if not _revenue_tied(sentence, aliases):
                # The revenue noun belongs to something else ("Total product
                # sales excluding X", "Other product sales, which include X"):
                # the amount is not this product's.
                continue
            located_amounts = [
                (position, amount)
                for position, amount in located_amounts
                if _subject_names_product(sentence, position, aliases) is not False
            ]
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
            tied = _revenue_tied(sentence, aliases)
            for index, (period, (amount, unit, currency)) in enumerate(pairs):
                values.append(
                    ExtractedValue(
                        product_label=product,
                        period=period.period,
                        period_type=period.period_type,
                        value_as_reported=amount,
                        unit_label=unit,
                        currency=currency,
                        source_quote=sentence.strip(),
                        fingerprint_signature="prose_inherited_period" if inherited else "prose",
                        value_index=index,
                        specificity=0 if not inherited else 1,
                    )
                )
    return values
