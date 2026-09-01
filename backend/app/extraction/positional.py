"""Read a table that PDF extraction flattened into a single run of text.

Pulling text out of a PDF loses the grid: a product block that looked like

    UPTRAVI      Q2'17  Q1'17  Q4'16 ...
      US           102     91     77
      Intl           8      9      8
      WW           110    100     85

arrives as one line - "UPTRAVI US 102 91 77 ... Intl 8 9 8 ... WW 110 100 85".
The numbers are still in order and still grouped by their row label, so the
structure is recoverable by splitting on the labels rather than on whitespace.

This matters more than it sounds: issuer product-sales exhibits are mostly PDFs,
so a pipeline that cannot read flattened text is blind to the single richest
source of per-product revenue. It is also where a reader can quietly attribute
one geography's numbers to another, so scope labels are returned explicitly and
never merged.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.parsing.evidence import product_aliases

# Geography and scope labels that split a product block into rows. Ordered so
# the longer spellings match before their abbreviations.
_SCOPE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Worldwide", r"worldwide|world\s*wide|\bWW\b|\bW\.W\.\b"),
    ("International", r"international|\bIntl\.?\b|\bInt'l\b|outside\s+the\s+u\.?s\.?"),
    ("United States", r"united\s+states|\bU\.?S\.?A?\b|domestic"),
)

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# A dash standing in for a period with nothing to report holds its column, the
# same way it does in a delimited table.
_TOKEN_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)|([-–—*])")


@dataclass(frozen=True)
class PositionalRow:
    """One scope's run of values inside a product block."""

    scope: str
    values: tuple[float | None, ...]
    quote: str


def _tokenize_run(text: str) -> tuple[float | None, ...]:
    """Numbers in order, with standalone dashes preserved as empty columns."""
    tokens: list[float | None] = []
    for match in _TOKEN_RE.finditer(text):
        number, dash = match.group(1), match.group(2)
        if number is not None:
            tokens.append(float(number.replace(",", "")))
        elif dash is not None:
            tokens.append(None)
    return tuple(tokens)


def _product_block(text: str, aliases: Iterable[str]) -> tuple[str, int] | None:
    """The slice of text belonging to this product, and where it started.

    A block runs from the product's name to the next all-caps product name, so
    one product's numbers can never spill into another's.
    """
    lowered = text.lower()
    for alias in sorted(aliases, key=len, reverse=True):
        start = lowered.find(alias.lower())
        if start == -1:
            continue
        after = start + len(alias)
        # The next product heading ends this block. Headings are set in caps in
        # these exhibits, which is what distinguishes them from row labels.
        next_heading = re.search(r"\b[A-Z][A-Z'’\-]{3,}\b", text[after:])
        end = after + next_heading.start() if next_heading else len(text)
        return text[start:end], start
    return None


def read_positional_block(
    text: str,
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
) -> list[PositionalRow]:
    """Scope rows for one product, read out of flattened PDF text.

    Returns one entry per scope label found, each holding that scope's values in
    column order. Nothing is returned when the product's block carries no scope
    labels, since a bare run of numbers gives no way to know whose they are.
    """
    aliases = product_aliases(product, generic, extra=extra_aliases)
    block = _product_block(text or "", aliases)
    if block is None:
        return []
    body, _ = block

    # Locate each scope label, then take the numbers up to the next label.
    hits: list[tuple[int, int, str]] = []
    for scope, pattern in _SCOPE_PATTERNS:
        for match in re.finditer(pattern, body, re.IGNORECASE):
            hits.append((match.start(), match.end(), scope))
    if not hits:
        return []
    hits.sort()

    rows: list[PositionalRow] = []
    for index, (_, end, scope) in enumerate(hits):
        stop = hits[index + 1][0] if index + 1 < len(hits) else len(body)
        run = body[end:stop]
        values = _tokenize_run(run)
        if values:
            rows.append(
                PositionalRow(scope=scope, values=values, quote=" ".join(body.split()))
            )
    return rows
