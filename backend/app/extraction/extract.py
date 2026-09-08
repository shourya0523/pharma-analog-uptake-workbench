"""Stage 2: read values out of a fingerprinted table.

Extraction here is deterministic and refuses to guess. If a product row's
numbers cannot be aligned to the fingerprint's period columns with confidence,
the row yields nothing and records why, rather than emitting a value attributed
to a period it may not belong to.

That rule exists because the alignment step is where a wrong number looks most
right. Merck's schedule dropped a "-" where a prior-year comparative belonged;
anything that silently shifted the remaining numbers left would have booked a
full-year total as a fourth-quarter figure - which is precisely the defect the
gold audit found.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.extraction.fingerprint import PeriodBlock, TableFingerprint, build_fingerprint
from app.parsing.evidence import product_aliases
from app.parsing.tables import clean_label
from app.quality.candidate_filters import KNOWN_PEER_BRANDS


# A change column is within this many percentage points of the computed change.
_PERCENT_TOLERANCE = 0.6

# Cells an issuer prints where a number would go, meaning "nothing to report".
# They occupy a column, so they must hold their place during alignment.
_PLACEHOLDER_RE = re.compile(r"^[\s$]*[-–—*]+[\s%)]*$|^\s*(?:n/?a|nm|not\s+meaningful)\s*$", re.I)
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
        }


@dataclass
class TableReadout:
    """What one table produced, including why it produced nothing."""

    fingerprint: TableFingerprint
    values: list[ExtractedValue]
    skipped_reason: str | None = None


def _matches_product(label: str, aliases: list[str]) -> bool:
    """True when the row label names this product and no competing brand."""
    normalized = label.lower()
    if not any(alias.lower() in normalized for alias in aliases):
        return False
    own = {alias.lower() for alias in aliases}
    for brand in KNOWN_PEER_BRANDS:
        if brand in own:
            continue
        if re.search(rf"\b{re.escape(brand)}\b", normalized):
            return False
    return True


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


def cell_number(cell: str | None) -> float | None:
    """The number a single cell states, or None if it states something else.

    Unlike ``tokenize_row`` this does not need dashes to hold their place: a
    value read off a rectangle is identified by the column it sits in, so a
    missing comparative cannot shift anything.
    """
    text = (cell or "").strip()
    if not text or _PERCENT_RE.search(text):
        return None
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
        key = f"{block.months}m@{block.end_month}:{block.year}"
        if key in claimed and claimed[key] != column:
            return None, "two_values_for_one_period"
        claimed[key] = column
        assigned[column] = value
    if not assigned:
        return None, "no_values"
    return assigned, None


def _names_the_product(label: str, product: str, generic: str | None) -> bool:
    """Whether this row is the product itself rather than one of its lines.

    "Tyvaso" is the product; "Tyvaso DPI" and "Harvoni - Japan" are lines within
    or beside it. Both match the product's aliases, and the difference between
    them is that one label is the name and nothing else.
    """
    words = lambda text: re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()
    return words(label) in ([words(product)] + ([words(generic)] if generic else []))


def _regroup(assigned: dict[int, float], period_of) -> dict[object, float]:
    """A row's values keyed by the period they are in, not the column they sit in.

    Two columns can state the same period. A filer prints the currency symbol in
    its own cell on the first line of a block and leaves it off the lines below,
    so "Harvoni - U.S." carries its figure one column to the right of "Harvoni -
    Europe" while both are the same quarter. Adding the components up by column
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


