"""Reading what the model described: placement and arithmetic, nothing else.

A ``GridRegion`` says which rows belong to a product, what each row is, and
what each column means. This module takes the numbers off those rows and
keeps a description only where the row's own arithmetic admits it
(``columns.align_row`` places blanks and runs every check the layout
declares) and where the document prints what the description claims. It
never decides what a label means, never reads a header, never guesses a
unit: everything it cannot verify is reported as a ``VerificationFailure``
and, once, sent back to the model for repair.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.domain.models import ParsedDocument
from app.extraction.columns import Alignment, ColumnLayout, _tolerance, align_row
from app.extraction.readers import Observation, ReadReport, _period_bounds, _same_amount
from app.fingerprint.llm import (
    Fingerprint,
    GridRegion,
    RowDescription,
    SectionDescription,
    describe_column,
    duplicate_value_columns,
    product_name,
    squash,
)
from app.llm.grounding import quote_is_verbatim
from app.parsing.evidence import product_aliases
from app.parsing.grids import is_value_token

EXACT_LINES = {"own_revenue", "subtotal_of_geographies"}
READ_LINES = EXACT_LINES | {"franchise_or_bundle", "other_line_item"}
PROSE_SCOPES = {"product_own_revenue", "product_line_item_qualified"}
REPAIRABLE = {
    "more_cells_than_columns", "no_placement_satisfies_the_header", "ambiguous_alignment",
    "subtotal_not_sum_of_members", "coverage_outside_period", "product_row_not_described",
    "too_many_blank_placements", "duplicate_columns", "contradicted_within_document", "duplicate_geography_rows",
}


@dataclass(frozen=True)
class VerificationFailure:
    grid_index: int
    row_index: int | None
    code: str
    detail: str

    def render(self) -> str:
        where = f"row r{self.row_index}" if self.row_index is not None else "grid"
        return f"{where}: {self.code}: {self.detail}"

    def key(self) -> str:
        return f"table{self.grid_index}:r{self.row_index if self.row_index is not None else '-'}:{self.code}"


def _names(product: str, aliases: Iterable[str]) -> set[str]:
    return {product_name(a).lower() for a in aliases} | {product.lower()}


def _section_for(region: GridRegion, row_index: int) -> SectionDescription | None:
    above = [s for s in region.sections if s.row_index <= row_index]
    return max(above, key=lambda s: s.row_index) if above else None


def _current_periods(layout: ColumnLayout) -> dict[int | None, str]:
    """The latest period the layout carries for each period length."""
    current: dict[int | None, str] = {}
    for column in layout.columns:
        if column.kind == "value" and column.period and column.period > current.get(column.months, ""):
            current[column.months] = column.period
    return current


def _inside(covers: tuple[str, str], period: str, period_type: str) -> bool:
    bounds = _period_bounds(period, period_type)
    return bool(bounds) and bounds[0] <= covers[0] <= covers[1] <= bounds[1]


def _align(region: GridRegion, rows: list[list[str]], row: RowDescription) -> tuple[Alignment | None, VerificationFailure | None]:
    cells = rows[row.row_index]
    tokens = list(cells[row.label_width:])
    # Descriptive text columns between the label and the figures (a
    # therapeutic area, an indication) are part of what names the row, not
    # figures: a cell that is not a number or a placeholder cannot fill a
    # described column.
    while tokens and not is_value_token(tokens[0]):
        tokens.pop(0)
    layout = region.layout
    alignments, reason = align_row(tokens, layout)
    if reason:
        return None, VerificationFailure(
            region.grid_index, row.row_index, reason,
            f"{row.label_as_printed!r}: {len(tokens)} cells for {len(layout.columns)} described columns; {reason}",
        )
    readings = {tuple(sorted(a.values.items())) for a in alignments}
    if len(readings) > 1:
        placements = "; ".join(
            "blank at " + ",".join(f"c{g}" for g in a.gaps) + " -> " + ", ".join(f"c{c}={v:g}" for c, v in sorted(a.values.items()))
            for a in alignments[:4]
        )
        return None, VerificationFailure(
            region.grid_index, row.row_index, "ambiguous_alignment",
            f"{row.label_as_printed!r}: {len(readings)} placements satisfy the header: {placements}",
        )
    return alignments[0], None


def _label_names_product(label: str, names: set[str]) -> bool:
    lowered = label.lower()
    return any(name in lowered for name in names)


def read_described_grid(
    doc: ParsedDocument, region: GridRegion, *, product: str, aliases: Iterable[str], source_url: str = "",
) -> tuple[list[Observation], list[VerificationFailure]]:
    """Observations for ``product`` from one described grid, and what could not be verified."""
    tables = doc.tables or []
    if region.grid_index >= len(tables):
        return [], [VerificationFailure(region.grid_index, None, "no_such_grid", "the document has no grid with this index")]
    rows = tables[region.grid_index]
    names = _names(product, aliases)
    failures: list[VerificationFailure] = []
    observations: list[Observation] = []
    layout = region.layout
    duplicates = duplicate_value_columns(layout)
    if duplicates:
        # Nothing is placed against a header that names one figure twice; the
        # description goes back to the model with the columns spelled out.
        if not any(r.product.lower() in names for r in region.rows):
            return [], []
        first, second = duplicates[0]
        return [], [VerificationFailure(
            region.grid_index, None, "duplicate_columns",
            f"columns c{first} and c{second} are both described as {describe_column(layout.columns[first])}; "
            "a grid prints each figure once, so one of them is another period or period type "
            "(three months beside six or nine months, a quarter beside its year); re-read the header over each column",
        )]

    # A grid prints one figure per geography for a product: two own-revenue
    # or subtotal rows of this product under the same geography mean one of
    # them is another region or the subtotal of the others.
    by_geography: dict[str, list[RowDescription]] = defaultdict(list)
    for row in region.rows:
        if row.product.lower() in names and row.line in EXACT_LINES and row.geography:
            key = row.geography if row.geography != "Other" else f"Other:{squash(row.geography_as_printed or row.label_as_printed)}"
            by_geography[key].append(row)
    for geography, rows_alike in by_geography.items():
        if len(rows_alike) > 1:
            first, second = rows_alike[0], rows_alike[1]
            return [], [VerificationFailure(
                region.grid_index, second.row_index, "duplicate_geography_rows",
                f"rows r{first.row_index} ({first.label_as_printed!r}) and r{second.row_index} ({second.label_as_printed!r}) "
                f"are both described as {geography} for {product}; a grid prints one figure per geography, so one of them "
                "is another geography from the closed set (Europe, Japan, Other for a region the set does not name) or the "
                "subtotal_of_geographies of the others, with its members listed; re-read each row's label",
            )]

    # Every described row is placed, because subtotals are verified against
    # rows that may belong to no product of interest on their own.
    aligned: dict[int, Alignment] = {}
    for row in region.rows:
        if row.line not in READ_LINES or row.row_index >= len(rows):
            continue
        alignment, failure = _align(region, rows, row)
        if failure is not None:
            if row.product.lower() in names:
                failures.append(failure)
            continue
        aligned[row.row_index] = alignment

    described_indexes = {r.row_index for r in region.rows}
    for index, cells in enumerate(rows):
        label = " ".join(c for c in cells if c and not re.fullmatch(r"[\d,.()%$\-–—]+", c.strip())).strip()
        if index in described_indexes or not label:
            continue
        if _label_names_product(label, names) and any(re.search(r"\d", c) for c in cells[1:]):
            failures.append(VerificationFailure(
                region.grid_index, index, "product_row_not_described",
                f"row {index} prints {label[:60]!r} with values but the description does not say what it is",
            ))

    current = _current_periods(layout)
    for row in region.rows:
        if row.product.lower() not in names or row.line not in READ_LINES:
            continue
        alignment = aligned.get(row.row_index)
        if alignment is None:
            continue
        if row.line == "subtotal_of_geographies" and row.members:
            checked = 0
            for col, value in alignment.values.items():
                parts = [aligned[m].values[col] for m in row.members if m in aligned and col in aligned[m].values]
                if len(parts) < len(row.members):
                    continue
                checked += 1
                if abs(sum(parts) - value) > _tolerance(len(parts)):
                    failures.append(VerificationFailure(
                        region.grid_index, row.row_index, "subtotal_not_sum_of_members",
                        f"{row.label_as_printed!r} c{col}={value:g} but members {list(row.members)} sum to {sum(parts):g}",
                    ))
                    break
            else:
                if checked == 0 and any(m not in aligned for m in row.members):
                    failures.append(VerificationFailure(
                        region.grid_index, row.row_index, "subtotal_members_unaligned",
                        f"{row.label_as_printed!r}: members {list(row.members)} could not be placed",
                    ))
            if any(f.row_index == row.row_index and f.code == "subtotal_not_sum_of_members" for f in failures):
                continue
        section = _section_for(region, row.row_index)
        row_covers = row.covers or (section.covers if section else None)
        exact = row.line in EXACT_LINES
        # "Product revenues, net" assigned to the product because the filing
        # sells nothing else is provisional: it stands only where the
        # product's own rows leave a cell empty. A geography row ("U.S.",
        # "Japan") under the product's group is the product's own row.
        generic_line = (
            row.line == "own_revenue"
            and row.geography is None
            and not _label_names_product(row.label_as_printed, names)
            and not any(
                other is not row and other.product.lower() in names and _label_names_product(other.label_as_printed, names)
                for other in region.rows
            )
        )
        for col, value in sorted(alignment.values.items()):
            spec = layout.columns[col]
            if spec.kind != "value" or spec.period is None:
                continue
            covers = spec.covers
            if covers and not _inside(covers, spec.period, spec.period_type):
                # The description tied this span to this column; the column's
                # period does not contain it.
                failures.append(VerificationFailure(
                    region.grid_index, row.row_index, "coverage_outside_period",
                    f"{row.label_as_printed!r} c{col}: coverage {covers[0]}/{covers[1]} is not inside {spec.period}",
                ))
                covers = None
            if covers is None and row_covers and _inside(row_covers, spec.period, spec.period_type):
                # A footnote on the row or its section limits whichever
                # column's period contains the span; the other columns are whole.
                covers = row_covers
            notes = ["llm_fingerprint_v2"]
            if spec.period == current.get(spec.months):
                notes.append("current_period_column")
            if covers and (spec.covers is None):
                notes.append("coverage_from_description")
            if generic_line:
                notes.append("generic_product_line")
            if not layout.unit_declared:
                notes.append("unit_not_declared")
            observations.append(
                Observation(
                    product_label=row.label_as_printed or row.product,
                    period=spec.period,
                    period_type=spec.period_type,
                    value_as_reported=value,
                    unit_label=layout.unit_label,
                    currency=layout.currency,
                    unit_declared=layout.unit_declared,
                    geography=row.geography or spec.geography or region.grid_geography,
                    covers=covers,
                    source_quote=" ".join(c for c in rows[row.row_index] if c and c.strip()),
                    method="grid",
                    layout_signature=layout.signature,
                    verified=alignment.verified,
                    specificity=0 if exact else 1,
                    line_item="exact" if exact else "qualified",
                    source_url=source_url,
                    source_id=doc.source_id,
                    table_index=region.grid_index,
                    row_index=row.row_index,
                    notes=tuple(notes),
                    geography_label=row.geography_as_printed or (spec.label if spec.geography else None),
                    described_product=row.product,
                    line_kind=row.line,
                )
            )
    return observations, failures


_VALUE_IN_QUOTE_TOLERANT = True


def _value_in_quote(value: float, quote: str) -> bool:
    digits = quote.replace(",", "")
    return re.search(rf"(?<![\d.]){re.escape(f'{value:g}')}(?![\d])", digits) is not None


def _paragraph_around(text: str, quote: str) -> str:
    probe = quote.strip()[:60]
    position = text.find(probe)
    if position < 0:
        squashed = re.sub(r"\s+", " ", text)
        position = squashed.find(re.sub(r"\s+", " ", probe))
        text = squashed
    if position < 0:
        return quote
    start = max(0, text.rfind("\n\n", 0, position))
    end = text.find("\n\n", position)
    return text[start : end if end != -1 else len(text)]


def read_described_prose(
    doc: ParsedDocument, fingerprint: Fingerprint, *, product: str, aliases: Iterable[str], source_url: str = "",
) -> tuple[list[Observation], Counter, list[str]]:
    names = _names(product, aliases)
    observations: list[Observation] = []
    dropped: Counter = Counter()
    skipped: list[str] = []
    text = doc.full_text
    for region in fingerprint.prose:
        if (region.product or "").lower() not in names:
            continue
        if region.statement != "actual":
            dropped[region.statement] += 1
            continue
        if region.scope not in PROSE_SCOPES:
            dropped[f"scope:{region.scope}"] += 1
            continue
        if not quote_is_verbatim(region.quote, text):
            skipped.append(f"prose:{region.period}:quote_not_in_document")
            continue
        if not _value_in_quote(region.value, region.quote):
            skipped.append(f"prose:{region.period}:value_not_in_quote")
            continue
        paragraph = _paragraph_around(text, region.quote)
        if not any(name in region.quote.lower() or name in paragraph.lower() for name in names):
            skipped.append(f"prose:{region.period}:product_not_named_near_quote")
            continue
        covers = region.covers
        if covers and not _inside(covers, region.period, region.period_type):
            skipped.append(f"prose:{region.period}:coverage_outside_period")
            covers = None
        exact = region.scope == "product_own_revenue"
        observations.append(
            Observation(
                product_label=product,
                period=region.period,
                period_type=region.period_type,
                value_as_reported=region.value,
                unit_label=region.unit,
                currency=region.currency,
                unit_declared=True,
                geography=region.geography,
                covers=covers,
                source_quote=region.quote,
                method="prose",
                layout_signature="llm_prose",
                verified=("quote_in_document", "value_in_quote"),
                specificity=1 if region.period_from_context else 0,
                line_item="exact" if exact else "qualified",
                source_url=source_url,
                source_id=doc.source_id,
                notes=("llm_fingerprint_v2",) + (("period_from_context",) if region.period_from_context else ()),
                geography_label=region.geography_as_printed,
                described_product=region.product,
                line_kind="prose",
                provisional=True,
            )
        )
    return observations, dropped, skipped


def _contradictions(observations: list[Observation]) -> tuple[list[Observation], list[VerificationFailure]]:
    """Grid readings of one figure that disagree within one document, and the failures that send them back.

    A document states each figure once. Two grids that give different values
    for the same product, period, period type, geography and coverage mean
    one description put a column under the wrong period (a six-month column
    beside its quarter, a year beside a quarter). The reader cannot tell
    which, so neither is kept; the model is shown both.
    """
    groups: dict[tuple, list[Observation]] = defaultdict(list)
    for o in observations:
        if o.method == "grid" and o.line_item == "exact":
            groups[(o.period, o.period_type, o.geography, o.covers)].append(o)
    contradicted: list[Observation] = []
    failures: list[VerificationFailure] = []
    for (period, period_type, geography, _covers), group in groups.items():
        if all(_same_amount(group[0], o) for o in group[1:]):
            continue
        contradicted.extend(group)
        figure = f"{period} {period_type}" + (f" {geography}" if geography else "")
        for o in group:
            others = "; ".join(
                f"grid {x.table_index} row r{x.row_index} ({x.source_quote[:40]!r}) states {x.value_as_reported:,g} {x.unit_label}"
                for x in group if x is not o
            )
            failures.append(VerificationFailure(
                o.table_index, o.row_index, "contradicted_within_document",
                f"states {o.value_as_reported:,g} {o.unit_label} for {figure}, but {others} for the same figure in this "
                "document; a document states each figure once, so one of these columns is another period or period type "
                "(three months beside six or nine months, a quarter beside its year); re-read the header over each column",
            ))
    return contradicted, failures


def _drop_contradicted(report: ReadReport) -> None:
    contradicted, failures = _contradictions(report.observations)
    if not contradicted:
        return
    gone = {id(o) for o in contradicted}
    report.observations = [o for o in report.observations if id(o) not in gone]
    report.failures = report.failures + failures
    report.skipped = report.skipped + [f.key() for f in failures]


def read_described_document(
    doc: ParsedDocument,
    fingerprint: Fingerprint | None,
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
    source_url: str = "",
) -> ReadReport:
    """Everything the description lets the reader verify, for one product."""
    if fingerprint is None:
        return ReadReport([], ["no_fingerprint"], mode="model")
    aliases = product_aliases(product, generic, extra=extra_aliases)
    observations: list[Observation] = []
    failures: list[VerificationFailure] = []
    for region in fingerprint.grids_for(product, aliases):
        got, failed = read_described_grid(doc, region, product=product, aliases=aliases, source_url=source_url)
        observations.extend(got)
        failures.extend(failed)
    prose, dropped, skipped = read_described_prose(doc, fingerprint, product=product, aliases=aliases, source_url=source_url)
    observations.extend(prose)
    report = ReadReport(
        observations=observations,
        skipped=[f.key() for f in failures] + skipped,
        failures=failures,
        rejected=list(fingerprint.rejected),
        mode="model",
        dropped_prose=dict(dropped),
    )
    _drop_contradicted(report)
    return report


async def read_with_repair(
    doc: ParsedDocument,
    fingerprint: Fingerprint | None,
    fingerprinter: Any,
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
    source_url: str = "",
    products: list[str] | None = None,
    title: str = "",
) -> ReadReport:
    """Read; then, once, hand each grid the reader could not verify back to the model."""
    report = read_described_document(doc, fingerprint, product=product, generic=generic, extra_aliases=extra_aliases, source_url=source_url)
    if fingerprint is None or fingerprinter is None:
        return report
    aliases = product_aliases(product, generic, extra=extra_aliases)
    by_grid: dict[int, list[VerificationFailure]] = defaultdict(list)
    for failure in report.failures:
        if failure.code in REPAIRABLE:
            by_grid[failure.grid_index].append(failure)
    for grid_index, failed in by_grid.items():
        region = fingerprint.grid(grid_index)
        if region is None:
            continue
        repaired = await fingerprinter.repair(
            doc, region, [f.render() for f in failed], products=products or [product], title=title,
        )
        if repaired is None:
            continue
        before_obs = [o for o in report.observations if o.table_index == grid_index and o.method == "grid"]
        got, failed_after = read_described_grid(doc, repaired, product=product, aliases=aliases, source_url=source_url)
        repairable_after = [f for f in failed_after if f.code in REPAIRABLE]
        if len(repairable_after) < len(failed) or (len(got) > len(before_obs) and len(repairable_after) <= len(failed)):
            report.observations = [o for o in report.observations if not (o.table_index == grid_index and o.method == "grid")] + got
            report.failures = [f for f in report.failures if f.grid_index != grid_index] + failed_after
            report.skipped = [s for s in report.skipped if not s.startswith(f"table{grid_index}:")] + [f.key() for f in failed_after]
            report.repairs += 1
            report.repaired_grids.append(grid_index)
    _drop_contradicted(report)
    return report
