from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from typing import TextIO


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
        if estimate_type not in {"consensus", "modeled", "observed"}:
            raise PeakImportError(f"Row {line_number} has invalid estimate_type")
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

