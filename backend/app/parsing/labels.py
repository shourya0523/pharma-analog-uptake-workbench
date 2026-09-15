"""What a row label says beyond the product's name.

A label that names the product is not therefore the product's own row.
``Calderon - Europe`` is one region of it, ``Total Calderon`` is the family,
``Calderon / Calderon XR / NuVessa`` is a combined line over three products,
``Calderon: Raw materials`` is an inventory line, and ``Accrual for settlement
related to calderinol litigation`` is nothing to do with sales at all. Every
one of those contains the product's name, and a reader that stops at "the
name is in there" publishes the last two as revenue.

So the label is read by its residue: strip the alias that matched, the scope
tokens, the qualifiers a filer prints around a name ("net product sales",
"total"), trademark and footnote marks, and the names of the other products
the table or the pipeline knows, and look at what is left.

    nothing left                  the product's own row, in the scope the
                                  tokens gave
    only other product names      a combined line over those products
    anything else                 words the reader cannot account for; the
                                  row is a question, not an answer

The footnote a label cites is read too, because the label alone can lie by
omission: ``Calderon (1)`` with "(1) includes Nebulized Calderon" beneath the
table is a combined line, and "(1) for the period between the acquisition
date and quarter end" makes the figure a partial period rather than the
quarter's.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.parsing.evidence import SCOPE_PATTERNS
from app.quality.candidate_filters import (
    _AGGREGATE_WORDS,
    _QUALIFIER_WORDS,
    _is_spelling_variant,
)

# A footnote marker as a filer prints it beside a label or at the start of the
# note: "(1)", "1)", "*", "**", "+", daggers. Superscript digits arrive as
# plain digits from the HTML parser, so a bare trailing digit is not read as a
# marker - "Calderon 2" would be a strength, not a note.
_MARK_RE = re.compile(r"\((\d{1,2})\)|(?<![\w)])(\d{1,2})\)|(\*+|\+|†|‡)")
_FOOTNOTE_LINE_RE = re.compile(r"^\s*(?:\((\d{1,2})\)|(\d{1,2})\)|(\*+|\+|†|‡))\s*(.+?)\s*$")
_TRADEMARK_RE = re.compile(r"[®™©]")
# Names are joined on one line by these. A hyphen is not among them: "Calderon
# - Europe" is one product under a geography. It is a separator between the
# name and what qualifies it, and is split on below as such.
_JOINER_SPLIT_RE = re.compile(r"\s*(/|\+|&|;|,|\band\b|\bwith\b|\bplus\b)\s*", re.IGNORECASE)
# "products" after a name is how a filer writes a family line under its
# generic ("Tiopronin products"); it qualifies the name rather than widening
# it. "Other products" widens it, and "other" is what says so.
_PLURAL_QUALIFIERS = frozenset({"products"})
_SEPARATOR_RE = re.compile(r"\s+[-–—]\s+|:\s*|\s+[-–—]\s*$")
_WORD_RE = re.compile(r"[\w'.&-]+")
_TOTAL_WORDS = frozenset({"total", "totals", "subtotal"})

# Which of the scope vocabulary's labels is the whole-product figure.
_WHOLE = frozenset({"Worldwide"})
_SCOPE_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE)) for label, pattern in SCOPE_PATTERNS
)

# The footnote says the figure covers less than the period: an acquisition
# closed inside it. A launch inside the period is different - the product's
# whole revenue for the period is what it sold since launch - so "launch" is
# not in this pattern and a note naming one is not partial.
_PARTIAL_RE = re.compile(
    r"\b(?:acqui(?:red|sition)|period between|from the date of|"
    r"following the (?:closing|completion)|since (?:the )?(?:closing|completion)|"
    r"months? that we owned|weeks? that we owned)\b",
    re.IGNORECASE,
)
_INCLUDES_RE = re.compile(r"\b(?:includes?|including|consists? of|comprises?|comprised of)\b", re.IGNORECASE)

# The note says the line is not this product's at all: "there were no sales
# of NuVessa in the quarter" under "Calderon and NuVessa (1)".
FLAG_NO_SALES = "footnote_says_no_sales"

# What span or period a note qualifies, where it names one: "for the six
# months ended June 30, 2023 is for the period between ..." is about the
# six-month column and says nothing about the quarter beside it.
_NOTE_SPAN_RE = re.compile(
    r"\b(three|six|nine|twelve)\s+months\s+ended\b|\b(?:fiscal\s+)?year\s+ended\b",
    re.IGNORECASE,
)
_NOTE_PERIOD_RE = re.compile(
    r"\bQ([1-4])\s*'?\s*((?:19|20)\d{2}|\d{2})(?!\d)"
    r"|\b([1-4])Q\s*'?\s*((?:19|20)\d{2}|\d{2})(?!\d)"
    r"|\b(first|second|third|fourth)\s+quarter\s+(?:of\s+)?((?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_SPAN_MONTHS = {"three": 3, "six": 6, "nine": 9, "twelve": 12}
_QUARTER_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}

FLAG_NOT_UNDERSTOOD = "label_not_understood"
FLAG_COMBINED = "combined_line"
FLAG_PARTIAL = "partial_period"
FLAG_FAMILY_INCLUDES = "family_line_includes_product"

# The flags that make a row a question about the product rather than an answer
# for it: the reader published something, but what it published is not the
# product's own figure for that period without a person looking. Owned here,
# beside the flags themselves, so a flag added to this vocabulary is not a
# question by accident in one module and an answer in another.
QUESTION_FLAGS = frozenset({FLAG_NOT_UNDERSTOOD, FLAG_PARTIAL, FLAG_COMBINED})


def _words(text: str) -> list[str]:
    return [w.strip(".'-&") for w in _WORD_RE.findall(text.lower()) if w.strip(".'-&")]


def _joined(text: str) -> str:
    return "".join(_words(text))


@dataclass(frozen=True)
class LabelReading:
    """One label, accounted for word by word."""

    label: str
    matched: str | None
    scope: str | None
    is_total: bool
    combined_with: tuple[str, ...]
    residue: str
    marks: tuple[str, ...]
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def names_product(self) -> bool:
        return self.matched is not None

    @property
    def understood(self) -> bool:
        return not self.residue

    @property
    def whole(self) -> bool:
        """The family figure: no region, no residue, not a combined line."""
        return (
            self.names_product
            and self.understood
            and not self.combined_with
            and (self.scope is None or self.scope in _WHOLE)
        )


def footnote_marks(label: str) -> tuple[str, ...]:
    """The footnote markers a label carries, as the notes beneath key them."""
    marks: list[str] = []
    for match in _MARK_RE.finditer(label or ""):
        mark = match.group(1) or match.group(2) or match.group(3)
        if mark and mark not in marks:
            marks.append(mark)
    return tuple(marks)


def footnotes_by_mark(footnotes: Iterable[str] | None) -> dict[str, str]:
    """The notes printed under a table, keyed by the marker each begins with."""
    keyed: dict[str, str] = {}
    for line in footnotes or ():
        match = _FOOTNOTE_LINE_RE.match(line or "")
        if not match:
            continue
        mark = match.group(1) or match.group(2) or match.group(3)
        keyed.setdefault(mark, match.group(4))
    return keyed


def _scope_of(text: str) -> tuple[str | None, str]:
    """(scope label, the text with the scope tokens removed)."""
    found: str | None = None
    for label, pattern in _SCOPE_RES:
        if pattern.search(text):
            found = found or label
            text = pattern.sub(" ", text)
    return found, text


def _names_one_of(part: str, names: Iterable[str]) -> str | None:
    """The name in ``names`` this part is, whole words only, longest first."""
    key = _joined(part)
    if not key:
        return None
    for name in sorted(names, key=lambda n: -len(n)):
        if key == _joined(name):
            return name
    return None


def read_label(
    label: str,
    aliases: Iterable[str],
    *,
    products: Iterable[str] = (),
    siblings: Iterable[str] = (),
) -> LabelReading:
    """Account for every word of a label.

    ``aliases`` are the names the asked product goes by; ``products`` are the
    other products the pipeline tracks or this run was asked about; ``siblings``
    are the labels of the other rows in the same table, which is the filer's
    own list of what it reports.
    """
    marks = footnote_marks(label)
    text = _MARK_RE.sub(" ", _TRADEMARK_RE.sub(" ", label or ""))
    own = [a for a in aliases if a]
    own_keys = {_joined(a) for a in own if _joined(a)}
    others = [p for p in products if p and _joined(p) not in own_keys]
    sibling_names = [s for s in siblings if s and _joined(s) not in own_keys]

    matched: str | None = None
    scope: str | None = None
    is_total = False
    combined: list[str] = []
    residue: list[str] = []

    # Each part with the joiner that introduced it, so a slash can be told
    # from an "and": the first joins the names of one product, the second
    # joins two products.
    pieces = _JOINER_SPLIT_RE.split(text)
    parts = [("", pieces[0])] + [
        (pieces[i], pieces[i + 1]) for i in range(1, len(pieces) - 1, 2)
    ]
    for joiner, part in ((j, p) for j, p in parts if p and p.strip()):
        for piece in (p for p in _SEPARATOR_RE.split(part) if p and p.strip()):
            piece_scope, stripped = _scope_of(piece)
            if piece_scope:
                scope = scope or piece_scope
            words = _words(stripped)
            if any(w in _TOTAL_WORDS for w in words):
                is_total = True
                words = [w for w in words if w not in _TOTAL_WORDS]
            core = " ".join(words)
            if not core:
                continue
            # The whole piece is another product's name: a combined line.
            other = _names_one_of(core, others) or _names_one_of(core, sibling_names)
            if other and _joined(other) not in own_keys:
                if other not in combined:
                    combined.append(other)
                continue
            # The whole piece is the product's own name, or qualifiers around it.
            bare = [w for w in words if w not in _QUALIFIER_WORDS and w not in _PLURAL_QUALIFIERS]
            if not bare:
                continue
            piece_key = "".join(bare)
            hit = next((a for a in own if _joined(a) == piece_key), None)
            if hit is not None:
                matched = matched or hit
                continue
            # The name inside a longer piece: the words around it are the
            # residue - "raw materials", "accrual settlement litigation".
            # Joined by a slash to the name itself, the longer piece is one
            # of the product's own presentations ("Calderon / Calderon XR"),
            # and the line is the family's.
            inner = next((a for a in sorted(own, key=lambda a: -len(a))
                          if _joined(a) and _joined(a) in piece_key), None)
            if inner is not None and joiner.strip() == "/" and matched is not None:
                continue
            if inner is not None:
                matched = matched or inner
                rest = list(bare)
                for word in _words(inner):
                    if word in rest:
                        rest.remove(word)
                residue.extend(rest)
                continue
            if _is_spelling_variant(core, set(own_keys)):
                matched = matched or own[0]
                continue
            # "Other" after a separator is the other-regions slice of a
            # block ("Calderon - U.S.", "- Europe", "- Other"). Joined to the
            # name ("Calderon and other products") it widens the line.
            if bare == ["other"] and not joiner.strip():
                scope = scope or "Other"
                continue
            if all(w in _AGGREGATE_WORDS for w in bare):
                residue.extend(bare)
                continue
            # A slash joins the names of one product: a brand and its generic,
            # or the name it carries in another market. Unknown to this reader,
            # such a name is tolerated beside a name it does know. Joined any
            # other way, or standing alone, an unknown name is unaccounted for.
            if joiner.strip() == "/" and matched is not None:
                continue
            residue.extend(bare)

    flags: list[str] = []
    if matched is not None and residue:
        flags.append(FLAG_NOT_UNDERSTOOD)
    if matched is not None and combined:
        flags.append(FLAG_COMBINED)
    return LabelReading(
        label=label,
        matched=matched,
        scope=scope,
        is_total=is_total,
        combined_with=tuple(combined),
        residue=" ".join(residue),
        marks=marks,
        flags=tuple(flags),
    )


@dataclass(frozen=True)
class NoteReading:
    """What a footnote says about the row that cites it, and about which of
    the row's figures it says it."""

    names: tuple[str, ...]      # other products the note says the line includes
    flags: tuple[str, ...]
    months: int | None = None   # the span the note qualifies, where it names one
    period: str | None = None   # the period the note qualifies, where it names one
    no_sales_of: tuple[str, ...] = ()  # products the note says had no sales

    def applies_to(self, months: int, period: str) -> bool:
        """Whether a figure of this span and period is what the note is about.

        A note naming neither is about the whole row.
        """
        if self.period is not None:
            return period == self.period
        if self.months is not None:
            return months == self.months
        return True


