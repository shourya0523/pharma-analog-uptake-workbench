"""Read every revenue observation a parsed document states for one product.

This is the one place the pipeline turns a document into numbers, and it is
deliberately indifferent to where the document came from. A parsed document
is text plus grids (see ``app.parsing.grids``); each grid's header is read
into column semantics by ``app.extraction.columns`` and each product row is
aligned to those columns by constraint; sentences are read by
``app.extraction.prose``. Nothing here names an issuer or a layout.

What a row is *of* is read from its label, generically:

* a label is split into product words and a geography word ("Biktarvy –
  U.S.", "OPSUMIT US", "Intl", "WW");
* a row whose label is only a geography, or has no label at all, belongs to
  the last product named above it - the way a reader takes "Intl" under
  "OPSUMIT" to mean Opsumit's international sales;
* an unlabelled row directly under a product's geography rows is that
  product's total only if it actually equals the sum of the rows above it;
* a label that names the product plus a qualifier ("Alliance revenue -
  Adempas/Verquvo", "Nebulized Tyvaso" when the product asked for is Tyvaso)
  is a *different line item* from the product's own line, and is ranked
  behind an exact line rather than merged with it.

The reader emits observations, not conclusions: the same period can come out
several times from one document (a quarter and its prior-year comparative,
a total and its geographies), and reconciling those across documents is the
job of ``app.extraction.series``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.domain.models import ParsedDocument
from app.extraction.columns import (
    _GEO_HEAD_RE,
    Alignment,
    ColumnLayout,
    align_row,
    build_layouts,
    split_geography,
)
from app.extraction.fingerprint import detect_currency, detect_unit
from app.extraction.prose import read_prose
from app.parsing.evidence import product_aliases
from app.parsing.periods import MONTHS
from app.parsing.grids import is_value_token, is_year_token
from app.parsing.tables import clean_label
from app.quality.candidate_filters import KNOWN_PEER_BRANDS

_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_MARKER_RE = re.compile(r"\(\s*(\d{1,2})\s*\)")
_FOOTNOTE_DEF_RE = re.compile(r"\(\s*(\d{1,2})\s*\)\s*([^()]{5,300}?)(?=\s*\(\s*\d{1,2}\s*\)|\n|$)")
_MONTH_NAME = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_ACQUISITION_DATE_RE = re.compile(
    rf"\b(?:acqui\w+|closed|closing|completed|effective)\b[^.;]{{0,80}}?\bon\s+(?P<month>{_MONTH_NAME})\.?\s+(?P<day>\d{{1,2}}),?\s+(?P<year>(?:19|20)\d{{2}})",
    re.I,
)
_FOOTNOTE_RE = re.compile(r"\(\s*[a-z0-9]{1,2}\s*\)", re.I)
_SEPARATOR_RE = re.compile(r"[/,;|:–—-]+|\band\b|\bor\b", re.I)
_TOTAL_WORD_RE = re.compile(r"^(?:total|net|sales|revenues?|product|products|net\s+sales)$", re.I)


@dataclass(frozen=True)
class Observation:
    """One figure a document states, with everything needed to reconcile it."""

    product_label: str
    period: str
    period_type: str
    value_as_reported: float
    unit_label: str
    currency: str
    unit_declared: bool
    geography: str | None
    covers: tuple[str, str] | None
    source_quote: str
    method: str                       # "grid" | "prose"
    layout_signature: str
    verified: tuple[str, ...]
    specificity: int                  # 0 exact line, 1 qualified line
    line_item: str = "exact"          # "exact" | "qualified" (a different line item that names the product)
    source_url: str = ""
    source_id: str = ""
    table_index: int = -1
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_label": self.product_label,
            "period": self.period,
            "period_type": self.period_type,
            "value_as_reported": self.value_as_reported,
            "unit_label": self.unit_label,
            "currency": self.currency,
            "unit_declared": self.unit_declared,
            "geography": self.geography,
            "covers": list(self.covers) if self.covers else None,
            "source_quote": self.source_quote,
            "method": self.method,
            "layout_signature": self.layout_signature,
            "verified": list(self.verified),
            "specificity": self.specificity,
            "line_item": self.line_item,
            "source_url": self.source_url,
            "source_id": self.source_id,
            "notes": list(self.notes),
        }


@dataclass
class ReadReport:
    observations: list[Observation]
    skipped: list[str]


def _is_year_row(row: list[str]) -> bool:
    """A header row listing the year columns.

    "2024 | 2023 | 2022" on its own, or "Years Ended December 31 | 2024 |
    2023 | 2022" with its label: every numeric cell is a year and there are
    at least two of them. A revenue row prints thousands separators, so two
    bare four-digit years in a row are column labels, not values.
    """
    values = [cell for cell in row if is_value_token(cell)]
    if not values or not all(is_year_token(cell) for cell in values):
        return False
    if len(values) == len(row):
        return True
    return len(values) >= 2 and all(not re.search(r"\d", cell) for cell in row if not is_value_token(cell))


def _is_data_row(row: list[str]) -> bool:
    if len(row) < 2 or _is_year_row(row):
        return False
    return all(is_value_token(cell) for cell in row[1:]) or all(is_value_token(cell) for cell in row)


def _row_label(row: list[str]) -> tuple[str, list[str]]:
    """(label, value tokens). A row of only values has an empty label."""
    if all(is_value_token(cell) for cell in row):
        return "", list(row)
    return row[0], list(row[1:])


def _matches_aliases(label: str, aliases: list[str]) -> bool:
    normalized = label.lower()
    if not any(alias.lower() in normalized for alias in aliases):
        return False
    # A brand that is a word of this product's own name ("Tyvaso" inside
    # "Tyvaso DPI") is not a competing brand.
    own_words = {word for alias in aliases for word in alias.lower().split()}
    for brand in KNOWN_PEER_BRANDS:
        if brand in own_words:
            continue
        if re.search(rf"\b{re.escape(brand)}\b", normalized):
            return False
    return True


def _specificity(label: str, aliases: list[str]) -> int:
    """0 when the label is the product line itself, 1 when it is a qualified line.

    Issuers report several names on one line ("SIMPONI / SIMPONI ARIA",
    "INVEGA SUSTENNA / XEPLION / INVEGA TRINZA / TREVICTA", "ZYTIGA /
    abiraterone acetate"): the line is the product's when one of the
    slash-separated names is exactly the product. A qualifier on the
    product's own name ("Nebulized Tyvaso", "Alliance revenue - Adempas",
    "Remodulin delivery pumps and supplies") makes it a different line.
    """
    text = clean_label(label).lower()
    names = {a.lower() for a in aliases}
    segments = [seg.strip(" -–—:") for seg in re.split(r"\s*/\s*", text)]
    if any(seg in names or re.sub(r"\s+", " ", seg) in names for seg in segments):
        if len(segments) > 1 or text in names:
            return 0
    for alias in sorted(aliases, key=len, reverse=True):
        text = text.replace(alias.lower(), " ")
    text = _FOOTNOTE_RE.sub(" ", text)
    text = _SEPARATOR_RE.sub(" ", text)
    leftover = [w for w in text.split() if not _TOTAL_WORD_RE.match(w)]
    return 0 if not leftover else 1


def _footnotes(text: str) -> dict[int, str]:
    """Footnote number -> its text, for markers rows carry."""
    notes: dict[int, str] = {}
    for match in _FOOTNOTE_DEF_RE.finditer(text):
        number = int(match.group(1))
        body = match.group(2).strip()
        if number not in notes and len(body.split()) >= 2:
            notes[number] = body
    return notes


def _acquisition_date(text: str) -> str | None:
    """The one acquisition date a note states, as ISO, or None."""
    dates = set()
    for match in _ACQUISITION_DATE_RE.finditer(text):
        month = MONTHS.get(match.group("month").lower())
        if month:
            dates.add(f"{int(match.group('year')):04d}-{month:02d}-{int(match.group('day')):02d}")
    return dates.pop() if len(dates) == 1 else None


def _quarter_bounds(period: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"(\d{4})Q([1-4])", period or "")
    if not match:
        return None
    year, quarter = int(match.group(1)), int(match.group(2))
    from calendar import monthrange

    start_month = quarter * 3 - 2
    end_month = quarter * 3
    return (
        f"{year}-{start_month:02d}-01",
        f"{year}-{end_month:02d}-{monthrange(year, end_month)[1]:02d}",
    )


def _period_bounds(period: str, period_type: str) -> tuple[str, str] | None:
    if period_type == "quarterly":
        return _quarter_bounds(period)
    match = re.fullmatch(r"(\d{4})", period or "")
    if not match:
        return None
    year = int(match.group(1))
    months = {"six_month": 6, "nine_month": 9, "annual": 12}.get(period_type)
    if not months:
        return None
    from calendar import monthrange

    return f"{year}-01-01", f"{year}-{months:02d}-{monthrange(year, months)[1]:02d}"


def _coverage_from_acquisition(period: str, period_type: str, acquired_on: str) -> tuple[str, str] | None:
    """An acquirer's figure for the period containing the closing date starts there."""
    bounds = _period_bounds(period, period_type)
    if bounds is None:
        return None
    start, end = bounds
    if start < acquired_on <= end:
        return acquired_on, end
    return None


