"""LLM fingerprinting: where a document states product revenue, and what each part means.

A converted document is a set of grids and passages. The model is asked to
*describe* the ones that state revenue for a set of products - which grid,
what unit and currency, what each column is, which rows belong to which
product and what kind of line each is, which sentences state a figure and
whether the statement is a result, guidance, a payment or a bundle. It never
extracts a number. The reader takes the numbers off the described cells and
keeps a description only where the row's own arithmetic and the document's
own text agree with it, so a wrong description costs a value rather than
inventing one.

Interpretation of meaning lives here, in the contract with the model.
Everything in this module that is not the prompt is grounding: the sketch
shows the model the document with stable indexes, and the parser accepts a
description only where its labels and indexes are the document's.

One call covers every product of interest in one part of a document; long
documents are described in parts. Answers are cached on disk by prompt
version, model, products and the exact text shown, so replays are free.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.domain.models import ParsedDocument
from app.extraction.columns import _PERIOD_TYPE_BY_MONTHS, ColumnLayout, ColumnSpec
from app.llm.client import OpenRouterClient, load_prompt
from app.parsing.evidence import MONEY_RE, product_aliases
from app.parsing.grids import is_value_token

logger = logging.getLogger(__name__)

PROMPT_NAME = "region_fingerprinter"
REPAIR_PROMPT_NAME = "region_fingerprint_repair"
CACHE_DIR = Path(__file__).resolve().parents[2] / "storage" / "fingerprints"

# Contract vocabulary. These are the closed sets the model is asked to use;
# validating against them is validation of the model's output format, not a
# reading of the document.
_PERIOD_RE = re.compile(r"^(\d{4})(?:Q([1-4]))?$")
_MONTHS_BY_TYPE = {"quarterly": 3, "six_month": 6, "nine_month": 9, "annual": 12}
UNIT_WORDS = ("units", "thousands", "millions", "billions")
UNIT_SOURCES = ("header", "caption", "footnote", "document_head", "undeclared")
GEOGRAPHIES = ("United States", "International", "Worldwide", "Europe", "Japan", "Other")
LINE_KINDS = (
    "own_revenue", "subtotal_of_geographies", "franchise_or_bundle", "other_line_item", "cost_or_expense", "not_revenue",
)
SECTION_KINDS = ("revenue", "cost_or_expense", "balance", "other")
STATEMENTS = ("actual", "guidance", "payment_or_financing", "franchise_or_multi_product", "change", "other")
SCOPES = ("product_own_revenue", "product_line_item_qualified", "company_total", "franchise")

# Grids shown to the model beyond those naming a product: any grid that says
# it is about sales or revenue. This chooses what the model gets to see, not
# what anything means; a grid it misses is a grid the model never described.
_REVENUE_WORDS_RE = re.compile(r"\b(?:product\s+sales|net\s+sales|revenues?)\b", re.IGNORECASE)


# --------------------------------------------------------------------------
# Descriptions
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RowDescription:
    row_index: int
    label_as_printed: str
    label_width: int                      # leading cells that are the label, from grounding
    product: str
    geography: str | None
    geography_as_printed: str | None
    line: str
    members: tuple[int, ...] = ()
    covers: tuple[str, str] | None = None
    why: str = ""


@dataclass(frozen=True)
class SectionDescription:
    row_index: int
    heading_as_printed: str
    kind: str
    covers: tuple[str, str] | None = None


@dataclass(frozen=True)
class GridRegion:
    grid_index: int
    layout: ColumnLayout
    products: tuple[dict[str, Any], ...] = ()     # product -> row label summary, derived from rows
    rows: tuple[RowDescription, ...] = ()
    sections: tuple[SectionDescription, ...] = ()
    grid_geography: str | None = None
    unit_source: str = "undeclared"
    why: str = ""
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProseRegion:
    product: str
    period: str
    period_type: str
    value: float
    unit: str
    currency: str
    geography: str | None
    quote: str
    period_from_context: bool = False
    statement: str = "actual"
    scope: str = "product_own_revenue"
    geography_as_printed: str | None = None
    covers: tuple[str, str] | None = None
    passage_offset: int | None = None
    model: str = ""


@dataclass
class Fingerprint:
    grids: list[GridRegion] = field(default_factory=list)
    prose: list[ProseRegion] = field(default_factory=list)
    raw: list[dict[str, Any]] = field(default_factory=list)
    cached: bool = False
    model: str = ""
    rejected: list[str] = field(default_factory=list)
    adjusted: list[str] = field(default_factory=list)   # corrections the parser made without dropping anything
    promotions: list[str] = field(default_factory=list)
    parts: int = 0
    calls: int = 0
    tiers: dict[str, int] = field(default_factory=dict)
    shown_grids: tuple[int, ...] = ()

    def grids_for(self, product: str, aliases: Iterable[str]) -> list[GridRegion]:
        names = {a.lower() for a in aliases} | {product.lower()}
        return [
            g for g in self.grids
            if any(row.product.lower() in names for row in g.rows)
            or any((p.get("product") or "").lower() in names for p in g.products)
        ]

    def grid(self, index: int) -> GridRegion | None:
        return next((g for g in self.grids if g.grid_index == index), None)

    def extend(self, other: Fingerprint) -> None:
        self.grids.extend(other.grids)
        self.prose.extend(other.prose)
        self.raw.extend(other.raw)
        self.rejected.extend(other.rejected)
        self.adjusted.extend(other.adjusted)
        self.promotions.extend(other.promotions)


# --------------------------------------------------------------------------
# Document sketch
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SketchPart:
    index: int
    total: int
    head: str
    grids_text: str
    prose_text: str
    grid_indexes: tuple[int, ...]
    rows_shown: int

    @property
    def text(self) -> str:
        return f"{self.head}\n{self.grids_text}\n{self.prose_text}"


def _mentions(text: str, aliases: list[str]) -> bool:
    lowered = text.lower()
    return any(alias.lower() in lowered for alias in aliases)


def _has_values(row: list[str]) -> bool:
    return any(is_value_token(cell) for cell in row[1:]) or (bool(row) and is_value_token(row[0]))


def _row_line(index: int, row: list[str]) -> str:
    cells = list(row)
    if cells and is_value_token(cells[0]):
        cells = ["<no label>"] + cells
    return f"r{index}: " + " | ".join(cells)


def _grid_lines(rows: list[list[str]], keep: set[int] | None) -> str:
    """Rows with their indexes; omitted runs are marked so indexes stay valid."""
    lines: list[str] = []
    omitted_from: int | None = None
    for index, row in enumerate(rows):
        if keep is not None and index not in keep:
            if omitted_from is None:
                omitted_from = index
            continue
        if omitted_from is not None:
            lines.append(f"... (rows r{omitted_from}-r{index - 1} omitted)")
            omitted_from = None
        lines.append(_row_line(index, row))
    if omitted_from is not None:
        lines.append(f"... (rows r{omitted_from}-r{len(rows) - 1} omitted)")
    return "\n".join(lines)


def _select_rows(rows: list[list[str]], aliases: list[str], *, whole_limit: int = 120) -> set[int] | None:
    """Which rows of a long grid to show: its header, the product rows in context, its end."""
    if len(rows) <= whole_limit:
        return None
    keep: set[int] = set(range(min(8, len(rows))))
    for index, row in enumerate(rows):
        if _mentions(" ".join(row), aliases):
            keep.update(range(max(0, index - 3), min(len(rows), index + 4)))
    keep.update(range(max(0, len(rows) - 6), len(rows)))
    return keep


def sketch_document(
    doc: ParsedDocument,
    *,
    aliases: list[str],
    part_max_chars: int = 45_000,
    max_rows_per_part: int = 160,
    max_passages: int = 40,
    head_chars: int = 1500,
) -> list[SketchPart]:
    """What the model gets to see, in parts a call can carry.

    Grids that name a product are shown whole (bounded, with the product
    rows in context when very long); other grids that say they are about
    sales are shown as header and first rows, so a product table whose
    labels use a name the catalog does not list can still be found; a
    header-only grid printed just before a product grid (a page break) is
    shown whole. Every row carries its index. Passages are the paragraphs
    that mention a product near a money amount.
    """
    tables = doc.tables or []
    joined_by_index = {i: "\n".join(" ".join(row) for row in rows) for i, rows in enumerate(tables)}
    alias_grids = {i for i, joined in joined_by_index.items() if _mentions(joined, aliases)}

    grid_blocks: list[tuple[int, str, int]] = []
    for index, rows in enumerate(tables):
        joined = joined_by_index[index]
        if index in alias_grids:
            keep = _select_rows(rows, aliases)
            text = _grid_lines(rows, keep)
            shown_rows = len(rows) if keep is None else len(keep)
        elif _REVENUE_WORDS_RE.search(joined) and len(rows) <= 60:
            keep = set(range(min(12, len(rows)))) | set(range(max(0, len(rows) - 3), len(rows)))
            text = _grid_lines(rows, keep)
            shown_rows = len(keep)
        elif index + 1 in alias_grids and len(rows) <= 12 and not any(_has_values(r) for r in rows):
            text = _grid_lines(rows, None)
            shown_rows = len(rows)
        else:
            continue
        grid_blocks.append((index, f"[grid {index}] ({len(rows)} rows)\n{text}\n", shown_rows))

    prose_blocks: list[str] = []
    text = doc.full_text
    if aliases:
        pattern = re.compile("|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)), re.IGNORECASE)
        seen_spans: list[tuple[int, int]] = []
        for match in pattern.finditer(text):
            start = max(0, text.rfind("\n\n", 0, match.start()))
            end = text.find("\n\n", match.end())
            end = len(text) if end == -1 else end
            if end - start > 2500:
                start = max(start, match.start() - 900)
                end = min(end, match.end() + 900)
            if any(s <= match.start() < e for s, e in seen_spans):
                continue
            passage = text[start:end].strip()
            if not MONEY_RE.search(passage):
                continue
            seen_spans.append((start, end))
            prose_blocks.append(f"[passage @{start}]\n{passage}\n")
            if len(prose_blocks) >= max_passages:
                break

    if not grid_blocks and not prose_blocks:
        return []

    head = text[:head_chars].strip()
    parts: list[tuple[list[tuple[int, str]], list[str], int]] = []
    current_grids: list[tuple[int, str]] = []
    current_prose: list[str] = []
    current_chars = len(head)
    current_rows = 0

    def close() -> None:
        nonlocal current_grids, current_prose, current_chars, current_rows
        if current_grids or current_prose:
            parts.append((current_grids, current_prose, current_rows))
        current_grids, current_prose, current_chars, current_rows = [], [], len(head), 0

    for index, block, shown_rows in grid_blocks:
        if current_grids and (current_chars + len(block) > part_max_chars or current_rows + shown_rows > max_rows_per_part):
            close()
        current_grids.append((index, block))
        current_chars += len(block)
        current_rows += shown_rows
    for block in prose_blocks:
        if (current_grids or current_prose) and current_chars + len(block) > part_max_chars:
            close()
        current_prose.append(block)
        current_chars += len(block)
    close()

    return [
        SketchPart(
            index=i, total=len(parts), head=head,
            grids_text="\n".join(b for _, b in grids), prose_text="\n".join(prose),
            grid_indexes=tuple(g for g, _ in grids), rows_shown=rows,
        )
        for i, (grids, prose, rows) in enumerate(parts)
    ]


# --------------------------------------------------------------------------
# Parsing the model's description: grounding, not interpretation
# --------------------------------------------------------------------------

_SQUASH_RE = re.compile(r"[\s|®™'’‘`\"]+")


def squash(text: str) -> str:
    """Text reduced to what survives any renderer: letters, digits, punctuation."""
    return _SQUASH_RE.sub("", (text or "").replace("\xa0", " ")).lower()


def _period_parts(period: str, period_type: str) -> tuple[int | None, int | None, int | None]:
    """(months, end_month, year) for a described column."""
    match = _PERIOD_RE.match((period or "").strip())
    if not match:
        return None, None, None
    year = int(match.group(1))
    quarter = int(match.group(2)) if match.group(2) else None
    months = _MONTHS_BY_TYPE.get(period_type or ("quarterly" if quarter else "annual"))
    if quarter:
        # A quarter label with a longer type is a contradiction; trust the label.
        return 3, quarter * 3, year
    if months is None:
        return None, None, None
    return months, min(12, months), year


def _covers(value: Any) -> tuple[str, str] | None:
    if isinstance(value, str) and value.count("/") == 1:
        start, end = value.split("/")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start.strip()) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", end.strip()):
            return start.strip(), end.strip()
    return None


def _geography(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    for name in GEOGRAPHIES:
        if text.lower() == name.lower():
            return name
    return None


def layout_from_region(region: dict[str, Any]) -> ColumnLayout | None:
    columns: list[ColumnSpec] = []
    coverage: dict[int, tuple[str, str]] = {}
    for entry in region.get("coverage") or []:
        if isinstance(entry, dict):
            span = _covers(entry.get("covers"))
            try:
                if span:
                    coverage[int(entry.get("column_index", -1))] = span
            except (TypeError, ValueError):
                continue
    for index, column in enumerate(region.get("columns") or []):
        if not isinstance(column, dict):
            return None
        kind = (column.get("kind") or "value").lower()
        geography = _geography(column.get("geography"))
        if kind == "change":
            columns.append(ColumnSpec("change", geography=geography, label=str(column.get("label_as_printed") or "change")))
            continue
        months, end_month, year = _period_parts(column.get("period") or "", column.get("period_type") or "")
        if year is None:
            return None
        columns.append(
            ColumnSpec(
                "value", months, end_month, year, geography=geography, covers=coverage.get(index),
                label=str(column.get("label_as_printed") or column.get("period")),
            )
        )
    if not any(c.kind == "value" for c in columns):
        return None
    unit = (region.get("unit") or "").lower().strip()
    currency = (region.get("currency") or "").upper().strip()
    unit_declared = unit in UNIT_WORDS
    currency_declared = bool(re.fullmatch(r"[A-Z]{3}", currency))
    return ColumnLayout(
        columns=tuple(columns),
        unit_label=unit if unit_declared else "millions",
        currency=currency if currency_declared else "USD",
        unit_declared=unit_declared,
        currency_declared=currency_declared,
        notes=("llm_fingerprint",) + (() if unit_declared else ("unit_not_declared",)),
    )


def ground_label(label: str, row: list[str]) -> int | None:
    """The number of leading cells that print ``label``, or None when the row does not."""
    wanted = squash(label)
    if not wanted:
        return 0 if row and is_value_token(row[0]) else None
    found: int | None = None
    for width in range(1, len(row) + 1):
        printed = squash(" ".join(row[:width]))
        if printed == wanted:
            # A cell that squashes to nothing ("®") belongs to the label too.
            found = width
            continue
        if len(printed) > len(wanted):
            break
    return found


def _uniform_geography(layout: ColumnLayout) -> str | None:
    geos = {c.geography for c in layout.columns if c.kind == "value"}
    return next(iter(geos)) if len(geos) == 1 and None not in geos else None


def duplicate_value_columns(layout: ColumnLayout) -> list[tuple[int, int]]:
    """Pairs of value columns described as the same figure: same period, period type, geography and coverage.

    A grid prints each figure once, so two such columns mean one of them is
    another period or period type (a six-month column beside its quarter).
    """
    seen: dict[tuple, int] = {}
    pairs: list[tuple[int, int]] = []
    for index, column in enumerate(layout.columns):
        if column.kind != "value":
            continue
        key = (column.months, column.end_month, column.year, column.geography, column.covers)
        if key in seen:
            pairs.append((seen[key], index))
        else:
            seen[key] = index
    return pairs


def describe_column(column: ColumnSpec) -> str:
    if column.kind != "value":
        return "change"
    period_type = _PERIOD_TYPE_BY_MONTHS.get(column.months, f"{column.months} months")
    period = f"{column.year}Q{(column.end_month - 1) // 3 + 1}" if column.months == 3 else \
        (f"{column.year}" if column.months == 12 else f"{column.months} months to {column.year}-{column.end_month:02d}")
    return f"{period} {period_type}" + (f" {column.geography}" if column.geography else "")


def _parse_grid(region: dict[str, Any], *, doc: ParsedDocument | None, shown: set[int] | None, model: str,
                rejected: list[str], adjusted: list[str]) -> GridRegion | None:
    try:
        index = int(region["grid_index"])
    except (KeyError, TypeError, ValueError):
        rejected.append("grid:?:no_index")
        return None
    if shown is not None and index not in shown:
        rejected.append(f"grid{index}:not_shown")
        return None
    rows_of_grid = (doc.tables or [])[index] if doc is not None and index < len(doc.tables or []) else None
    if doc is not None and rows_of_grid is None:
        rejected.append(f"grid{index}:no_such_grid")
        return None
    layout = layout_from_region(region)
    if layout is None:
        rejected.append(f"grid{index}:columns_unparseable")
        return None
    grid_geography = _geography(region.get("grid_geography"))
    uniform = _uniform_geography(layout)
    if uniform is not None:
        # One geography on every value column is the grid's scope, which the
        # contract asks for as grid_geography; a column geography is only
        # what tells columns apart.
        from dataclasses import replace as _replace
        layout = _replace(layout, columns=tuple(
            _replace(c, geography=None) if c.kind == "value" else c for c in layout.columns
        ))
        grid_geography = grid_geography or uniform
        adjusted.append(f"grid{index}:uniform_column_geography_taken_as_grid_geography")
    for first, second in duplicate_value_columns(layout):
        # Kept, so that the reader can hand the grid back for repair with the
        # concrete failure; the reader places nothing from it meanwhile.
        rejected.append(f"grid{index}:duplicate_columns(c{first},c{second})")

    sections: list[SectionDescription] = []
    for entry in region.get("sections") or []:
        if not isinstance(entry, dict):
            continue
        try:
            row_index = int(entry.get("row_index"))
        except (TypeError, ValueError):
            rejected.append(f"grid{index}:section:no_index")
            continue
        heading = str(entry.get("heading_as_printed") or "")
        kind = str(entry.get("kind") or "other")
        if kind not in SECTION_KINDS:
            kind = "other"
        if rows_of_grid is not None:
            if not 0 <= row_index < len(rows_of_grid):
                rejected.append(f"grid{index}:section{row_index}:out_of_range")
                continue
            printed = squash(" ".join(rows_of_grid[row_index]))
            if heading and squash(heading) not in printed:
                rejected.append(f"grid{index}:section{row_index}:heading_not_grounded")
                continue
        sections.append(SectionDescription(row_index, heading, kind, _covers(entry.get("covers"))))

    rows: list[RowDescription] = []
    for entry in region.get("rows") or []:
        if not isinstance(entry, dict):
            continue
        try:
            row_index = int(entry.get("row_index"))
        except (TypeError, ValueError):
            rejected.append(f"grid{index}:row:?:no_index")
            continue
        product = str(entry.get("product") or "").strip()
        line = str(entry.get("line") or "").strip()
        if not product:
            rejected.append(f"grid{index}:row{row_index}:no_product")
            continue
        if line not in LINE_KINDS:
            rejected.append(f"grid{index}:row{row_index}:line_kind({line})")
            continue
        label = str(entry.get("label_as_printed") if entry.get("label_as_printed") is not None else "")
        width: int | None = None
        if rows_of_grid is not None:
            if 0 <= row_index < len(rows_of_grid):
                width = ground_label(label, rows_of_grid[row_index])
            if width is None:
                # The label is the ground truth; a slipped index is corrected
                # when exactly one row prints that label.
                matches = [i for i, r in enumerate(rows_of_grid) if ground_label(label, r) is not None and label]
                if len(matches) == 1:
                    adjusted.append(f"grid{index}:row{row_index}:index_adjusted_to_{matches[0]}")
                    row_index = matches[0]
                    width = ground_label(label, rows_of_grid[row_index])
            if width is None:
                rejected.append(f"grid{index}:row{row_index}:label_not_grounded({label[:40]!r})")
                continue
        geography = _geography(entry.get("geography"))
        if entry.get("geography") and geography is None:
            rejected.append(f"grid{index}:row{row_index}:geography({entry.get('geography')})")
            geography = "Other"
        if any(r.row_index == row_index for r in rows):
            rejected.append(f"grid{index}:row{row_index}:described_twice")
            continue
        members: list[int] = []
        for m in entry.get("members") or []:
            try:
                members.append(int(m))
            except (TypeError, ValueError):
                continue
        rows.append(
            RowDescription(
                row_index=row_index, label_as_printed=label, label_width=width if width is not None else 1,
                product=product, geography=geography,
                geography_as_printed=str(entry.get("geography_as_printed")) if entry.get("geography_as_printed") else None,
                line=line, members=tuple(members), covers=_covers(entry.get("covers")), why=str(entry.get("why") or ""),
            )
        )
    described_indexes = {r.row_index for r in rows}
    rows = [
        RowDescription(**{**r.__dict__, "members": tuple(m for m in r.members if m in described_indexes)})
        for r in rows
    ]
    # v1 compatibility: a product -> row label summary.
    products: list[dict[str, Any]] = []
    for r in rows:
        products.append({
            "product": r.product, "row_label": r.label_as_printed, "geography": r.geography,
            "line": "exact" if r.line in {"own_revenue", "subtotal_of_geographies"} else "qualified",
        })
    for p in region.get("products") or []:
        if isinstance(p, dict) and p.get("product") and not rows:
            products.append(p)
    unit_source = str(region.get("unit_source") or ("undeclared" if not layout.unit_declared else "header"))
    if unit_source not in UNIT_SOURCES:
        unit_source = "undeclared"
    return GridRegion(
        grid_index=index, layout=layout, products=tuple(products), rows=tuple(rows), sections=tuple(sections),
        grid_geography=grid_geography, unit_source=unit_source, why=str(region.get("why") or ""), model=model, raw=region,
    )


def _parse_prose(region: dict[str, Any], *, model: str, rejected: list[str]) -> ProseRegion | None:
    try:
        value = float(str(region.get("value")).replace(",", ""))
    except (TypeError, ValueError):
        rejected.append("prose:value_not_numeric")
        return None
    period = str(region.get("period") or "")
    if not _PERIOD_RE.match(period):
        rejected.append(f"prose:period({period})")
        return None
    if not region.get("quote"):
        rejected.append(f"prose:{period}:no_quote")
        return None
    statement = str(region.get("statement") or "actual")
    if statement not in STATEMENTS:
        rejected.append(f"prose:{period}:statement({statement})")
        statement = "other"
    scope = str(region.get("scope") or "product_own_revenue")
    if scope not in SCOPES:
        rejected.append(f"prose:{period}:scope({scope})")
        scope = "franchise"
    unit = (region.get("unit") or "millions").lower()
    offset = region.get("passage_offset")
    try:
        offset = int(offset) if offset is not None else None
    except (TypeError, ValueError):
        offset = None
    return ProseRegion(
        product=str(region.get("product") or ""),
        period=period,
        period_type=str(region.get("period_type") or ("quarterly" if "Q" in period else "annual")),
        value=value,
        unit=unit if unit in UNIT_WORDS else "millions",
        currency=(region.get("currency") or "USD").upper(),
        geography=_geography(region.get("geography")),
        quote=str(region.get("quote")),
        period_from_context=bool(region.get("period_from_context")),
        statement=statement,
        scope=scope,
        geography_as_printed=str(region.get("geography_as_printed")) if region.get("geography_as_printed") else None,
        covers=_covers(region.get("covers")),
        passage_offset=offset,
        model=model,
    )


def parse_fingerprint(payload: dict[str, Any], *, doc: ParsedDocument | None = None,
                      shown: set[int] | None = None, model: str = "") -> Fingerprint:
    """The model's description, kept only where the document grounds it."""
    result = Fingerprint(raw=[payload], model=model)
    for region in payload.get("regions") or []:
        if not isinstance(region, dict):
            continue
        kind = (region.get("kind") or "").lower()
        if kind in {"grid", "table"}:
            grid = _parse_grid(region, doc=doc, shown=shown, model=model, rejected=result.rejected, adjusted=result.adjusted)
            if grid is not None:
                result.grids.append(grid)
        elif kind == "prose":
            prose = _parse_prose(region, model=model, rejected=result.rejected)
            if prose is not None:
                result.prose.append(prose)
    return result


