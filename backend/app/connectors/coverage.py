"""Does this document answer a period we still need, for this product?

Retrieval decides what to fetch from properties of the filing index - the form
string and the item codes - and nothing downstream ever asks what the document
it got actually covers. So the same run fetches documents that carry nothing
and skips documents that carry something, and in neither case does anything
notice.

This is the question, computed from the document. It changes nothing about
what is fetched; it records what was fetched turned out to hold, which is the
measurement any ordering of retrieval would have to be argued from.

**It is a figure test, not a name test.** ``"CALDERON" in text`` is true of a
risk factor, a collaboration note and a table of contents - of a product named
beside no number at all - so it answers a neighbour of the question rather than
the question. ``carries`` is only ever a number read out of a cell, on a row
whose label resolves to the product, under a column whose heading resolves to
the period.

``carries`` is therefore a floor rather than a ceiling, and the two ways it is
one are worth stating.

A figure printed in a sentence - "revenue for Calderon in the first quarter was
$78.7 million" - is reported ``names_only`` where the document prints tables
too. The document's text includes its tables' own cells, run together, so a
figure test over the text would call a table a sentence; refusing to guess is
the safer of the two wrong answers, and the only one that does not claim a
figure it cannot point at.

A document the reader produced no table from at all is the other, and it is
``unreadable`` rather than ``names_only``: an XBRL instance, a JSON payload, a
release written entirely in prose. The figure test never ran on it, so "the
product is named here and no figure of ours is against it" would be a claim
about a document nobody read. Both are floors the reader could be raised off;
neither may be reported as a document that was read and held nothing.

The verdict is written per document and is what a reader of "which quarters
does no filing cover" would replace `_quarters_no_filing_covers` with: that
one answers from filing *dates* - no successful filing of this issuer dated
inside the window - and so calls a quarter uncovered when a filing covering it
was fetched and read, and covered when one was fetched that holds nothing for
this product. This answers the same question from the figures, per document.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.connectors.sources import normalize_registrant
from app.domain.models import ParsedDocument, ParsingStatus, RetrievedSource
from app.extraction.fingerprint import column_periods
from app.parsing.labels import LabelReading, read_label
from app.parsing.periods import (
    detect_period_context,
    period_label,
    period_months,
    period_span,
    quarter_of_month,
)
from app.parsing.tables import NIL_CELLS, cell_figure, clean_label

logger = logging.getLogger(__name__)

# What one period of a document can be for one product.
CARRIES = "carries"
REFUTES = "refutes"
SILENT = "silent"

# What the document is for the whole set of periods asked about.
ANSWERS = "answers"
PARTIAL = "partial"
NAMES_ONLY = "names_only"
ABSENT = "absent"
UNREADABLE = "unreadable"



@dataclass(frozen=True)
class DocumentCoverage:
    """What one document holds for one product over one set of periods.

    ``per_period`` is every period asked about and the year each one falls in,
    so a reader can tell a period the document refuses from one it was never
    asked about. ``figures`` is the number that made each carried period
    carry, which is what makes the verdict checkable rather than a label.
    """

    verdict: str
    per_period: dict[str, str] = field(default_factory=dict)
    figures: dict[str, float] = field(default_factory=dict)
    names_product: bool = False

    @property
    def carried(self) -> list[str]:
        return sorted(q for q, v in self.per_period.items() if v == CARRIES)

    @property
    def refuted(self) -> list[str]:
        return sorted(q for q, v in self.per_period.items() if v == REFUTES)

    def as_metadata(self) -> dict:
        """The verdict as it is stored on a retrieved source."""
        return {
            "verdict": self.verdict,
            "names_product": self.names_product,
            "carries": self.carried,
            "refutes": self.refuted,
            "figures": {q: self.figures[q] for q in self.carried if q in self.figures},
        }


def coverage(
    document: ParsedDocument | None,
    *,
    aliases: Iterable[str],
    periods: Iterable[str],
    products: Iterable[str] = (),
) -> DocumentCoverage:
    """What ``document`` holds for the product ``aliases`` name, over ``periods``.

    ``periods`` are canonical `period_label` keys and come from the window the
    run was asked about; the year each one falls in is asked about too, because
    a document that reports by fiscal year states a period the caller's
    quarters cannot name. ``products`` are the other products the run tracks,
    which `read_label` needs to tell a combined line from this product's own.

    Per period the answer is one of:

    ``carries``  a figure on a row resolving to the product, under a column
                 resolving to that period.
    ``refutes``  the document states the product had nothing in that period -
                 a zero or a dash in the same place a figure would be - or its
                 own reporting period ends before the period asked about, so
                 it cannot state it.
    ``silent``   neither.

    And the document as a whole is ``answers`` when every period the caller
    asked about is carried, ``partial`` when some period is, ``names_only``
    when the product resolves somewhere in it and no period is carried,
    ``absent`` when the product resolves nowhere in it, and ``unreadable``
    when the parse failed or produced no table for the figure test to run on.
    """
    asked = [str(period) for period in periods if period]
    # The years those periods fall in, each as a period in its own right. A
    # caller asks in quarters and a 10-K states its product table by fiscal
    # year, so a set holding only quarters can never match a column that
    # document does state, and the document reads as though it held nothing.
    # Derived from the periods asked, through the producer that spelled them,
    # so both are one namespace; `answers` stays a statement about the periods
    # the caller asked for, because a year it did not ask for is not one.
    wanted = asked + [key for key in _annual_keys(asked) if key not in asked]
    own = [alias for alias in aliases if alias]
    if (
        document is None
        or document.parsing_status != ParsingStatus.SUCCESS
        or not document.table_grids
    ):
        # No table, no figure test. Saying `names_only` here would report a
        # document nobody read as one that was read and held nothing.
        return DocumentCoverage(UNREADABLE, dict.fromkeys(wanted, SILENT))

    per_period = dict.fromkeys(wanted, SILENT)
    figures: dict[str, float] = {}
    names_product = False

    for grid in document.table_grids:
        keys = _period_by_column(grid)
        labels = _row_labels(grid)
        section: str | None = None
        for row in grid:
            label = _row_label(row)
            if not label:
                continue
            if not _states_a_figure(row):
                # A label with no figures beside it heads the rows below rather
                # than stating anything itself. A filer prints a product that
                # way - "CALDERON" alone, then "US", "Intl", "WW" beneath it -
                # so the rows carrying the numbers never name the product.
                section = label
                continue
            reading = _read(label, own, products, labels)
            if not reading.names_product and section:
                reading = _read(f"{section} {label}", own, products, labels)
            if not reading.names_product:
                continue
            names_product = True
            # A line that names this product and another is that pair's
            # figure, not this product's, and nothing here can split it.
            if reading.combined_with:
                continue
            for column, key in keys.items():
                if key not in per_period or column >= len(row):
                    continue
                value = cell_figure(row[column])
                if value is None:
                    continue
                if value:
                    per_period[key] = CARRIES
                    figures.setdefault(key, value)
                elif per_period[key] == SILENT:
                    per_period[key] = REFUTES

    if not names_product:
        names_product = _names_the_product(document, own)

    _refute_what_the_document_predates(document, per_period)

    carried = [q for q, v in per_period.items() if v == CARRIES]
    if carried:
        verdict = ANSWERS if all(per_period[q] == CARRIES for q in asked) else PARTIAL
    elif names_product:
        verdict = NAMES_ONLY
    else:
        verdict = ABSENT
    return DocumentCoverage(verdict, per_period, figures, names_product)


def record_coverage(
    source: RetrievedSource,
    document: ParsedDocument | None,
    *,
    aliases: Iterable[str],
    periods: Iterable[str],
    products: Iterable[str] = (),
) -> DocumentCoverage:
    """Compute the verdict for one retrieved document, store it and log it.

    The verdict goes on the source's own metadata rather than anywhere new,
    because the question it answers is about that source: "this job fetched N
    documents" becomes "this many of them carried nothing", per job, out of
    rows the pipeline already writes.
    """
    result = coverage(document, aliases=aliases, periods=periods, products=products)
    source.metadata = {**(source.metadata or {}), "coverage": result.as_metadata()}
    logger.info(
        "document_coverage source=%s verdict=%s names_product=%s carries=%s refutes=%s url=%s",
        source.source_id, result.verdict, result.names_product,
        ",".join(result.carried) or "-", ",".join(result.refuted) or "-", source.url,
    )
    return result


def _annual_keys(periods: Iterable[str]) -> list[str]:
    """The canonical key of the full year each of ``periods`` ends in.

    Read through the same producer that writes a period's key rather than off
    the front of the string, so a key and the year it belongs to keep saying
    the same thing when either is respelled.
    """
    years = {end[0] for key in periods if (end := _period_end(key))}
    return sorted(period_label(year, 12, 4) for year in years)


def _read(
    label: str, aliases: list[str], products: Iterable[str], labels: list[str]
) -> LabelReading:
    """`read_label` on one row of a table the document prints.

    The other rows are the siblings: a row is not its own sibling, or every
    label would read as a combined line over itself. `extraction/extract.py`
    reads a row the same way and for the same reason.
    """
    own = clean_label(label)
    siblings = [other for other in labels if other != own and other not in own]
    return read_label(label, aliases, products=products, siblings=siblings)


def _states_a_figure(row: list[str | None]) -> bool:
    """Whether this row prints a number of its own anywhere across it."""
    return any(cell_figure(cell) is not None for cell in row)


def _period_by_column(grid: list[list[str | None]]) -> dict[int, str]:
    """The canonical period key each column of this table states, if any."""
    return {
        column: period_label(year, months, quarter_of_month(month))
        for column, (months, month, year) in column_periods(grid).items()
    }


def _row_label(row: list[str | None]) -> str:
    """The label of a row: its first cell that is neither a figure nor a
    currency or percent mark standing on its own."""
    for cell in row:
        text = (cell or "").strip()
        if not text or text in {"$", "%"} or text in NIL_CELLS:
            continue
        if cell_figure(text) is not None:
            return ""
        return text
    return ""


def _row_labels(grid: list[list[str | None]]) -> list[str]:
    """Every row label this table prints - the filer's own list of what it
    reports, which is what tells a sibling product from this one.

    Cleaned of the marks a filer hangs off a label, because that is the form
    `read_label` compares siblings in.
    """
    return [label for row in grid if (label := clean_label(_row_label(row)))]


def _identifies_someone(alias: str) -> bool:
    """Whether this alias names anyone at all.

    An alias arrives as a name of the product or of a company that sells it,
    and a sponsor's name split at its comma leaves the form of registration
    behind as an entry of its own - ``Inc.``, ``S.A.`` Those are not names:
    every registrant of that form carries one, so a document about anybody
    prints them. `normalize_registrant` is what already decides which words of
    a company's name identify it, and it is asked here rather than a second
    list being written beside it; the dots an abbreviation carries are closed
    up first, so ``S.A.`` reduces the way ``SA`` does.
    """
    return bool(normalize_registrant(alias.replace(".", "")))


def _names_the_product(document: ParsedDocument, aliases: list[str]) -> bool:
    """Whether the product is named anywhere in the document at all.

    This is the name test, and it is used for the one thing a name test can
    answer: telling a document that mentions the product but puts no figure we
    can reach against it - a narrative note, a row-group heading whose rows
    print no number - from one that is about somebody else entirely. It never
    contributes to ``carries``.
    """
    text = document.full_text.casefold()
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(alias.casefold())}(?![a-z0-9])", text)
        for alias in aliases
        if alias and _identifies_someone(alias)
    )


def _refute_what_the_document_predates(
    document: ParsedDocument, per_period: dict[str, str]
) -> None:
    """Mark the periods this document could not state whatever it holds.

    A document reports a period that had ended when it was written, so a
    period ending after its own reporting period is one it cannot answer -
    not a period it is silent about. Only periods still silent are touched: a
    figure the document actually prints outranks this reasoning about it.
    """
    context = detect_period_context(document.full_text)
    if context is None:
        return
    for key, verdict in per_period.items():
        end = _period_end(key)
        if verdict == SILENT and end is not None and end > (context.year, context.month):
            per_period[key] = REFUTES


def _period_end(key: str) -> tuple[int, int] | None:
    """(year, last month) of a canonical period key, or None if it is not one.

    The key states its own span, so the namespace answers both halves: the
    span it names and the days that span covers.
    """
    months = period_months(key)
    span = period_span(key, months) if months else None
    return (span[1].year, span[1].month) if span else None