_AS_OF_RE = re.compile(
    rf"\b(?:as\s+of|through|thru)\s+(?P<month>{_MONTH_NAME})\.?\s+(?P<day>\d{{1,2}}),?\s+(?P<year>(?:19|20)\d{{2}})",
    re.I,
)


def _as_of_coverage(sentence: str, period: str, period_type: str) -> tuple[str, str] | None:
    """A figure stated "as of" a date inside its period covers only up to that date."""
    bounds = _period_bounds(period, period_type)
    if bounds is None:
        return None
    start, end = bounds
    for match in _AS_OF_RE.finditer(sentence):
        month = MONTHS.get(match.group("month").lower())
        if not month:
            continue
        date = f"{int(match.group('year')):04d}-{month:02d}-{int(match.group('day')):02d}"
        if start <= date < end:
            return start, date
    return None


_NOT_REVENUE_GRID_RE = re.compile(
    r"\b(?:inventor(?:y|ies)|raw\s+materials|work[-\s]in[-\s]pro(?:cess|gress)|finished\s+goods|"
    r"total\s+assets|total\s+liabilities|accounts\s+receivable|accrued|deferred\s+tax|"
    r"cost\s+of\s+(?:product\s+)?sales|research\s+and\s+development\s+expense|per\s+share)\b",
    re.I,
)