def _resolve_matches(
    matches: list[tuple[int, str, dict[int, float]]],
    source_rows: list[list[str | None]],
    product: str,
    generic: str | None,
    read_row,
    quote_of,
    *,
    naming: int,
    quote_from: dict[int, int],
    period_of=None,
    reach: int = 2,
) -> tuple[list[tuple[str, str, dict[int, float]]], str | None]:
    """Which of the rows naming a product is the product's revenue.

    An issuer that reports a product by region prints a line per region and the
    worldwide figure as their sum, and every one of those lines names the
    product. Publishing each of them files four different numbers as the same
    quarter's revenue - the defect this resolves - and picking the first is a
    guess. So:

    * one row names the product and nothing else - that row is the product;
    * otherwise a row whose value is the sum of the others, in every period, is
      the total the components add to. It may be one of the matched rows
      ("Total Harvoni") or the unlabelled line printed beneath them, which is
      how Gilead files it. The arithmetic is what identifies it, so no list of
      region names is involved and an issuer inventing a new region changes
      nothing;
    * otherwise the table has several lines for this product and no total, and
      which one is the product's revenue is exactly what has not been said.

    The total's quote runs from the first component to the total itself, so the
    number can be checked against the lines it sums.
    """
    if not matches:
        return [], None
    # Components add up within a period. Which column a figure sits in is how
    # the page is set, not what the figure is about.
    period_of = period_of or (lambda index: index)
    grouped = lambda assigned: _regroup(assigned, period_of)
    # How many rows name the product, not how many of them could be read: a
    # component whose numbers did not parse still means the row that did parse
    # is a component, and publishing it as the product is the same mistake.
    if naming == 1:
        position, label, assigned = matches[0]
        taken = source_rows[quote_from.get(position, position) : position + 1]
        return [(label, quote_of(*taken), assigned)], None

    named = [m for m in matches if _names_the_product(m[1], product, generic)]
    if len(named) == 1:
        position, label, assigned = named[0]
        taken = source_rows[quote_from.get(position, position) : position + 1]
        return [(label, quote_of(*taken), assigned)], None

    # A total printed among the matched rows.
    for index, (position, _label, assigned) in enumerate(matches):
        parts = [other[2] for other in matches[:index] + matches[index + 1 :]]
        if _adds_up(grouped(assigned), _totals(grouped(p) for p in parts), len(parts)):
            first = min(quote_from.get(other[0], other[0]) for other in matches)
            taken = source_rows[min(first, position) : max(first, position) + 1]
            return [(product, quote_of(*taken), assigned)], None

    # A total printed beneath them, with no label of its own.
    parts = _totals(grouped(assigned) for _, _, assigned in matches)
    last = max(position for position, _, _ in matches)
    for position in range(last + 1, min(last + 1 + reach, len(source_rows))):
        cells = _origins(source_rows[position])
        if not cells:
            continue
        labelled = cell_number(cells[0][1]) is None
        assigned, _reason = read_row(source_rows[position], cells, labelled=labelled)
        if assigned and _adds_up(grouped(assigned), parts, len(matches)):
            first = min(quote_from.get(other[0], other[0]) for other in matches)
            return [(product, quote_of(*source_rows[first : position + 1]), assigned)], None

    labels = ", ".join(label for _, label, _ in matches)
    unread = f" unread={naming - len(matches)}" if naming > len(matches) else ""
    return [], f"{labels}:several_lines_no_total{unread}"


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
) -> TableReadout:
    """Read one table, by its geometry where that describes it and not otherwise.

    ``grid`` is the same table as a rectangle. Given it, a value's period is the
    one the headings covering its column state; without it the ragged rows are
    all there is and the columns have to be inferred.

    A rectangle can still fail to describe the table it came from - Gilead's
    press release spans "2021" over three columns while its product rows write
    figures in two of them and 2020's figure in the third. The body says so
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
) -> TableReadout:
    """One reading of one table, either by column or from the ragged rows."""
    fingerprint = build_fingerprint(rows, context, grid=grid)
    if not fingerprint.usable:
        reason = ";".join(fingerprint.notes) or "unusable_fingerprint"
        return TableReadout(fingerprint=fingerprint, values=[], skipped_reason=reason)

    aliases = product_aliases(product, generic, extra=extra_aliases)
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

    matches: list[tuple[int, str, dict[int, float]]] = []
    quote_from: dict[int, int] = {}
    section: tuple[int, str] | None = None
    naming = 0
    for position, row in enumerate(source_rows):
        cells = _origins(row)
        if not cells:
            continue
        label = clean_label(cells[0][1])
        if not label:
            continue
        if not any(cell_number(cell) is not None for _column, cell in cells[1:]):
            # A label with no figures beside it heads the rows below rather than
            # stating anything itself. Johnson & Johnson prints the product that
            # way - "DARZALEX" alone, then "US", "Intl", "WW" beneath it - so
            # the rows carrying the numbers never name the product at all.
            section = (position, label)
            continue
        scoped, start = label, position
        if not _matches_product(label, aliases):
            if not section or not _matches_product(f"{section[1]} {label}", aliases):
                continue
            scoped, start = f"{section[1]} {label}", section[0]
        naming += 1
        assigned, reason = read_row(row, cells)
        if assigned is None:
            skipped.append(f"{scoped}:{reason}")
            continue
        quote_from[position] = start
        matches.append((position, scoped, assigned))

    # Two columns can name the same period; the arithmetic that identifies a
    # total is about periods, so it groups the columns the table has equated.
    def period_of(index: int) -> object:
        block = by_index.get(index)
        return f"{block.months}m@{block.end_month}:{block.year}" if block else index

    published, refusal = _resolve_matches(
        matches, source_rows, product, generic, read_row, quote_of,
        naming=naming, quote_from=quote_from, period_of=period_of,
    )
    if refusal:
        skipped.append(refusal)

    for label, quote, assigned in published:
        for value_index, value in sorted(assigned.items()):
            block = by_index[value_index]
            values.append(
                ExtractedValue(
                    product_label=label,
                    period=block.period,
                    period_type=block.period_type,
                    value_as_reported=value,
                    unit_label=fingerprint.unit_label,
                    currency=fingerprint.currency,
                    source_quote=quote,
                    fingerprint_signature=fingerprint.signature,
                    value_index=value_index,
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
        )
        for index, rows in enumerate(tables or [])
    ]
