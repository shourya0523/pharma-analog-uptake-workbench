"""Stage 2: read values out of a fingerprinted table.

Extraction here is deterministic and refuses to guess. If a product row's
numbers cannot be aligned to the fingerprint's period columns with confidence,
the row yields nothing and records why, rather than emitting a value attributed
to a period it may not belong to.

That rule exists because the alignment step is where a wrong number looks most
right. A schedule drops a "-" where a prior-year comparative belongs, and
anything that silently shifts the remaining numbers left then books a full-year
total as a fourth-quarter figure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # a type only; periods.py must not import this module back
    from app.parsing.periods import PeriodContext

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.extraction.fingerprint import PeriodBlock, TableFingerprint, build_fingerprint
from app.parsing.evidence import product_aliases
from app.parsing.labels import (
    FLAG_COMBINED,
    FLAG_FAMILY_INCLUDES,
    FLAG_NO_SALES,
    FLAG_PARTIAL,
    QUESTION_FLAGS,
    LabelReading,
    NoteReading,
    cite_footnote,
    footnotes_by_mark,
    names_product,
    read_footnote,
    read_label,
)
from app.parsing.tables import clean_label

# A change column is within this many percentage points of the computed change.
_PERCENT_TOLERANCE = 0.6

# Cells an issuer prints where a number would go, meaning "nothing to report".
# They occupy a column, so they must hold their place during alignment.
_PLACEHOLDER_RE = re.compile(r"^[\s$]*[-–—*]+[\s%)]*$|^\s*(?:n/?a|nm|not\s+meaningful)\s*$", re.IGNORECASE)
_NUMBER_CELL_RE = re.compile(r"^[\s$(]*(-?[\d,]+(?:\.\d+)?)[\s)%]*$")
# A value cell may carry a footnote or legend after the number, as in
# "$6,517 (USD thousands)". The number still owns the column.
_ANNOTATED_NUMBER_RE = re.compile(r"^[\s$]*(-?[\d,]+(?:\.\d+)?)\s*\(.*$")
# A percentage is a change column, never a reported amount.
_PERCENT_RE = re.compile(r"%")


def tokenize_row(cells: list[str]) -> list[float | None]:
    """Row cells as an ordered column vector, keeping dash placeholders as None.

    ``parse_numbers`` drops anything non-numeric, which silently closes the gap
    a dash leaves behind and shifts every later column one place left. That is
    the mechanism behind a full-year total landing in a quarter's slot, so
    alignment here is done on positions, not on the numbers that survived.
    """
    tokens: list[float | None] = []
    for cell in cells:
        text = (cell or "").strip()
        if not text:
            continue
        if _PLACEHOLDER_RE.match(text):
            tokens.append(None)
            continue
        match = _NUMBER_CELL_RE.match(text) or _ANNOTATED_NUMBER_RE.match(text)
        if match:
            negative = text.lstrip().startswith("(")
            value = float(match.group(1).replace(",", ""))
            tokens.append(-value if negative and value > 0 else value)
    return tokens


@dataclass(frozen=True)
class ExtractedValue:
    """One number, with everything needed to normalize and audit it."""

    product_label: str
    period: str
    period_type: str
    value_as_reported: float
    unit_label: str
    currency: str
    source_quote: str
    fingerprint_signature: str
    value_index: int
    # What the label said beyond the product's name. ``scope`` is the
    # geography it named (None for the whole product); ``combined_with`` the
    # other products it joined; ``residue`` the words unaccounted for; and
    # ``flags`` what follows - a combined line, a partial period, a label not
    # understood - which decides whether the value can be published at all.
    scope: str | None = None
    combined_with: tuple[str, ...] = ()
    residue: str = ""
    flags: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_label": self.product_label,
            "period": self.period,
            "period_type": self.period_type,
            "value_as_reported": self.value_as_reported,
            "unit_label": self.unit_label,
            "currency": self.currency,
            "source_quote": self.source_quote,
            "fingerprint_signature": self.fingerprint_signature,
            "value_index": self.value_index,
            "scope": self.scope,
            "combined_with": list(self.combined_with),
            "residue": self.residue,
            "flags": list(self.flags),
        }


@dataclass
class TableReadout:
    """What one table produced, including why it produced nothing."""

    fingerprint: TableFingerprint
    values: list[ExtractedValue]
    skipped_reason: str | None = None


def _percent_change(current: float, prior: float) -> float | None:
    if prior == 0:
        return None
    return (current - prior) / abs(prior) * 100.0


def _looks_like_change(candidate: float, current: float, prior: float) -> bool:
    change = _percent_change(current, prior)
    if change is None:
        return False
    return abs(abs(candidate) - abs(change)) <= _PERCENT_TOLERANCE


def _group_blocks(blocks: tuple[PeriodBlock, ...]) -> list[list[PeriodBlock]]:
    """Blocks grouped by reporting period, preserving column order.

    A 10-Q row is "Q values, its change columns, YTD values, its change
    columns". Grouping by period keeps the change-column skip local to the
    group it belongs to.
    """
    groups: list[list[PeriodBlock]] = []
    for block in sorted(blocks, key=lambda b: b.value_index):
        key = (block.months, block.end_month)
        if groups and (groups[-1][0].months, groups[-1][0].end_month) == key:
            groups[-1].append(block)
        else:
            groups.append([block])
    return groups


def map_values_to_blocks(
    values: list[float | None],
    blocks: tuple[PeriodBlock, ...],
) -> tuple[dict[int, float] | None, str | None]:
    """Assign row numbers to period columns, or explain why it is not safe to.

    ``values`` is position-preserving: a None marks a column the issuer printed
    as a dash, which holds its place so later columns do not shift left.

    Returns ({value_index: value}, None) on success, or (None, reason). Columns
    explicitly reported as a dash are simply absent from the mapping.
    """
    if not values or not blocks:
        return None, "no_values"

    def emit(pairs: list[tuple[PeriodBlock, float | None]]) -> dict[int, float]:
        return {block.value_index: value for block, value in pairs if value is not None}

    # Cleanest case: one column per declared period, nothing else in the row.
    if len(values) == len(blocks):
        ordered = sorted(blocks, key=lambda b: b.value_index)
        return emit(list(zip(ordered, values, strict=True))), None

    groups = _group_blocks(blocks)
    extra = len(values) - len(blocks)
    if extra < 0:
        return None, "too_few_values_for_declared_periods"
    if extra % len(groups) != 0:
        return None, "uneven_extra_columns"
    # Every period block in a filing carries the same set of change columns, so
    # the surplus divides evenly across the groups.
    change_columns = extra // len(groups)

    assigned: dict[int, float] = {}
    verified = change_columns == 0
    verifiable = change_columns == 0
    cursor = 0
    for group in groups:
        if cursor + len(group) > len(values):
            return None, "too_few_values_for_declared_periods"
        taken = values[cursor : cursor + len(group)]
        assigned.update(emit(list(zip(group, taken, strict=True))))
        cursor += len(group)
        skipped = values[cursor : cursor + change_columns]
        cursor += change_columns
        # Prove at least one skipped column really is a change column. If the
        # arithmetic never lines up, the row is not laid out the way its header
        # declared and any mapping would be a guess.
        if len(taken) >= 2:
            current, prior = taken[0], taken[1]
            # A comparative printed as a dash makes the change undefined, so
            # this group cannot confirm or deny the layout - a first-year
            # product reads "1,514 | - | 100.0 | %". Alignment there rests on
            # the dash holding its own column, which it does.
            if current is not None and prior is not None:
                verifiable = True
                verified = verified or any(
                    candidate is not None and _looks_like_change(candidate, current, prior)
                    for candidate in skipped
                )

    if cursor != len(values):
        return None, "unconsumed_values_after_mapping"
    if verifiable and not verified:
        return None, "unverified_extra_columns"
    return assigned, None


# What a filer prints in a value column to mean nil: an em dash, an en dash,
# a hyphen, and the same with a trailing footnote marker stripped already.
_NIL_CELLS = frozenset({"\u2014", "\u2013", "-", "\u2212"})


def cell_number(cell: str | None) -> float | None:
    """The number a single cell states, or None if it states something else.

    Unlike ``tokenize_row`` this does not need dashes to hold their place: a
    value read off a rectangle is identified by the column it sits in, so a
    missing comparative cannot shift anything.
    """
    text = (cell or "").strip()
    if not text or _PERCENT_RE.search(text):
        return None
    # A dash standing alone in a value column is nil, and reads as the zero it
    # means. Treating it as "no number here" makes a line that sold nothing
    # everywhere - a product not yet launched in a region, every comparative
    # column of a product's first quarter - look like a heading with no
    # figures, so the
    # label is carried onto the next product and the line stops counting
    # towards its own total.
    if text in _NIL_CELLS:
        return 0.0
    match = _NUMBER_CELL_RE.match(text) or _ANNOTATED_NUMBER_RE.match(text)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    return -value if text.startswith("(") and value > 0 else value


def _origins(row: list[str | None]) -> list[tuple[int, str]]:
    """(column, text) for the cells this row actually contains.

    Columns holding None continue a cell that began to the left or above; they
    are not cells of this row and carry no text of their own.
    """
    return [(column, cell) for column, cell in enumerate(row) if cell is not None]


def read_values_by_column(
    row: list[str | None],
    blocks: tuple[PeriodBlock, ...],
) -> tuple[dict[int, float] | None, str | None]:
    """Assign a grid row's numbers to the columns whose headings name a period.

    No inference is involved: a number belongs to the period stated over its own
    column, and a number in a column no heading gives a period to - a change
    column, a footnote marker - belongs to nothing and is dropped.

    Two numbers landing on one period is the one case this cannot resolve, since
    the table has not said which of them is the period's figure. The row is
    refused rather than guessed at.
    """
    periods = {block.value_index: block for block in blocks}
    assigned: dict[int, float] = {}
    claimed: dict[str, int] = {}
    for column, cell in _origins(row):
        block = periods.get(column)
        if block is None:
            continue
        value = cell_number(cell)
        if value is None:
            continue
        # A period under two geography headings is two columns of one period,
        # and the scope is what tells them apart.
        key = f"{block.months}m@{block.end_month}:{block.year}:{block.scope or ''}"
        if key in claimed and claimed[key] != column:
            return None, "two_values_for_one_period"
        claimed[key] = column
        assigned[column] = value
    if not assigned:
        return None, "no_values"
    return assigned, None


def _regroup(assigned: dict[int, float], period_of) -> dict[object, float]:
    """A row's values keyed by the period they are in, not the column they sit in.

    Two columns can state the same period. A filer prints the currency symbol in
    its own cell on the first line of a block and leaves it off the lines below,
    so "Calderon - U.S." carries its figure one column to the right of
    "Calderon - Europe" while both are the same quarter. Adding the components
    up by column
    then compares the U.S. line against nothing and the rest against a total
    they cannot reach, and the table is refused for having no total when the
    total is printed directly beneath it.
    """
    summed: dict[object, float] = {}
    for index, value in assigned.items():
        key = period_of(index)
        summed[key] = summed.get(key, 0.0) + value
    return summed


def _totals(assignments: Iterable[dict[object, float]]) -> dict[object, float]:
    summed: dict[object, float] = {}
    for assigned in assignments:
        for index, value in assigned.items():
            summed[index] = summed.get(index, 0.0) + value
    return summed


def _adds_up(candidate: dict[int, float], parts: dict[int, float], count: int) -> bool:
    """Whether a row is the sum of the component rows, in every period at once.

    Each component is printed rounded, so their sum can miss the printed total
    by up to half a unit each way. Requiring every period to agree is what makes
    the arithmetic an identification rather than a coincidence.
    """
    if not parts or set(candidate) != set(parts):
        return False
    return all(abs(candidate[index] - parts[index]) <= 0.5 * count for index in parts)


FLAG_REGIONS_NO_TOTAL = "region_rows_no_total"

Match = tuple[int, str, dict[int, float], LabelReading, tuple[str, ...]]
Published = tuple[int, str, str, dict[int, float], LabelReading, tuple[str, ...]]


def _resolve_matches(
    matches: list[Match],
    source_rows: list[list[str | None]],
    product: str,
    read_row,
    quote_of,
    *,
    quote_from: dict[int, int],
    period_of=None,
    reach: int = 2,
) -> tuple[list[Published], str | None]:
    """Which of the rows naming a product is the product's revenue.

    Every match arrives with its label read: whether the label is the whole
    product, a region of it, a combined line over it and its siblings, or a
    label with words nobody could account for. That reading decides:

    * a row that is the whole product, or a labelled total, is the product;
    * region rows are components. Their total is the labelled total, or the
      row beneath them whose figures equal their sums in every period - the
      table proving which row is its total. With no total, each region is
      published in its own scope and flagged, never as the family figure;
    * a combined line is the family's figure, not the product's, and a row
      whose label was not understood, or whose footnote made it a partial
      period, states nothing certain about the product: each is published as
      a question - it carries its flag and cannot be auto-passed - and is
      never the answer that silences the rest.

    The arithmetic is what identifies a total, so no list of region names is
    involved and an issuer inventing a new region changes nothing.
    """
    if not matches:
        return [], None
    period_of = period_of or (lambda index: index)
    grouped = lambda assigned: _regroup(assigned, period_of)

    def quote_for(position: int, first: int | None = None) -> str:
        start = quote_from.get(position, position) if first is None else first
        return quote_of(*source_rows[min(start, position) : position + 1])

    def publish(match: Match, first: int | None = None, *, label: str | None = None,
                extra: tuple[str, ...] = ()) -> Published:
        position, scoped, assigned, reading, flags = match
        return (position, label or scoped, quote_for(position, first), assigned, reading, flags + extra)

    questions = [m for m in matches if QUESTION_FLAGS & set(m[4])]
    plain = [m for m in matches if m not in questions]
    wholes = [m for m in plain if m[3].whole or m[3].is_total]
    regions = [m for m in plain if m not in wholes]

    published: list[Published] = []
    refusal: str | None = None

    if len(wholes) == 1:
        published.append(publish(wholes[0]))
    elif wholes:
        # The whole product printed more than once: one of them is the total
        # the others add to, or the table has said the same thing twice.
        total = _total_among(wholes, grouped)
        if total is not None:
            published.append(publish(total, min(quote_from.get(m[0], m[0]) for m in wholes)))
        else:
            values = [tuple(sorted(grouped(m[2]).items())) for m in wholes]
            if all(v == values[0] for v in values):
                published.append(publish(wholes[0]))
            else:
                refusal = ", ".join(m[1] for m in wholes) + ":several_lines_no_total"
    elif regions:
        total = _total_among(regions, grouped)
        if total is not None:
            published.append(publish(
                total, min(quote_from.get(m[0], m[0]) for m in regions), label=product))
        else:
            beneath = _total_beneath(regions, source_rows, read_row, grouped, reach)
            if beneath is not None:
                position, assigned = beneath
                first = min(quote_from.get(m[0], m[0]) for m in regions)
                reading = LabelReading(label=product, matched=product, scope=None,
                                       is_total=True, combined_with=(), residue="", marks=())
                published.append((position, product, quote_of(*source_rows[first : position + 1]),
                                  assigned, reading, ()))
            else:
                for match in regions:
                    published.append(publish(match, extra=(FLAG_REGIONS_NO_TOTAL,)))

    # A question is only worth asking where nothing answered: a row the
    # reader could not account for, beside the product's own row for the same
    # periods, is noise around an answer rather than a candidate for it.
    answered = {key for _, _, _, assigned, _, _ in published for key in grouped(assigned)}
    for match in questions:
        if not set(grouped(match[2])) <= answered:
            published.append(publish(match))
    return published, refusal


def _total_among(matches: list[Match], grouped) -> Match | None:
    """The matched row whose figures are the sum of the others, in every period."""
    for index, match in enumerate(matches):
        parts = [other[2] for other in matches[:index] + matches[index + 1 :]]
        if parts and _adds_up(grouped(match[2]), _totals(grouped(p) for p in parts), len(parts)):
            return match
    return None


def _total_beneath(
    matches: list[Match],
    source_rows: list[list[str | None]],
    read_row,
    grouped,
    reach: int,
) -> tuple[int, dict[int, float]] | None:
    """An unlabelled row printed beneath the components that sums them."""
    parts = _totals(grouped(assigned) for _, _, assigned, _, _ in matches)
    last = max(position for position, _, _, _, _ in matches)
    for position in range(last + 1, min(last + 1 + reach, len(source_rows))):
        cells = _origins(source_rows[position])
        if not cells:
            continue
        labelled = cell_number(cells[0][1]) is None
        assigned, _reason = read_row(source_rows[position], cells, labelled=labelled)
        if assigned and _adds_up(grouped(assigned), parts, len(matches)):
            return position, assigned
    return None


def _split_by_column_scope(
    assigned: dict[int, float], by_index: dict[int, PeriodBlock]
) -> dict[str | None, dict[int, float]]:
    """A row's values grouped by the geography their columns are headed by."""
    groups: dict[str | None, dict[int, float]] = {}
    for index, value in assigned.items():
        block = by_index.get(index)
        groups.setdefault(block.scope if block else None, {})[index] = value
    return groups


