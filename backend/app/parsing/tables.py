"""Read product revenue straight out of an earnings-release revenue table.

Earnings exhibits use a stable layout: a header naming the period, a row of year
columns, then one row per product. Parsing that structure is deterministic, so it
does not depend on the extraction model choosing to return a given row - which it
does inconsistently, dropping whole tables on some runs.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from app.parsing.evidence import product_aliases
from app.parsing.periods import MONTH_WORDS, MONTHS, quarter_of_month
from app.quality.candidate_filters import KNOWN_PEER_BRANDS
from app.quality.comparative import ABS_TOLERANCE, parse_numbers

_PERIOD_HEADER_RE = re.compile(
    r"\b(three|six|nine|twelve)\s+months?\s+ended\s+([A-Za-z]{3,9})", re.I
)
_YEAR_HEADER_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_YEAR_ENDED_RE = re.compile(r"\byear\s+ended\s+([A-Za-z]{3,9})", re.I)
_FOOTNOTE_RE = re.compile(r"\(\d\)")

PERIOD_TYPE_BY_MONTHS = {3: "quarterly", 6: "six_month", 9: "nine_month", 12: "annual"}


def clean_label(cell: str) -> str:
    """Product label without trademark marks or footnote references."""
    text = _FOOTNOTE_RE.sub("", cell or "")
    text = text.replace("®", " ").replace("™", " ").replace("©", " ")
    return re.sub(r"\s+", " ", text).strip(" :")


def _period_header(rows: list[list[str]]) -> tuple[int, int] | None:
    """(period length in months, period-end month) declared by a table header."""
    for row in rows[:6]:
        joined = " ".join(row)
        match = _PERIOD_HEADER_RE.search(joined)
        if match:
            month = MONTHS.get(match.group(2).lower())
            if month:
                return MONTH_WORDS.get(match.group(1).lower(), 3), month
        annual = _YEAR_ENDED_RE.search(joined)
        if annual:
            month = MONTHS.get(annual.group(1).lower())
            if month:
                return 12, month
    return None


def _year_columns(rows: list[list[str]]) -> list[int]:
    """Year labels in column order, from the header row that lists them."""
    for row in rows[:8]:
        years = [int(y) for cell in row for y in _YEAR_HEADER_RE.findall(cell)]
        if len(years) >= 2:
            return years
    return []


def _scope_for(label: str, product: str) -> str:
    normalized = label.lower()
    base = product.lower()
    if normalized in {base, f"total {base}"}:
        return "Product family"
    return "Formulation-specific"


def _matches_product(label: str, aliases: list[str], product: str) -> bool:
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


def _row_is_consistent(values: list[float]) -> bool:
    """Confirm the column layout via the table's own change column."""
    if len(values) < 3:
        return True
    return abs((values[0] - values[1]) - values[2]) <= ABS_TOLERANCE


def extract_revenue_rows(
    tables: Iterable[list[list[str]]],
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Revenue candidates read directly from table rows, one per period column."""
    aliases = product_aliases(product, generic, extra=extra_aliases)
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, float, str]] = set()

    for rows in tables or []:
        header = _period_header(rows)
        years = _year_columns(rows)
        if not header or not years:
            continue
        months, month = header
        quarter = quarter_of_month(month)
        period_type = PERIOD_TYPE_BY_MONTHS.get(months, "unknown")

        for row in rows:
            if not row:
                continue
            label = clean_label(row[0])
            if not label or not _matches_product(label, aliases, product):
                continue
            values = parse_numbers(" ".join(row[1:]))
            if len(values) < 2:
                continue
            if not _row_is_consistent(values):
                continue
            # A confirmed change column means exactly two period columns per block,
            # so later years in the header belong to a second (year-to-date) block.
            if len(values) >= 3 and abs((values[0] - values[1]) - values[2]) <= ABS_TOLERANCE:
                period_years = years[:2]
            else:
                period_years = years[: len(values)]
            quote = " ".join(cell for cell in row if cell and cell.strip())
            scope = _scope_for(label, product)
            for index, year in enumerate(period_years):
                value = values[index]
                period = f"{year}Q{quarter}" if months == 3 else str(year)
                key = (period, round(value, 3), scope)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "period": period,
                        "period_type": period_type,
                        "value_reported": value,
                        "value_normalized_usd_millions": None,
                        "currency": "USD",
                        "unit": "millions",
                        "revenue_scope": scope,
                        "formulation": None if scope == "Product family" else label,
                        "source_quote": quote,
                        "product_mentioned_in_quote": True,
                        "is_company_total": False,
                        "confidence": 0.75,
                        "extraction_method": "table",
                        "_from_table": True,
                    }
                )
    return candidates
