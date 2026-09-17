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

A table is not prose, and HTML-to-text prints each of its cells on its own
line. The unit a table states is the row - the label and the figures beside it
- so a line that states no word at all continues the row whose label opened it,
and only a line that says something starts a new unit:

    Calderon XR / $ / 34,974 / $ / 22,209   is one unit, not five
    NuVessa / 12,088 / 9,401                is the next
"""

from __future__ import annotations

import re

from app.quality.checks import quote_contains_value

# A line break or a bullet mark ends a line - a release's bullets are lines,
# not sentences, and carry no full stop at all.
_LINE_BREAK_RE = re.compile(r"\s*\n+\s*|\s*[•▪◦·]\s*")
# Within a line, a full stop, a question or exclamation mark, or a semicolon
# ends a sentence.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?;])[\"'”’)\]]*\s+")
# A line that states a word. A table's figure cells - "$", "34,974", "7", "%",
# "(1)", "2024" - state none, and belong to the row the last such line opened.
_STATES_A_WORD_RE = re.compile(r"[^\W\d_]")

# An amount governed by "increased ... by" / "decreased ... by" / "grew by" is
# the size of a move. The level, where the sentence states one, follows "to".
_CHANGE_BY_RE = re.compile(
    r"\b(?:increas|decreas|grew|grow|declin|ros|rise|fell|fall|drop|up|down|improv|"
    r"reduc)\w*\b[^.;]{0,80}?\bby\s+(?:approximately\s+|about\s+|roughly\s+)?"
    r"(?:US)?\$?\s*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand)?",
    re.IGNORECASE,
)
_TO_LEVEL_RE = re.compile(r"\bto\s+(?:approximately\s+|about\s+)?(?:US)?\$\s*[\d,]+", re.IGNORECASE)


def _lines(quote: str) -> list[str]:
    """The quote's lines, with each wordless line joined onto the one it follows.

    A quote lifted out of a table arrives one cell per line, and its figures
    are not statements of their own; joined back onto the label above them they
    are the row the filer printed. A quote lifted out of prose has a word on
    every line and is unchanged.
    """
    lines: list[str] = []
    for raw in _LINE_BREAK_RE.split(quote or ""):
        line = raw.strip()
        if not line:
            continue
        if lines and not _STATES_A_WORD_RE.search(line):
            lines[-1] = f"{lines[-1]} {line}"
        else:
            lines.append(line)
    return lines


def sentences(quote: str) -> list[str]:
    """The sentences a quote is made of, in order, empty ones dropped.

    A table row is one of them, however many lines its cells were printed on.
    """
    return [
        part.strip()
        for line in _lines(quote)
        for part in _SENTENCE_END_RE.split(line)
        if part and part.strip()
    ]


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
