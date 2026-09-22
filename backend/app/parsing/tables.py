"""Read what an earnings-release revenue table says about its own rows.

Earnings exhibits use a stable layout: a header naming the period, a row of
year columns, then one row per product. That structure is what says which
tables in a filing are schedules of figures by period - and the rows of the
one our product appears in are the filer's own list of what it reports beside
it, which is where the question "is this line somebody else's product" gets a
document-supplied answer instead of a catalogue.

The cell readers here are the same ones: what a cell reports, and what a row
is labelled, decided from the cell rather than from what a caller expected.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.parsing.evidence import REVENUE_HINT_RE, product_aliases
from app.parsing.periods import MONTH_WORDS, MONTHS, fiscal_period_end
from app.quality.candidate_filters import (
    is_generic_product_revenue_label,
    names_a_competing_product,
)

_PERIOD_HEADER_RE = re.compile(
    r"\b(three|six|nine|twelve)\s+months?\s+ended\s+([A-Za-z]{3,9})\.?\s*(\d{1,2})?", re.IGNORECASE
)
_YEAR_HEADER_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_YEAR_ENDED_RE = re.compile(r"\byear\s+ended\s+([A-Za-z]{3,9})\.?\s*(\d{1,2})?", re.IGNORECASE)
_FOOTNOTE_RE = re.compile(r"\(\d\)")


def clean_label(cell: str) -> str:
    """Product label without trademark marks or footnote references."""
    text = _FOOTNOTE_RE.sub("", cell or "")
    text = text.replace("®", " ").replace("™", " ").replace("©", " ")
    return re.sub(r"\s+", " ", text).strip(" :")


# A cell that is a reported number. A bare four-digit year is a column heading
# wherever it appears, never a figure, which is the one shape a plain numeric
# parse reads wrongly.
_FIGURE_CELL_RE = re.compile(r"^\(?-?[\d,]*\d(?:\.\d+)?\)?$")
_YEAR_CELL_RE = re.compile(r"^(?:19|20)\d{2}$")
# The dashes a filer prints for "nothing here". An em dash and an en dash are
# the same statement as a zero, and a bare hyphen is too.
NIL_CELLS = frozenset({"-", "–", "—", "−"})


def cell_figure(cell: str | None) -> float | None:
    """The number a cell reports, or None where it reports none.

    A dash is a reported nothing and reads as 0.0; a year is a heading and
    reads as nothing at all. Value-returning rather than a predicate, because
    every caller that wants to know whether a cell is a figure also wants the
    figure, and a predicate makes them parse it again.
    """
    text = (cell or "").strip().replace("$", "").strip()
    if text in NIL_CELLS:
        return 0.0
    if not text or not _FIGURE_CELL_RE.match(text) or _YEAR_CELL_RE.match(text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    try:
        value = float(text.strip("()").replace(",", ""))
    except ValueError:
        return None
    return -value if negative else value


def _period_header(rows: list[list[str]]) -> tuple[int, int] | None:
    """(period length in months, period-end month) declared by a table header."""
    for row in rows[:6]:
        joined = " ".join(row)
        match = _PERIOD_HEADER_RE.search(joined)
        if match:
            month = MONTHS.get(match.group(2).lower())
            if month:
                month, _ = fiscal_period_end(month, int(match.group(3)) if match.group(3) else None)
                return MONTH_WORDS.get(match.group(1).lower(), 3), month
        annual = _YEAR_ENDED_RE.search(joined)
        if annual:
            month = MONTHS.get(annual.group(1).lower())
            if month:
                month, _ = fiscal_period_end(month, int(annual.group(2)) if annual.group(2) else None)
                return 12, month
    return None


def _year_columns(rows: list[list[str]]) -> list[int]:
    """Year labels in column order, from the header row that lists them."""
    for row in rows[:8]:
        years = [int(y) for cell in row for y in _YEAR_HEADER_RE.findall(cell)]
        if len(years) >= 2:
            return years
    return []


def _row_labels(rows: list[list[str]]) -> list[str]:
    """The first cell of every row: this table's own list of what it reports."""
    return [clean_label(row[0]) for row in rows if row and clean_label(row[0])]


def sibling_row_labels(
    tables: Iterable[list[list[str]]] | None,
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
) -> list[str]:
    """The row labels of every schedule in a document that reports this product.

    A table with a period heading and year columns is a schedule of figures by
    period, and the one our product has a row in is the filer's own list of
    what it reports beside it. Every other table in a filing - the cover page,
    the table of contents, a maturity schedule - is a list of something else,
    and its rows say nothing about which products this issuer sells.
    """
    aliases = product_aliases(product, generic, extra=extra_aliases)
    found: list[str] = []
    for rows in tables or ():
        if not _period_header(rows) or not _year_columns(rows):
            continue
        labels = _row_labels(rows)
        if any(_matches_product(label, aliases, product, labels) for label in labels):
            found.extend(labels)
    return found


def is_product_revenue_schedule(
    rows: list[list[str]],
    *,
    caption: str = "",
) -> bool:
    """Whether a period schedule's labels/caption name product revenue.

    Derived from the table's own vocabulary (REVENUE_HINT_RE and bare product-
    revenue lines), not from a hand list of Item 8 / MD&A titles.
    """
    if not _period_header(rows) or not _year_columns(rows):
        return False
    blob = " ".join([caption, *(_row_labels(rows))])
    if REVENUE_HINT_RE.search(blob):
        return True
    return any(is_generic_product_revenue_label(label) for label in _row_labels(rows))


def product_revenue_schedules(
    tables: Iterable[list[list[str]]] | None,
    *,
    captions: Iterable[str] | None = None,
) -> list[list[list[str]]]:
    """Period schedules whose labels or caption declare product revenue."""
    caps = list(captions or ())
    out: list[list[list[str]]] = []
    for index, rows in enumerate(tables or ()):
        caption = caps[index] if index < len(caps) else ""
        if is_product_revenue_schedule(rows, caption=caption):
            out.append(rows)
    return out


def schedules_for_quote(
    tables: Iterable[list[list[str]]] | None,
    *,
    quote: str,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
) -> list[list[list[str]]]:
    """Schedules that contain the quote's row or a bare product-revenue line.

    Used for sole-generic peer scans so a brand on a different schedule does
    not block a bare net-product line on the product-sales table.
    """
    del product, generic, extra_aliases  # signature kept stable for callers
    quote_l = (quote or "").lower()
    out: list[list[list[str]]] = []
    for rows in tables or ():
        if not _period_header(rows) or not _year_columns(rows):
            continue
        labels = _row_labels(rows)
        if any(is_generic_product_revenue_label(label) for label in labels):
            out.append(rows)
            continue
        if any(label and len(label) >= 3 and label.lower() in quote_l for label in labels):
            out.append(rows)
    return out


def _matches_product(
    label: str, aliases: list[str], product: str, siblings: list[str] | None = None
) -> bool:
    normalized = label.lower()
    if not any(alias.lower() in normalized for alias in aliases):
        return False
    return names_a_competing_product(label, aliases, siblings) is None