# A row putting two figures under one period is the table saying its headings
# and its body are not in the same columns, so the geometry describes neither.
_GEOMETRY_CONTRADICTED = "two_values_for_one_period"


def read_table(
    rows: list[list[str]],
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
    context: str = "",
    grid: list[list[str | None]] | None = None,
    period_context: PeriodContext | None = None,
    footnotes: Iterable[str] | None = None,
    products: Iterable[str] | None = None,
    declared_unit: str | None = None,
) -> TableReadout:
    """Read one table, by its geometry where that describes it and not otherwise.

    ``grid`` is the same table as a rectangle. Given it, a value's period is the
    one the headings covering its column state; without it the ragged rows are
    all there is and the columns have to be inferred.

    A rectangle can still fail to describe the table it came from - a press
    release spans "2021" over three columns while its product rows write figures
    in two of them and 2020's figure in the third. The body says so
    itself: a row puts two of its figures under one period, which cannot happen
    where the headings and the figures share columns. One such row condemns the
    reading for the whole table, not just for itself, because the rows that did
    not trip it were read against the same headings and are right only by luck -
    the U.S. line there reads correctly while Europe's does not.
    """
    readout = _read_table(
        rows,
        product=product,
        generic=generic,
        extra_aliases=extra_aliases,
        context=context,
        grid=grid,
        period_context=period_context,
        footnotes=footnotes,
        products=products,
        declared_unit=declared_unit,
    )
    if (
        grid
        and readout.fingerprint.by_column
        and _GEOMETRY_CONTRADICTED in (readout.skipped_reason or "")
    ):
        return _read_table(
            rows,
            product=product,
            generic=generic,
            extra_aliases=extra_aliases,
            context=context,
            grid=None,
            period_context=period_context,
            footnotes=footnotes,
            products=products,
            declared_unit=declared_unit,
        )
    return readout


