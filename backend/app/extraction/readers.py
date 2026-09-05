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
from dataclasses import dataclass, field
from typing import Any

from app.parsing.grids import is_value_token, is_year_token


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
    row_index: int | None = None          # the grid row the figure was read from
    notes: tuple[str, ...] = field(default_factory=tuple)
    geography_label: str | None = None    # the geography as the document printed it
    described_product: str | None = None  # the product the description assigned the row to
    line_kind: str = ""                   # the description's line kind, when read from one
    provisional: bool = False             # fills empty cells only; never seeds derivation on its own

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_label": self.product_label,
            "geography_label": self.geography_label,
            "described_product": self.described_product,
            "line_kind": self.line_kind,
            "provisional": self.provisional,
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
    failures: list[Any] = field(default_factory=list)     # described.VerificationFailure
    rejected: list[str] = field(default_factory=list)     # descriptions the parser did not ground
    mode: str = "degraded"
    dropped_prose: dict[str, int] = field(default_factory=dict)
    repairs: int = 0
    repaired_grids: list[int] = field(default_factory=list)


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


def _same_amount(a: Observation, b: Observation) -> bool:
    """Two observations of the same period state the same figure, to the coarser one's precision."""
    from app.extraction.units import UNIT_SCALE_TO_MILLIONS

    def millions(o: Observation) -> float | None:
        scale = UNIT_SCALE_TO_MILLIONS.get(o.unit_label or "")
        return None if scale is None else o.value_as_reported * scale

    x, y = millions(a), millions(b)
    if x is None or y is None:
        return True
    return abs(x - y) <= 0.05 * max(1.0, min(abs(x), abs(y)))
