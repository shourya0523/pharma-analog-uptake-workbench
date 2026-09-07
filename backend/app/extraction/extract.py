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


def read_table(
    rows: list[list[str]],
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
    context: str = "",
    grid: list[list[str | None]] | None = None,
) -> TableReadout:
    """Fingerprint one table and read every product row it declares.

    ``grid`` is the same table as a rectangle. When it is given the periods come
    from the headings covering each column and the values are read by column;
    without it the ragged rows are all there is and the columns are inferred.
    """
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
    for row in source_rows:
        cells = _origins(row)
        if not cells:
            continue
        label = clean_label(cells[0][1])
        if not label or not _matches_product(label, aliases):
            continue
        if fingerprint.by_column:
            assigned, reason = read_values_by_column(row, fingerprint.blocks)
        else:
            assigned, reason = map_values_to_blocks(
                tokenize_row([cell for _, cell in cells[1:]]), fingerprint.blocks
            )
        if assigned is None:
            skipped.append(f"{label}:{reason}")
            continue
        quote = " ".join(cell for _, cell in cells if cell and cell.strip())
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
) -> list[TableReadout]:
    """Read every table. ``grids`` holds the same tables as rectangles, in order.

    The two lists are produced from one reading of the document, so table *n* of
    each is the same table; a shorter or absent ``grids`` simply means those
    tables are read from their ragged rows.
    """
    rectangles = list(grids or [])
    return [
        read_table(
            rows,
            product=product,
            generic=generic,
            extra_aliases=extra_aliases,
            context=context,
            grid=rectangles[index] if index < len(rectangles) else None,
        )
        for index, rows in enumerate(tables or [])
    ]