def _read_table(
    rows: list[list[str]],
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
    context: str = "",
    grid: list[list[str | None]] | None = None,
    period_context: PeriodContext | None = None,
    footnotes: Iterable[str] | None = None,
    products: Iterable[str] | None = None,
    declared_unit: str | None = None,
) -> TableReadout:
    """One reading of one table, either by column or from the ragged rows.

    ``footnotes`` are the notes printed under this table; ``products`` are the
    other products the pipeline tracks or this run was asked about, which is
    what lets a label be read as a combined line over named products rather
    than as words nobody can account for.
    """
    fingerprint = build_fingerprint(
        rows, context, grid=grid, period_context=period_context, declared_unit=declared_unit
    )
    if not fingerprint.usable:
        reason = ";".join(fingerprint.notes) or "unusable_fingerprint"
        return TableReadout(fingerprint=fingerprint, values=[], skipped_reason=reason)

    aliases = product_aliases(product, generic, extra=extra_aliases)
    others = [p for p in (products or ()) if p]
    notes = footnotes_by_mark(footnotes)
    by_index = {block.value_index: block for block in fingerprint.blocks}
    values: list[ExtractedValue] = []
    skipped: list[str] = []

    source_rows: list[list[str | None]] = (
        grid if fingerprint.by_column and grid else [list(row) for row in rows]
    )

    def read_row(
        row: list[str | None],
        cells: list[tuple[int, str]],
        *,
        labelled: bool = True,
    ) -> tuple[dict[int, float] | None, str | None]:
        if fingerprint.by_column:
            return read_values_by_column(row, fingerprint.blocks)
        wanted = cells[1:] if labelled else cells
        return map_values_to_blocks(
            tokenize_row([cell for _, cell in wanted]), fingerprint.blocks
        )

    def quote_of(*rows_taken: list[str | None]) -> str:
        return " ".join(
            cell
            for row in rows_taken
            for _, cell in _origins(row)
            if cell and cell.strip()
        )

    # The table's own list of what it reports: every row's label. A product's
    # siblings are whatever else the filer prints beside it, which is known
    # per document and needs no catalogue of brand names.
    sibling_labels = [
        clean_label(cells[0][1])
        for cells in (_origins(row) for row in source_rows)
        if cells and clean_label(cells[0][1])
    ]

    def read(label: str) -> LabelReading:
        # The other rows: a row is not its own sibling, or every label would
        # read as a combined line over itself.
        own_label = clean_label(label)
        siblings = [s for s in sibling_labels if s != own_label and s not in own_label]
        return read_label(label, aliases, products=others, siblings=siblings)

    matches: list[Match] = []
    quote_from: dict[int, int] = {}
    notes_of: dict[int, list[tuple[str, str, NoteReading]]] = {}
    section: tuple[int, str] | None = None
    for position, row in enumerate(source_rows):
        cells = _origins(row)
        if not cells:
            continue
        label = (cells[0][1] or "").strip()
        if not clean_label(label):
            continue
        if not any(cell_number(cell) is not None for _column, cell in cells[1:]):
            # A label with no figures beside it heads the rows below rather than
            # stating anything itself. A filer prints the product that way -
            # "CALDERON" alone, then "US", "Intl", "WW" beneath it - so the
            # rows carrying the numbers never name the product at all.
            section = (position, label)
            continue
        reading, start = read(label), position
        if not reading.names_product and section:
            reading, start = read(f"{section[1]} {label}"), section[0]
        if not reading.names_product:
            continue
        flags: list[str] = list(reading.flags)
        combined = list(reading.combined_with)
        # Each note the label cites, with what it says and which figures of
        # the row it says it about: a note about the six-month column is not
        # about the quarter beside it.
        cited: list[tuple[str, str, NoteReading]] = []
        for mark in reading.marks:
            note = notes.get(mark)
            if not note:
                continue
            note_reading = read_footnote(note, aliases, products=others, siblings=sibling_labels)
            cited.append((mark, note, note_reading))
            combined.extend(n for n in note_reading.names if n not in combined)
            # The label names a family the note says includes this product:
            # "Calderon (1)" over "(1) includes Nebulized Calderon" is the
            # family's line and cannot be Nebulized Calderon's own.
            own_name = "".join(c for c in product.lower() if c.isalnum())
            matched_name = "".join(c for c in (reading.matched or "").lower() if c.isalnum())
            if matched_name != own_name and names_product(note, [product]):
                flags.append(FLAG_FAMILY_INCLUDES)
            # What a note says about who sold nothing, and about a partial
            # period, is said of the figures it names - "no sales of NuVessa
            # in Q1 2026" is not about the prior-year column beside it - so
            # both are applied to each figure below, where its period is known.
        if FLAG_FAMILY_INCLUDES in flags:
            skipped.append(f"{reading.label}:{FLAG_FAMILY_INCLUDES}")
            continue
        if combined and FLAG_COMBINED not in flags:
            flags.append(FLAG_COMBINED)
        if combined != list(reading.combined_with):
            reading = LabelReading(
                label=reading.label, matched=reading.matched, scope=reading.scope,
                is_total=reading.is_total, combined_with=tuple(combined),
                residue=reading.residue, marks=reading.marks, flags=reading.flags,
            )
        assigned, reason = read_row(row, cells)
        if assigned is None:
            skipped.append(f"{reading.label}:{reason}")
            continue
        quote_from[position] = start
        if cited:
            notes_of[position] = cited
        # A table split by geography above its period row puts one row's
        # figures under several scopes. Each scope is its own line of the
        # product - the worldwide column the whole, the others regions - and
        # is resolved beside the rows exactly as a printed region row is.
        for column_scope, part in _split_by_column_scope(assigned, by_index).items():
            scoped_reading = reading
            if column_scope is not None and reading.scope is None:
                scoped_reading = LabelReading(
                    label=reading.label, matched=reading.matched,
                    scope=None if column_scope == "Worldwide" else column_scope,
                    is_total=reading.is_total, combined_with=reading.combined_with,
                    residue=reading.residue, marks=reading.marks, flags=reading.flags,
                )
            matches.append((position, reading.label, part, scoped_reading, tuple(flags)))

    # Two columns can name the same period; the arithmetic that identifies a
    # total is about periods, so it groups the columns the table has equated.
    def period_of(index: int) -> object:
        block = by_index.get(index)
        return f"{block.months}m@{block.end_month}:{block.year}" if block else index

    published, refusal = _resolve_matches(
        matches, source_rows, product, read_row, quote_of,
        quote_from=quote_from, period_of=period_of,
    )
    if refusal:
        skipped.append(refusal)

    for position, label, quote, assigned, reading, flags in published:
        cited = notes_of.get(position, [])
        for value_index, value in sorted(assigned.items()):
            block = by_index[value_index]
            # The notes about this figure travel with it, so whoever reads
            # the quote reads what the marker pointed at - and a note about
            # another column of the row is not attached to this one.
            about = [(mark, note, reading_) for mark, note, reading_ in cited
                     if reading_.applies_to(block.months, block.period)]
            # The note says this product sold nothing in this period, so
            # whatever the line states here is someone else's. The figure is
            # carried out with the note's flag rather than dropped: a figure
            # that disappears leaves a gap nobody can account for, and the
            # flag is what puts this one in front of a person.
            says_no_sales = any(FLAG_NO_SALES in r.flags for _, _, r in about)
            # "No sales of NuVessa" under "Calderon and NuVessa": for the
            # period the note names, the line is Calderon's alone.
            no_sales = {n for _, _, r in about for n in r.no_sales_of}
            combined_with = tuple(n for n in reading.combined_with if n not in no_sales)
            value_flags = tuple(
                f for f in flags
                if f != FLAG_PARTIAL and (f != FLAG_COMBINED or combined_with)
            ) + tuple(
                FLAG_PARTIAL for _ in [1] if any(FLAG_PARTIAL in r.flags for _, _, r in about)
            ) + ((FLAG_NO_SALES,) if says_no_sales else ())
            suffix = "".join(cite_footnote(mark, note) for mark, note, _ in about)
            values.append(
                ExtractedValue(
                    product_label=label,
                    period=block.period,
                    period_type=block.period_type,
                    value_as_reported=value,
                    unit_label=fingerprint.unit_label,
                    currency=fingerprint.currency,
                    source_quote=f"{quote}{suffix}",
                    fingerprint_signature=fingerprint.signature,
                    value_index=value_index,
                    scope=None if reading.is_total else reading.scope,
                    combined_with=combined_with,
                    residue=reading.residue,
                    flags=value_flags,
                )
            )

    return TableReadout(
        fingerprint=fingerprint,
        values=values,
        skipped_reason="; ".join(skipped) or None,
    )


