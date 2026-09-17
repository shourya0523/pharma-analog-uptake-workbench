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
from app.parsing.periods import dates_named, period_key, period_span, periods_named
from app.quality.candidate_filters import (
    _AGGREGATE_WORDS,
    _QUALIFIER_WORDS,
    _is_spelling_variant,
)

# A footnote marker as a filer prints it beside a label or at the start of the
# note: "(1)", "1)", "*", "**", "+", daggers. Superscript digits arrive as
# plain digits from the HTML parser, so a bare trailing digit is not read as a
# marker - "Calderon 2" would be a strength, not a note.
#
# A plus is both a marker and the joiner a filer writes between two brands it
# sells as one therapy, and this runs before the joiner split, so reading it
# as a marker everywhere erased the joiner: "Calderon + NuVessa" arrived as
# "Calderon NuVessa", one name with a word of residue after it, and the line
# was never seen to combine anything. A marker attaches to the thing it marks
# and is followed by the row's numbers or by nothing; a joiner stands between
# two names. So a plus followed by a letter is the joiner and is left alone.
_MARK_RE = re.compile(r"\((\d{1,2})\)|(?<![\w)])(\d{1,2})\)|(\*+|\+(?!\s*[A-Za-z])|†|‡)")
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

# The footnote says the figure covers less than the period without dating it:
# "the two months that we owned the product". A note that does date it is read
# from its dates instead, by `_dates_part_of_the_period` - which is why
# "launch" is excluded here and not there. An undated note naming a launch is
# a note about a product that has been on sale all period; a dated one puts
# the first day of selling inside the period and says so outright.
_PARTIAL_RE = re.compile(
    r"\b(?:acqui(?:red|sition)|period between|from the date of|"
    r"following the (?:closing|completion)|since (?:the )?(?:closing|completion)|"
    r"months? that we owned|weeks? that we owned)\b",
    re.IGNORECASE,
)
# The claim a note makes about what a line contains. Tested on its own, and
# the product's name tested on its own, "does not include sales of NuVessa"
# reads as a note saying the line contains NuVessa - the opposite of what it
# says. So the claim and its subject are carried by one pattern, as
# `_no_sales_of` carries them: the claim word may not be negated, and nothing
# that negates it may stand between the claim and the name.
_INCLUDES_CLAIM = r"includes?|including|consists?\s+of|comprises?|comprised\s+of"
_NEGATES = r"\b(?:not|never|excludes?|excluding|exclusive\s+of|other\s+than|without)\b"

# The note says the line is not this product's at all: "there were no sales
# of NuVessa in the quarter" under "Calderon and NuVessa (1)".
FLAG_NO_SALES = "footnote_says_no_sales"

# A note states its claim in one clause and the reason for it in another:
# "there were no sales of NuVessa during the quarters ended March 31, 2026 and
# June 30, 2026, as the issuer moved promotion to Calderon during the second
# quarter of 2025". Only the first clause says which of the row's figures the
# claim is about; the period in the second is part of the reason. So the
# clause carrying the claim is cut out before the periods in it are read.
#
# The words below are the subordinators and relativisers English starts a new
# clause with. It is a closed class of the language, not a list of anything in
# the data, so nothing in a filing makes it stale.
_CLAUSE_BREAK_RE = re.compile(
    r"[;:]|\b(?:as|because|since|while|whilst|when|whenever|after|before|until|"
    r"although|though|whereas|which|who|whom|whose|unless|if)\b",
    re.IGNORECASE,
)

FLAG_NOT_UNDERSTOOD = "label_not_understood"
FLAG_COMBINED = "combined_line"
FLAG_PARTIAL = "partial_period"
FLAG_FAMILY_INCLUDES = "family_line_includes_product"