# --------------------------------------------------------------------------
# The fingerprinter
# --------------------------------------------------------------------------

class LLMFingerprinter:
    """Describe documents with a fast model first and a strong model where the description fails."""

    def __init__(self, client: OpenRouterClient | None = None, *, model: str | None = None,
                 fast_model: str | None = None, cache_dir: Path = CACHE_DIR, concurrency: int | None = None,
                 max_tokens: int | None = None, tiering: bool = True) -> None:
        self.settings = get_settings()
        self.client = client or OpenRouterClient()
        self.model = model or self.settings.openrouter_model_fingerprint
        self.fast_model = (fast_model if fast_model is not None else self.settings.openrouter_model_fingerprint_fast) or None
        if not tiering or self.fast_model == self.model:
            self.fast_model = None
        self.prompt = load_prompt(PROMPT_NAME)
        self.repair_prompt = load_prompt(REPAIR_PROMPT_NAME)
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_tokens = max_tokens or self.settings.fingerprint_max_tokens
        self.max_calls = self.settings.fingerprint_max_calls_per_job
        self.calls = 0
        self._semaphore = asyncio.Semaphore(concurrency or self.settings.fingerprint_concurrency)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.openrouter_api_key)

    @property
    def version(self) -> str:
        return str(self.prompt.get("version", 0))

    def _cache_key(self, *, model: str, products: list[str], text: str, prompt_version: str | None = None) -> str:
        digest = hashlib.sha256()
        digest.update((prompt_version or self.version).encode())
        digest.update(model.encode())
        digest.update(json.dumps(sorted(products)).encode())
        digest.update(text.encode())
        return digest.hexdigest()

    def _read_cache(self, key: str) -> dict[str, Any] | None:
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return data.get("payload", data) if isinstance(data, dict) else None

    def _write_cache(self, key: str, payload: dict[str, Any], meta: dict[str, Any]) -> None:
        (self.cache_dir / f"{key}.json").write_text(json.dumps({"meta": meta, "payload": payload}, indent=1))

    async def _call(self, *, model: str, system: str, user: str, key: str, meta: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
        cached = self._read_cache(key)
        if cached is not None:
            return cached, True
        if not self.enabled:
            return None, False
        if self.calls >= self.max_calls:
            logger.warning("fingerprint_budget_exhausted calls=%s", self.calls)
            return None, False
        async with self._semaphore:
            self.calls += 1
            try:
                payload = await self.client.chat_json(
                    model=model, system=system, user=user, max_tokens=self.max_tokens,
                    temperature=0.0, timeout=300.0, retries=3,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("fingerprint_failed model=%s meta=%s error=%s", model, meta, exc)
                return None, False
        if "raw" in payload and "regions" not in payload:
            (self.cache_dir / f"{key}.failed.txt").write_text(str(payload.get("raw")))
            logger.warning("fingerprint_unparsed model=%s meta=%s", model, meta)
            return None, False
        self._write_cache(key, payload, meta)
        return payload, False

    @staticmethod
    def _needs_promotion(described: Fingerprint, part: SketchPart, doc: ParsedDocument, aliases: list[str]) -> str | None:
        """Why the fast model's description of this part is not enough."""
        if described.rejected:
            return "rejected:" + described.rejected[0]
        described_grids = {g.grid_index for g in described.grids if g.rows}
        for index in part.grid_indexes:
            rows = (doc.tables or [])[index]
            if _mentions("\n".join(" ".join(r) for r in rows), aliases) and any(_has_values(r) for r in rows) \
                    and index not in described_grids:
                return f"grid{index}:product_rows_not_described"
        return None

    async def _describe_part(self, part: SketchPart, *, doc: ParsedDocument, products: list[str],
                             listed: list[str], aliases: list[str], title: str, url: str) -> tuple[Fingerprint, bool, str]:
        user = self.prompt["user_template"].format(
            products=", ".join(listed), title=title or "(untitled)", url=url,
            part=part.index + 1, parts=part.total, head=part.head or "(none)",
            grids=part.grids_text or "(none)", prose=part.prose_text or "(none)",
        )
        tiers = [m for m in (self.fast_model, self.model) if m]
        last: Fingerprint | None = None
        last_cached = False
        promotions: list[str] = []
        for tier in tiers:
            key = self._cache_key(model=tier, products=products, text=part.text)
            meta = {"version": self.version, "model": tier, "part": part.index, "parts": part.total, "url": url,
                    "products": sorted(products), "grids": list(part.grid_indexes)}
            payload, cached = await self._call(model=tier, system=self.prompt["system"], user=user, key=key, meta=meta)
            if payload is None:
                continue
            described = parse_fingerprint(payload, doc=doc, shown=set(part.grid_indexes), model=tier)
            if tier != self.model:
                reason = self._needs_promotion(described, part, doc, aliases)
                if reason:
                    logger.info("fingerprint_promoted url=%s part=%s reason=%s", url, part.index, reason)
                    last, last_cached = described, cached
                    promotions.append(f"part{part.index}:{reason}")
                    continue
            described.promotions.extend(promotions)
            return described, cached, tier
        if last is not None:
            return last, last_cached, tiers[0]
        return Fingerprint(model=self.model), False, ""

    async def fingerprint(
        self,
        doc: ParsedDocument,
        *,
        products: list[str],
        generics: dict[str, str | None] | None = None,
        title: str = "",
        url: str = "",
    ) -> Fingerprint:
        aliases: list[str] = []
        listed: list[str] = []
        for product in products:
            generic = (generics or {}).get(product)
            aliases.extend(product_aliases(product, generic))
            listed.append(f"{product} ({generic})" if generic else product)
        parts = sketch_document(doc, aliases=aliases)
        result = Fingerprint(model=self.model, parts=len(parts), cached=True)
        if not parts:
            return result
        result.shown_grids = tuple(i for part in parts for i in part.grid_indexes)
        described = await asyncio.gather(*(
            self._describe_part(part, doc=doc, products=products, listed=listed, aliases=aliases, title=title, url=url)
            for part in parts
        ))
        for part_result, cached, tier in described:
            result.extend(part_result)
            result.cached = result.cached and cached
            if tier:
                result.tiers[tier] = result.tiers.get(tier, 0) + 1
        result.calls = self.calls
        return result

    async def repair(self, doc: ParsedDocument, region: GridRegion, failures: list[str], *,
                     products: list[str], title: str = "") -> GridRegion | None:
        """One round: the model re-describes a grid given what the reader could not verify."""
        rows = (doc.tables or [])[region.grid_index] if region.grid_index < len(doc.tables or []) else None
        if rows is None or not failures:
            return None
        grid_text = _grid_lines(rows, None)
        previous = json.dumps(region.raw, indent=1)
        failures_text = "\n".join(f"- {f}" for f in failures)
        user = self.repair_prompt["user_template"].format(
            products=", ".join(products), title=title or "(untitled)", grid_index=region.grid_index,
            grid=grid_text, previous=previous, failures=failures_text,
        )
        key = self._cache_key(
            model=self.model, products=products, text=f"{previous}\n{failures_text}\n{grid_text}",
            prompt_version=f"repair{self.repair_prompt.get('version', 0)}",
        )
        meta = {"version": f"repair{self.repair_prompt.get('version', 0)}", "model": self.model, "grid": region.grid_index}
        payload, _cached = await self._call(model=self.model, system=self.repair_prompt["system"], user=user, key=key, meta=meta)
        if payload is None:
            return None
        described = parse_fingerprint(payload, doc=doc, shown={region.grid_index}, model=self.model)
        return described.grid(region.grid_index)