def _is_revenue_grid(header_rows: list[list[str]], body: list[list[str]]) -> bool:
    """A grid of inventories, balances or expenses is not a revenue grid.

    Its rows may still name the product ("Remodulin delivery pumps" among
    inventories, "Adcirca" royalty expense), so the label alone does not
    decide; the vocabulary of the whole grid does.
    """
    labels = " ".join(row[0] for row in body if row and not is_value_token(row[0]))
    caption = _header_text(header_rows[-2:])
    return not _NOT_REVENUE_GRID_RE.search(labels + " " + caption)


_GENERIC_PRODUCT_LINE_RE = re.compile(
    r"^(?:net\s+)?product\s+(?:sales|revenues?)(?:,\s*net)?$|^(?:net\s+)?(?:sales|revenues?)\s+of\s+products?$",
    re.I,
)


def _is_sole_named_product(text: str, product: str, aliases: list[str], issuer_products: Iterable[str] | None) -> bool:
    """True when this is the only product of the issuer's catalog the document names.

    A filing from a one-product company states "Product sales, net" without
    naming the product on the line; when the document names no other
    catalog product, that line can only be this one.
    """
    if not issuer_products:
        return False
    lowered = text.lower()
    if not any(alias.lower() in lowered for alias in aliases):
        return False
    others = [p for p in issuer_products if p.lower() != product.lower()]
    if any(other.lower() in lowered for other in others):
        return False
    # The document itself has to say that product sales are this product's:
    # "YUTREPIA product sales", "product sales ... of YUTREPIA".
    for alias in aliases:
        escaped = re.escape(alias.lower())
        if re.search(rf"{escaped}[^.]{{0,40}}product\s+sales|product\s+sales[^.]{{0,60}}\b(?:of|from)\s+{escaped}", lowered):
            return True
    return False


def _years_in(text: str) -> list[int]:
    return sorted({int(y) for y in _YEAR_RE.findall(text) if 1990 <= int(y) <= 2039})


def _split_table(table: list[list[str]]) -> tuple[list[list[str]], list[list[str]], list[list[str]]]:
    """(header rows, body rows, footer rows) of a grid."""
    first = next((i for i, row in enumerate(table) if _is_data_row(row)), None)
    if first is None:
        return table, [], []
    last = max(i for i, row in enumerate(table) if _is_data_row(row))
    return table[:first], table[first : last + 1], table[last + 1 :]


def _header_text(rows: Iterable[list[str]]) -> str:
    return " ".join(" ".join(cell for cell in row if cell) for row in rows)


