"""A quote is a sentence.

A candidate's quote is what a reader is shown to check the figure against,
and a block of a release's headline bullets is not that: the product sits in
one bullet and a hundred-million-dollar financing facility in another, and a
judge holding the block against the figure finds both and passes it. So a
multi-sentence quote is split, and the sentence carrying the value must also
carry the product, or the candidate is not grounded.

The same sentence can say two things about an amount. "Revenue increased by
$12.3 million" states how much revenue moved; "increased by $12.3 million to
$61.2 million" states the level as well. Only the level is the quarter's
revenue.
"""

from __future__ import annotations

import re

from app.quality.checks import quote_contains_value

# Where one sentence ends and the next begins: a full stop, a question or
# exclamation mark, a semicolon, or a line break - a release's bullets are
# lines, not sentences, and carry no full stop at all.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?;])[\"'”’)\]]*\s+|\s*\n+\s*|\s*[•▪◦·]\s*")

# An amount governed by "increased ... by" / "decreased ... by" / "grew by" is
# the size of a move. The level, where the sentence states one, follows "to".
_CHANGE_BY_RE = re.compile(
    r"\b(?:increas|decreas|grew|grow|declin|ros|rise|fell|fall|drop|up|down|improv|"
    r"reduc)\w*\b[^.;]{0,80}?\bby\s+(?:approximately\s+|about\s+|roughly\s+)?"
    r"(?:US)?\$?\s*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand)?",
    re.IGNORECASE,
)
_TO_LEVEL_RE = re.compile(r"\bto\s+(?:approximately\s+|about\s+)?(?:US)?\$\s*[\d,]+", re.IGNORECASE)


def sentences(quote: str) -> list[str]:
    """The sentences a quote is made of, in order, empty ones dropped."""
    return [part.strip() for part in _SENTENCE_END_RE.split(quote or "") if part and part.strip()]


def sentence_carrying(quote: str, value: float | None) -> str | None:
    """The sentence of the quote that states the value, or None if none does."""
    for sentence in sentences(quote):
        if quote_contains_value(sentence, value):
            return sentence
    return None


def states_a_change_not_a_level(sentence: str, value: float | None) -> bool:
    """Whether the value in this sentence is a move in revenue, not revenue.

    True when the amount follows "increased ... by" (or a synonym) and the
    sentence states no level after "to". A sentence stating both - "increased
    by $12.3 million to $61.2 million" - is read for the level it states, and
    the candidate carrying 12.3 is still the change.
    """
    if value is None:
        return False
    for match in _CHANGE_BY_RE.finditer(sentence or ""):
        if quote_contains_value(match.group(0), value):
            after = sentence[match.end():]
            level = _TO_LEVEL_RE.search(after)
            return level is None or not quote_contains_value(level.group(0), value)
    return False
