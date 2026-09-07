"""Derive each column's period from the table's own span geometry.

Nothing here knows any issuer. An HTML table is expanded into a grid so that a
header cell occupies every column it spans, and a value column's period is then
whatever the header cells above it say - which is what the browser shows a human
and what the markup states unambiguously.

This is the structure the literature says filing agents encode and naive
flatteners destroy: the Stanford EDGAR Filings Dataset (arXiv 2606.18192)
describes filers "exploding a single semantic header into distinct table rows",
and PubTables-1M treats the spanning cell as a first-class table component.
"""
from __future__ import annotations

import re

MONTHS = {m: i for i, m in enumerate(
    ["january","february","march","april","may","june","july","august",
     "september","october","november","december"], start=1)}
LENGTH = {"three": 3, "six": 6, "nine": 9, "twelve": 12}
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
LENGTH_RE = re.compile(r"\b(three|six|nine|twelve)\s+months?\s+ended\b", re.I)
MONTH_RE = re.compile(r"\b(" + "|".join(MONTHS) + r")\b", re.I)
ORDINAL = {"first": 3, "second": 6, "third": 9, "fourth": 12}
ORDINAL_RE = re.compile(r"\b(first|second|third|fourth)\s+quarter\b", re.I)


def to_grid(table) -> list[list[str]]:
    """Expand colspan/rowspan so every cell occupies the columns it covers."""
    filled: dict[tuple[int, int], str] = {}
    for r, tr in enumerate(table.find_all("tr")):
        col = 0
        for cell in tr.find_all(["td", "th"]):
            while (r, col) in filled:
                col += 1
            text = re.sub(r"\s+", " ", cell.get_text(" ", strip=True))
            try:
                span = max(1, int(cell.get("colspan", 1)))
                rows_ = max(1, int(cell.get("rowspan", 1)))
            except ValueError:
                span, rows_ = 1, 1
            for dr in range(rows_):
                for dc in range(span):
                    filled[(r + dr, col + dc)] = text
            col += span
    if not filled:
        return []
    height = max(r for r, _ in filled) + 1
    width = max(c for _, c in filled) + 1
    return [[filled.get((r, c), "") for c in range(width)] for r in range(height)]


def _figure(text: str) -> bool:
    """A reported number, as opposed to a year standing in a column heading."""
    text = text.strip()
    if not re.fullmatch(r"\(?\$?-?[\d,]+(?:\.\d+)?\)?%?", text):
        return False
    return not re.fullmatch(r"(?:19|20)\d{2}", text)


def column_periods(grid: list[list[str]]) -> dict[int, str]:
    """Period label per column, read from the header cells stacked above it.

    A header row is one that carries no figures. Everything those rows say about
    a column - the length phrase, the month, the year - is concatenated, so a
    heading split across three rows reads as one statement again.
    """
    header_rows = []
    for row in grid:
        if any(_figure(cell) for cell in row):
            break
        header_rows.append(row)
    if not header_rows:
        return {}

    periods: dict[int, str] = {}
    width = max(len(r) for r in grid)
    for col in range(width):
        stacked = " ".join(
            row[col] for row in header_rows if col < len(row) and row[col]
        )
        # Fall back to whatever the header rows say overall for the length and
        # month when this column only carries its year - the usual shape, since
        # the length spans every column and the year does not.
        whole = " ".join(" ".join(r) for r in header_rows)
        year = YEAR_RE.search(stacked)
        if not year:
            continue
        length_hit = LENGTH_RE.search(stacked) or LENGTH_RE.search(whole)
        month_hit = MONTH_RE.search(stacked) or MONTH_RE.search(whole)
        if length_hit and month_hit:
            months = LENGTH[length_hit.group(1).lower()]
            month = MONTHS[month_hit.group(1).lower()]
        else:
            ordinal = ORDINAL_RE.search(stacked) or ORDINAL_RE.search(whole)
            if not ordinal:
                continue
            months = 3
            month = ORDINAL[ordinal.group(1).lower()]
        quarter = (month + 2) // 3
        periods[col] = f"{year.group(1)}Q{quarter}" if months == 3 else \
                       f"{months}m{year.group(1)}"
    return periods