# The flags that make a row a question about the product rather than an answer
# for it: the reader published something, but what it published is not the
# product's own figure for that period without a person looking. Owned here,
# beside the flags themselves, so a flag added to this vocabulary is not a
# question by accident in one module and an answer in another.
QUESTION_FLAGS = frozenset(
    {FLAG_NOT_UNDERSTOOD, FLAG_PARTIAL, FLAG_COMBINED, FLAG_NO_SALES}
)


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


# A name the filer marks as its own, read before the marks are stripped. The
# products list can only answer for brands somebody tracks, and the sibling in
# a co-administered pair is routinely one nobody does - so the line read as
# this product's own. The filer's mark says it is a brand without a list.
_TRADEMARKED_NAME_RE = re.compile(r"([A-Za-z][A-Za-z0-9\-]{2,})\s*[\u00ae\u2122]")


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
    marked = _TRADEMARKED_NAME_RE.findall(label or "")
    others = [
        p
        for p in [*products, *marked]
        if p and _joined(p) not in own_keys
    ]
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
            bare = [w for w in words if w not in _QUALIFIER_WORDS and w not in _PLURAL_QUALIFIERS]
            # The whole piece is another product's name: a combined line. The
            # name is looked for with the qualifiers around it and without
            # them, because a filer writes the joined name with the noun it
            # shares: "Total Calderon + NuVessa sales" hangs "sales" off the
            # second name, and that word alone made the piece match nothing.
            other = (
                _names_one_of(core, others)
                or _names_one_of(core, sibling_names)
                or _names_one_of(" ".join(bare), others)
                or _names_one_of(" ".join(bare), sibling_names)
            )
            if other and _joined(other) not in own_keys:
                if other not in combined:
                    combined.append(other)
                continue
            # The whole piece is the product's own name, or qualifiers around it.
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
    # The periods the note qualifies, where it names any - a note naming
    # several ("the quarters ended March 31, 2026 and June 30, 2026") is about
    # all of them.
    periods: frozenset[str] = frozenset()
    no_sales_of: tuple[str, ...] = ()  # products the note says had no sales

    @property
    def states_a_scope(self) -> bool:
        """Whether the note says which of the row's figures it is about.

        A note that does not is read as being about the whole row, which is
        the right answer for "includes Nebulized Calderon" and a guess for
        anything that qualifies one column. A caller that drops a figure on
        what a note says can ask for the difference; one that only attaches
        the note's prose to a quote does not need to.
        """
        return bool(self.periods) or self.months is not None

    def applies_to(self, months: int, period: str) -> bool:
        """Whether a figure of this span and period is what the note is about.

        A note stating no scope at all is about the whole row.
        """
        if self.periods:
            return period_key(period, months) in self.periods
        if self.months is not None:
            return months == self.months
        return True


def _claim_clause(note: str, claim_at: int) -> str:
    """The clause of the note that carries a claim beginning at ``claim_at``."""
    start = 0
    for match in _CLAUSE_BREAK_RE.finditer(note, 0, claim_at):
        start = match.end()
    end = next(
        (match.start() for match in _CLAUSE_BREAK_RE.finditer(note, claim_at)), len(note)
    )
    return note[start:end]


def _note_scope(note: str, claim_at: int | None) -> tuple[int | None, frozenset[str]]:
    """The span and the periods the note says its claim is about.

    Where the note carries a claim, only the claim's own clause is read: the
    reason a filer gives for a claim names periods of its own, and those are
    about the reason. A claim whose clause names no period is about the whole
    row, and is not rescued by a period elsewhere in the note.
    """
    text = _claim_clause(note, claim_at) if claim_at is not None else note
    named = periods_named(text)
    periods = frozenset(period.key for period in named if period.key)
    months = next((period.months for period in named), None)
    return months, periods


