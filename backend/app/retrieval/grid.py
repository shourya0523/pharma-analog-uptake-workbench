"""HTML tables as grids whose cells carry their full labels.

A results table prints its period once, in a heading above it ("FIRST-QUARTER
2026 and 2025"), groups its columns under spanning headers ("WORLDWIDE",
"U.S.") with the years beneath, and may put a product's regional lines under a
heading row naming the product. Flattened to text, a row of figures loses all
of that. Here each figure keeps it:

    [FIRST-QUARTER 2026 and 2025] Calderon | WORLDWIDE > 2026 = 411

`cells(document)` yields every figure in every table that way. Nothing is
known in advance about any issuer's layout: headers are the rows above a
table's first row of figures, and a heading is the text between the previous
table and this one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

import lxml.html

_FIGURE = re.compile(r"^\(?-?\$?\s*[\d,]+(?:\.\d+)?\)?%?$|^[—–-]$")
CONTEXT_LINES = 4


@dataclass(frozen=True)
class Cell:
    """One figure with everything printed around it."""

    table: int
    context: tuple[str, ...]  # the heading lines above the table, nearest last
    row: tuple[str, ...]  # a heading row the line sits under, then the line's own label
    column: tuple[str, ...]  # header cells above the figure, outermost first
    text: str  # the figure as printed

    def label(self) -> str:
        context = " / ".join(self.context)
        return f"[{context}] {' > '.join(self.row)} | {' > '.join(self.column)} = {self.text}"


def _text(element) -> str:
    return re.sub(r"\s+", " ", " ".join(element.itertext()).replace("\xa0", " ")).strip()


def _rows(table) -> list[list[tuple[str, int, int]]]:
    """The table's rows as (text, first column, end column), spans expanded."""
    out, carried = [], {}
    for r, tr in enumerate(table.iter("tr")):
        row, c = [], 0
        for td in tr:
            if td.tag not in ("td", "th"):
                continue
            while (r, c) in carried:
                row.append(carried.pop((r, c)))
                c = row[-1][2]
            span = int(td.get("colspan") or 1)
            cell = (_text(td), c, c + span)
            row.append(cell)
            for k in range(1, int(td.get("rowspan") or 1)):
                carried[(r + k, c)] = cell
            c += span
        out.append(row)
    return out


def _is_figure(text: str) -> bool:
    return bool(_FIGURE.match(text.replace(" ", "")))


def _cells(rows: list[list[tuple[str, int, int]]]) -> Iterator[tuple[tuple[str, ...], tuple[str, ...], str]]:
    """(row path, column path, figure) for every figure below the header rows."""
    first = next((i for i, row in enumerate(rows)
                  if row and row[0][0] and not _is_figure(row[0][0])
                  and any(_is_figure(t) for t, _, _ in row[1:] if t)), None)
    if first is None:
        return
    # Rows above the first row of figures are column headers, except a row
    # holding only a label in its first cell: that names a group of lines.
    headers, heading = [], None
    for row in rows[:first]:
        filled = [cell for cell in row if cell[0]]
        if len(filled) == 1 and filled[0][1] == 0:
            heading = filled[0][0]
        elif filled:
            headers.append(filled)
    for row in rows[first:]:
        filled = [cell for cell in row if cell[0] and cell[0] != "$"]
        if not filled:
            continue
        label = filled[0][0]
        figures = [cell for cell in filled[1:] if _is_figure(cell[0])]
        if not figures:
            heading = label  # a row naming a group, with its lines below it
            continue
        row_path = (heading, label) if heading else (label,)
        for text, a, b in figures:
            column = tuple(h[0] for header in headers for h in header if h[1] < b and a < h[2])
            yield row_path, column, text


def _context(document, table) -> tuple[str, ...]:
    """The last text lines between the previous table and this one."""
    lines: list[str] = []
    for element in document.iter():
        if element is table:
            break
        if element.tag == "table":
            lines = []
            continue
        if not isinstance(element.tag, str) or any(a.tag == "table" for a in element.iterancestors()):
            continue
        if len(element) == 0 or element.tag == "font":
            text = _text(element)
            if text and (not lines or lines[-1] != text):
                lines.append(text)
    return tuple(lines[-CONTEXT_LINES:])


def cells(document: bytes, mentioning: str | None = None) -> list[Cell]:
    """Every figure in the document's tables, labelled. With `mentioning`,
    only tables in which some row names it, and only that row's figures and
    those of rows under a heading naming it."""
    root = lxml.html.fromstring(document)
    want = mentioning.lower() if mentioning else None
    out = []
    for index, table in enumerate(root.iter("table")):
        rows = _rows(table)
        if want and not any(want in t.lower() for row in rows for t, _, _ in row[:1]):
            continue
        found = [(r, c, v) for r, c, v in _cells(rows) if not want or any(want in part.lower() for part in r)]
        if not found:
            continue
        context = _context(root, table)
        out.extend(Cell(index, context, r, c, v) for r, c, v in found)
    return out
