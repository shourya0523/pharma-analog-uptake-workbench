"""Read an analyst's hand-prepared file of cited peak-sales estimates.

Only the estimate types the selector actually reads are accepted. An
`observed` peak is derived from the product's own annual series, so a row
claiming one would be imported, stored and never consulted - accepting it
would be this file quietly disagreeing with what the selector does.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from typing import TextIO

from app.domain.models import PeakEstimateType

# The estimate types a citation can carry. Derived from the vocabulary minus
# the one the selector derives for itself, so a new estimate type is importable
# by default and has to be excluded deliberately.
DERIVED_ESTIMATE_TYPES = frozenset({PeakEstimateType.OBSERVED.value})
CITED_ESTIMATE_TYPES = frozenset(item.value for item in PeakEstimateType) - DERIVED_ESTIMATE_TYPES


class PeakImportError(ValueError):
    pass


@dataclass(frozen=True)
class PeakImportRow:
    product: str
    estimate_type: str
    value: float
    currency: str
    geography: str
    revenue_scope: str
    as_of_date: date
    source_url: str


REQUIRED_FIELDS = {
    "product",
    "estimate_type",
    "value",
    "currency",
    "geography",
    "revenue_scope",
    "as_of_date",
    "source_url",
}


def read_peak_sales_csv(stream: TextIO) -> list[PeakImportRow]:
    rows: list[PeakImportRow] = []
    for line_number, raw in enumerate(csv.DictReader(stream), start=2):
        missing = sorted(field for field in REQUIRED_FIELDS if not (raw.get(field) or "").strip())
        if missing:
            raise PeakImportError(f"Row {line_number} missing required fields: {', '.join(missing)}")
        estimate_type = raw["estimate_type"].strip().lower()
        if estimate_type not in CITED_ESTIMATE_TYPES:
            raise PeakImportError(
                f"Row {line_number} has an estimate_type no peak selection reads: "
                f"{estimate_type!r}. Citable types are "
                f"{', '.join(sorted(CITED_ESTIMATE_TYPES))}."
            )
        source_url = raw["source_url"].strip()
        if not source_url.startswith(("https://", "http://")):
            raise PeakImportError(f"Row {line_number} source_url must be an HTTP(S) citation")
        try:
            rows.append(
                PeakImportRow(
                    product=raw["product"].strip(),
                    estimate_type=estimate_type,
                    value=float(raw["value"]),
                    currency=raw["currency"].strip().upper(),
                    geography=raw["geography"].strip(),
                    revenue_scope=raw["revenue_scope"].strip(),
                    as_of_date=date.fromisoformat(raw["as_of_date"].strip()),
                    source_url=source_url,
                )
            )
        except (TypeError, ValueError) as exc:
            raise PeakImportError(f"Row {line_number} contains an invalid value: {exc}") from exc
    return rows