def _note_scope(note: str) -> tuple[int | None, str | None]:
    period = _NOTE_PERIOD_RE.search(note)
    if period:
        if period.group(5):
            quarter, year = _QUARTER_WORDS[period.group(5).lower()], int(period.group(6))
        else:
            quarter = int(period.group(1) or period.group(3))
            digits = period.group(2) or period.group(4)
            year = int(digits) if len(digits) == 4 else 2000 + int(digits)
        return None, f"{year}Q{quarter}"
    span = _NOTE_SPAN_RE.search(note)
    if span:
        return (_SPAN_MONTHS[span.group(1).lower()] if span.group(1) else 12), None
    return None, None


def _no_sales_of(note: str, names: Iterable[str]) -> tuple[str, ...]:
    found: list[str] = []
    for name in names:
        if not name:
            continue
        escaped = re.escape(name)
        if re.search(
            rf"\bno\s+(?:net\s+)?(?:product\s+)?(?:sales|revenues?)\s+(?:of|from|for)\s+{escaped}\b"
            rf"|\b{escaped}\s+(?:had|generated|recorded|reported)\s+no\s+(?:net\s+)?(?:sales|revenues?)\b",
            note, re.IGNORECASE,
        ):
            found.append(name)
    return tuple(found)


def read_footnote(
    note: str,
    aliases: Iterable[str],
    *,
    products: Iterable[str] = (),
    siblings: Iterable[str] = (),
) -> NoteReading:
    """What a footnote says about the row that cites it.

    A note that says the line includes a sibling makes it a combined line. A
    note that says the period began inside the quarter makes the figure a
    partial period - of the span or period the note names, where it names
    one, and of the whole row otherwise. A note that says the asked product
    had no sales makes the line not the product's. A note naming the asked
    product under another product's label is read by the caller, which knows
    whose label it is.
    """
    note = note or ""
    own = [a for a in aliases if a]
    own_keys = {_joined(a) for a in own if _joined(a)}
    names: list[str] = []
    if _INCLUDES_RE.search(note):
        for name in [*products, *siblings]:
            key = _joined(name)
            if not key or key in own_keys:
                continue
            if re.search(rf"\b{re.escape(name)}\b", note, re.IGNORECASE) and name not in names:
                names.append(name)
    flags: list[str] = []
    if _PARTIAL_RE.search(note) and not re.search(r"\blaunch", note, re.IGNORECASE):
        flags.append(FLAG_PARTIAL)
    none_sold = _no_sales_of(note, [*own, *products, *siblings])
    if any(_joined(n) in own_keys for n in none_sold):
        flags.append(FLAG_NO_SALES)
    months, period = _note_scope(note)
    return NoteReading(tuple(names), tuple(flags), months, period, none_sold)


def names_product(note: str, aliases: Iterable[str]) -> bool:
    return any(
        re.search(rf"\b{re.escape(a)}\b", note or "", re.IGNORECASE) for a in aliases if a
    )