def _includes(note: str, names: Iterable[str]) -> tuple[tuple[str, ...], int | None]:
    """The products the note says the line includes, and where it first says so.

    ``includes Nebulized Calderon`` names Nebulized Calderon; ``does not
    include sales of Nebulized Calderon`` names nobody.
    """
    found: list[str] = []
    at: int | None = None
    for name in names:
        if not name:
            continue
        match = re.search(
            rf"(?<!not\s)(?<!never\s)\b(?:{_INCLUDES_CLAIM})\b"
            rf"(?:(?!{_NEGATES})[^.;]){{0,60}}?\b{re.escape(name)}\b",
            note, re.IGNORECASE,
        )
        if match:
            found.append(name)
            at = match.start() if at is None else min(at, match.start())
    return tuple(found), at


def _no_sales_of(note: str, names: Iterable[str]) -> tuple[tuple[str, ...], int | None]:
    """The products the note says had no sales, and where it first says so."""
    found: list[str] = []
    at: int | None = None
    for name in names:
        if not name:
            continue
        escaped = re.escape(name)
        match = re.search(
            rf"\bno\s+(?:net\s+)?(?:product\s+)?(?:sales|revenues?)\s+(?:of|from|for)\s+{escaped}\b"
            rf"|\b{escaped}\s+(?:had|generated|recorded|reported)\s+no\s+(?:net\s+)?(?:sales|revenues?)\b",
            note, re.IGNORECASE,
        )
        if match:
            found.append(name)
            at = match.start() if at is None else min(at, match.start())
    return tuple(found), at


def _dates_part_of_the_period(
    note: str, months: int | None, periods: Iterable[str]
) -> bool:
    """Whether the note's own dates put the figure inside the period it is about.

    A note that dates a figure from an event names the day the figure starts
    at - a closing, a launch - and a row's period starts at its own first day.
    So a date the note writes out that falls strictly inside the period the
    note is about says the figure covers less of it than the heading does.
    A date that is the period's own end names the heading, not a boundary
    inside it.
    """
    if months is None:
        return False
    named = dates_named(note)
    if not named:
        return False
    for key in periods:
        span = period_span(key, months)
        if span is None:
            continue
        start, end = span
        if any(start < day < end for day in named):
            return True
    return False


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
    others: list[str] = []
    for name in [*products, *siblings]:
        key = _joined(name)
        if key and key not in own_keys and name not in others:
            others.append(name)
    names, includes_at = _includes(note, others)
    partial = _PARTIAL_RE.search(note)
    none_sold, sold_at = _no_sales_of(note, [*own, *products, *siblings])
    # Where the note makes a claim, its scope is the claim's; where it makes
    # two, the first one's clause is where the note starts saying something.
    claims = [
        at for at in (sold_at, includes_at, partial.start() if partial else None)
        if at is not None
    ]
    months, periods = _note_scope(note, min(claims) if claims else None)
    flags: list[str] = []
    undated = partial and not re.search(r"\blaunch", note, re.IGNORECASE)
    if undated or _dates_part_of_the_period(note, months, periods):
        flags.append(FLAG_PARTIAL)
    if any(_joined(n) in own_keys for n in none_sold):
        flags.append(FLAG_NO_SALES)
    return NoteReading(names, tuple(flags), months, periods, none_sold)


# How a note travels with the figure it is about. The table reader writes the
# marker and the note after the row it carried them out of, so whoever reads
# the quote reads what the filer footnoted; read back out, the note is a field
# of its own rather than trailing text a reader has to notice. One producer
# and one reader, so the two can never be written differently.
_CITED_NOTE_RE = re.compile(r" \[\(([^)]{1,8})\) ([^\]]+)\]")


def cite_footnote(mark: str, note: str) -> str:
    """The note, written so it travels on the end of the quote it is about."""
    return f" [({mark}) {note}]"


def footnotes_in(quote: str) -> list[str]:
    """The notes a quote carries, in the order they were attached to it."""
    return [note for _mark, note in _CITED_NOTE_RE.findall(quote or "")]


def names_product(note: str, aliases: Iterable[str]) -> bool:
    return any(
        re.search(rf"\b{re.escape(a)}\b", note or "", re.IGNORECASE) for a in aliases if a
    )