def read_tables(
    tables: Iterable[list[list[str]]],
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
    context: str = "",
    grids: Iterable[list[list[str | None]]] | None = None,
    captions: Iterable[str] | None = None,
    period_context: PeriodContext | None = None,
    footnotes: Iterable[Iterable[str]] | None = None,
    products: Iterable[str] | None = None,
    units: Iterable[str | None] | None = None,
) -> list[TableReadout]:
    """Read every table. ``grids`` holds the same tables as rectangles, in order.

    The lists are produced from one reading of the document, so table *n* of
    each is the same table; a shorter or absent ``grids`` simply means those
    tables are read from their ragged rows.

    ``captions`` is what introduces each table. A table declares its unit above
    itself, so its own caption is read before the document-wide ``context``,
    which otherwise supplies whichever declaration appears first in the filing -
    a different schedule's, stated in different units.
    """
    rectangles = list(grids or [])
    introductions = list(captions or [])
    notes = [list(n or ()) for n in (footnotes or [])]
    known = list(products or ())
    declared = list(units or ())
    return [
        read_table(
            rows,
            product=product,
            generic=generic,
            extra_aliases=extra_aliases,
            context=(
                f"{introductions[index]}\n{context}"
                if index < len(introductions) and introductions[index]
                else context
            ),
            grid=rectangles[index] if index < len(rectangles) else None,
            period_context=period_context,
            footnotes=notes[index] if index < len(notes) else None,
            products=known,
            declared_unit=declared[index] if index < len(declared) else None,
        )
        for index, rows in enumerate(tables or [])
    ]