def _fits(layout: ColumnLayout, rows: list[list[str]]) -> bool:
    """A layout inherited from an earlier grid must fit this grid's rows."""
    widths = [len(_row_label(row)[1]) for row in rows if _is_data_row(row)]
    if not widths:
        return False
    columns = len(layout.columns)
    return sum(1 for w in widths if w <= columns) >= max(1, len(widths) // 2)


def _shift_into_label(label: str, tokens: list[str], excess: int) -> tuple[str, list[str]]:
    """Move leading tokens into the label when a product name ends in a number.

    "Gardasil 9" and "Pneumovax 23" arrive as label "Gardasil" with the 9
    leading the values. The row then has one more token than the header has
    columns, and the surplus can only be a name.
    """
    moved = tokens[:excess]
    if excess != 1 or any(is_year_token(t) or not re.fullmatch(r"\d{1,2}", t) for t in moved):
        return label, tokens
    return f"{label} {' '.join(moved)}".strip(), tokens[excess:]


def read_grids(
    doc: ParsedDocument,
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
    source_url: str = "",
    described_layouts: dict[int, list[ColumnLayout]] | None = None,
    issuer_products: Iterable[str] | None = None,
) -> ReadReport:
    """Every observation the document's grids state for this product.

    ``described_layouts`` are column descriptions a fingerprinter produced
    for specific grids. They are tried first, but a description only counts
    if the product row's own arithmetic holds under it; the header grammar
    remains the fallback, so a fingerprinter's mistake costs a value, never
    invents one.
    """
    aliases = product_aliases(product, generic, extra=extra_aliases)
    context_head = doc.full_text[:4000]
    doc_years = _years_in(doc.full_text)
    observations: list[Observation] = []
    skipped: list[str] = []
    inherited: ColumnLayout | None = None
    footnotes = _footnotes(doc.full_text)
    sole_product = _is_sole_named_product(doc.full_text, product, aliases, issuer_products)

    for table_index, table in enumerate(doc.tables or []):
        header_rows, body, footer_rows = _split_table(table)
        if body and not _is_revenue_grid(header_rows, body):
            continue
        if not body:
            # A grid of header rows and nothing else (a page break split the
            # header from its rows) declares the columns of the grid below.
            header_only = build_layouts(_header_text(header_rows), context=context_head, year_candidates=doc_years)
            usable_only = [l for l in header_only if l.usable]
            if len(usable_only) == 1:
                inherited = usable_only[0]
            continue
        header_text = _header_text(header_rows)
        local_years = _years_in(header_text + " " + _header_text(footer_rows))
        layouts = build_layouts(
            header_text,
            context=context_head,
            year_candidates=local_years or doc_years,
        )
        usable = [l for l in layouts if l.usable]
        described = [l for l in (described_layouts or {}).get(table_index, []) if l.usable]
        if described:
            usable = described + [l for l in usable if l.signature not in {d.signature for d in described}]
        if not usable and inherited is not None and _fits(inherited, body):
            # A page header applies to every grid below it; a grid that
            # restates only a section label inherits the page's columns.
            usable = [inherited]
        if not usable:
            reasons = ";".join(layouts[0].notes) if layouts else "no_layout"
            if any(_matches_aliases(_row_label(r)[0], aliases) for r in body):
                skipped.append(f"table{table_index}:{reasons}")
            continue
        if len(usable) == 1:
            inherited = usable[0]
        # Unit: the grid's own header, then its caption/context, then the
        # document head. A grid that declares nothing keeps its values with
        # unit_declared=False so the series stage can decide.
        unit_label, unit_declared = usable[0].unit_label, usable[0].unit_declared
        currency, currency_declared = usable[0].currency, usable[0].currency_declared
        if not unit_declared:
            unit_label, unit_declared = detect_unit([[header_text]], context_head)
        if not unit_declared:
            for layout in usable:
                if layout.unit_declared:
                    unit_label, unit_declared = layout.unit_label, True
                    break
        if not currency_declared:
            currency, currency_declared = detect_currency([[header_text]], context_head)

        group_label = ""
        group_markers: set[int] = set()
        section_markers: set[int] = set()
        group_rows: list[tuple[str | None, dict[int, float]]] = []
        for row in body:
            if not _is_data_row(row):
                # A single-cell group label ("OPSUMIT -", "Other products:",
                # "PULMONARY HYPERTENSION (4)"). Its footnote markers apply to
                # the rows beneath it.
                raw = " ".join(row)
                markers = {int(m) for m in _MARKER_RE.findall(raw)}
                text = clean_label(raw)
                text = re.sub(r"[\s\-–—:]+$", "", text)
                if text and not is_value_token(text):
                    label_part, _geo = split_geography(text)
                    if label_part:
                        group_label = label_part
                        group_markers = markers
                        section_markers = markers if text.isupper() else section_markers
                        group_rows = []
                continue
            label, tokens = _row_label(row)
            row_markers = {int(m) for m in _MARKER_RE.findall(label)}
            label_part, geography = split_geography(clean_label(label))
            # Three kinds of row: a product row ("OPSUMIT US", "Biktarvy"),
            # a geography row under it ("Intl", "WW"), and a row with no
            # label at all, which can only be the group's subtotal.
            unlabelled = not clean_label(label).strip()
            geography_only = not unlabelled and not label_part
            # "US Exports", "Intl (excluding Japan)": a geography with a
            # qualifier is a sub-row of the current product's group.
            geography_sub_row = (
                not unlabelled and not geography_only and geography is not None
                and group_label and not _matches_aliases(label_part, aliases)
                and _GEO_HEAD_RE.match(clean_label(label)) is not None
            )
            if geography_sub_row:
                product_part = group_label
                geography_only = True
                sub_row_qualified = True
            else:
                sub_row_qualified = False
            if unlabelled or geography_only:
                product_part = group_label
            else:
                product_part = label_part
                if geography is None or group_label.lower() != label_part.lower():
                    group_label = label_part
                    group_markers = row_markers
                    group_rows = []
            sole_product_line = False
            if product_part and not _matches_aliases(product_part, aliases):
                # "Product sales, net" in a filing whose only named catalog
                # product is this one is this product's line.
                if _GENERIC_PRODUCT_LINE_RE.match(product_part) and sole_product:
                    sole_product_line = True
                else:
                    continue
            if not product_part:
                continue

            # Every candidate layout is tried; the one the row fits without
            # blanks or label surgery wins, and a tie between different
            # readings is an ambiguity, not a choice.
            fits: list[tuple[int, ColumnLayout, Alignment]] = []
            reasons: list[str] = []
            for layout in usable:
                columns = len(layout.columns)
                row_tokens = tokens
                shifted = 0
                if len(row_tokens) > columns:
                    _label, row_tokens = _shift_into_label(label, row_tokens, len(row_tokens) - columns)
                    shifted = len(tokens) - len(row_tokens)
                    if len(row_tokens) > columns:
                        reasons.append("more_cells_than_columns")
                        continue
                alignments, reason = align_row(row_tokens, layout)
                if reason:
                    reasons.append(reason)
                    continue
                for alignment in alignments:
                    fits.append((len(alignment.gaps) + shifted, layout, alignment))
            if not fits:
                skipped.append(f"table{table_index}:{label or '<unlabelled>'}:{';'.join(sorted(set(reasons)))}")
                continue
            best_penalty = min(f[0] for f in fits)
            best = [f for f in fits if f[0] == best_penalty]
            distinct = {tuple(sorted((layout.columns[i].period, layout.columns[i].geography, v) for i, v in a.values.items())) for _, layout, a in best}
            if len(distinct) > 1:
                skipped.append(f"table{table_index}:{label or '<unlabelled>'}:ambiguous_alignment({len(distinct)})")
                continue
            _penalty, layout, alignment = best[0]
            for _once in (1,):
                if unit_declared is False and layout.unit_declared:
                    unit_label, unit_declared = layout.unit_label, True
                # An unlabelled row is the group's total only if it sums the
                # geography rows above it.
                row_geography = geography
                if unlabelled:
                    if not group_rows:
                        continue
                    parts = defaultdict(float)
                    for geo, values in group_rows:
                        for col, value in values.items():
                            parts[col] += value
                    agreeing = [
                        col for col, value in alignment.values.items()
                        if col in parts and abs(parts[col] - value) <= 0.5 * (len(group_rows) + 1) + 0.01
                    ]
                    if len(agreeing) < max(1, len(alignment.values) // 2):
                        skipped.append(f"table{table_index}:<unlabelled>:not_the_sum_of_the_group")
                        continue
                    row_geography = "Worldwide"
                elif geography is not None and geography != "Worldwide":
                    group_rows.append((geography, dict(alignment.values)))
                quote = " ".join(cell for cell in row if cell and cell.strip())
                if unlabelled:
                    quote = f"{group_label} {quote}"
                specificity = 1 if (sole_product_line or sub_row_qualified) else _specificity(product_part, aliases)
                markers = row_markers | group_markers | section_markers
                acquired_on = None
                for marker in markers:
                    note = footnotes.get(marker)
                    if note:
                        acquired_on = acquired_on or _acquisition_date(note)
                current_periods = {
                    months: max(c.period for c in layout.columns if c.kind == "value" and c.months == months and c.period)
                    for months in {c.months for c in layout.columns if c.kind == "value" and c.period}
                }
                for col, value in sorted(alignment.values.items()):
                    spec = layout.columns[col]
                    if spec.period is None:
                        continue
                    covers = spec.covers
                    row_notes = list(layout.notes)
                    if covers is None and acquired_on:
                        covers = _coverage_from_acquisition(spec.period, spec.period_type, acquired_on)
                        if covers:
                            row_notes.append("coverage_from_acquisition_footnote")
                    if spec.period == current_periods.get(spec.months):
                        row_notes.append("current_period_column")
                    if sole_product_line:
                        row_notes.append("generic_product_line")
                    observations.append(
                        Observation(
                            product_label=product_part,
                            period=spec.period,
                            period_type=spec.period_type,
                            value_as_reported=value,
                            unit_label=unit_label,
                            currency=currency,
                            unit_declared=unit_declared,
                            geography=spec.geography or row_geography,
                            covers=covers,
                            source_quote=quote,
                            method="grid",
                            layout_signature=layout.signature,
                            verified=alignment.verified,
                            specificity=specificity,
                            line_item="exact" if (sole_product_line or not specificity) else "qualified",
                            source_url=source_url,
                            source_id=doc.source_id,
                            table_index=table_index,
                            notes=tuple(row_notes),
                        )
                    )
                break
    return ReadReport(observations, skipped)


def read_document(
    doc: ParsedDocument,
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
    source_url: str = "",
    fingerprint: Any = None,
    issuer_products: Iterable[str] | None = None,
) -> ReadReport:
    """Grid observations plus sentence observations, in one list.

    ``fingerprint`` is an optional ``app.fingerprint.llm.Fingerprint``: its
    grid descriptions become candidate layouts (verified per row) and its
    prose statements become observations only when the quoted sentence is
    in the document and the value is in the sentence.
    """
    aliases = product_aliases(product, generic, extra=extra_aliases)
    described: dict[int, list[ColumnLayout]] = defaultdict(list)
    if fingerprint is not None:
        for region in fingerprint.grids_for(product, aliases):
            described[region.grid_index].append(region.layout)
    report = read_grids(
        doc, product=product, generic=generic, extra_aliases=extra_aliases, source_url=source_url,
        described_layouts=dict(described) or None, issuer_products=issuer_products,
    )
    if fingerprint is not None:
        from app.llm.grounding import quote_is_verbatim

        names = {a.lower() for a in aliases}
        for region in fingerprint.prose:
            if (region.product or "").lower() not in names:
                continue
            if not quote_is_verbatim(region.quote, doc.full_text):
                report.skipped.append(f"prose:{region.period}:quote_not_in_document")
                continue
            digits = region.quote.replace(",", "")
            if not re.search(rf"(?<![\d.]){re.escape(f'{region.value:g}')}(?![\d])", digits):
                report.skipped.append(f"prose:{region.period}:value_not_in_quote")
                continue
            report.observations.append(
                Observation(
                    product_label=product,
                    period=region.period,
                    period_type=region.period_type,
                    value_as_reported=region.value,
                    unit_label=region.unit,
                    currency=region.currency,
                    unit_declared=True,
                    geography=region.geography,
                    covers=_as_of_coverage(region.quote, region.period, region.period_type),
                    source_quote=region.quote,
                    method="prose",
                    layout_signature="llm_prose",
                    verified=("quote_in_document", "value_in_quote"),
                    specificity=1 if region.period_from_context else 0,
                    source_url=source_url,
                    source_id=doc.source_id,
                    notes=("llm_fingerprint",),
                )
            )
    for value in read_prose(doc.full_text, product=product, generic=generic, extra_aliases=extra_aliases):
        covers = _as_of_coverage(value.source_quote, value.period, value.period_type)
        report.observations.append(
            Observation(
                product_label=value.product_label,
                period=value.period,
                period_type=value.period_type,
                value_as_reported=value.value_as_reported,
                unit_label=value.unit_label,
                currency=value.currency,
                unit_declared=True,
                geography=None,
                covers=covers,
                source_quote=value.source_quote,
                method="prose",
                layout_signature="prose",
                verified=("stated_in_sentence",) if value.fingerprint_signature == "prose" else ("period_from_paragraph",),
                specificity=value.specificity,
                source_url=source_url,
                source_id=doc.source_id,
            )
        )
    return report
