"""Bridge the fingerprinted extractor into the pipeline's candidate contract.

The orchestrator consumes revenue candidates as plain dicts. This module runs
the four stages - fingerprint, read, normalize, check - and emits candidates in
that shape, so the deterministic path can replace the previous table reader
without changing anything downstream of it.

Two differences matter versus the reader it replaces:

* Unit and currency come from the table's own declaration instead of being
  assumed to be USD millions, so a filing stated in thousands or in CHF no
  longer yields a value that is off by 1000x or by an exchange rate.
* Year-to-date columns are labelled as the periods they are, so a six- or
  nine-month figure sitting beside the quarter can never be emitted as a
  quarterly datapoint.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.extraction.check import Finding, run_checks
from app.extraction.extract import read_tables
from app.extraction.prose import read_prose
from app.extraction.process import Datapoint, normalize_all

# Read straight off a declared table, so it carries the confidence the previous
# table reader used for the same provenance.
TABLE_CONFIDENCE = 0.75


def _scope_for(label: str, product: str) -> str:
    normalized = label.lower().strip()
    base = product.lower()
    if normalized in {base, f"total {base}"}:
        return "Product family"
    return "Formulation-specific"


def _as_candidate(point: Datapoint, product: str) -> dict[str, Any]:
    scope = _scope_for(point.product_label, product)
    # A sentence and a table row arrive here as the same Datapoint, and only
    # the fingerprint says which: ``read_prose`` signs its values "prose". They
    # were all labelled as table reads, which made the extraction method in the
    # export a statement about this function rather than about the document.
    from_prose = point.fingerprint_signature == "prose"
    return {
        "period": point.period,
        "period_type": point.period_type,
        "value_reported": point.value_as_reported,
        "value_normalized_usd_millions": point.value_normalized_usd_millions,
        "currency": point.source_currency,
        "unit": point.source_unit,
        "revenue_scope": scope,
        "formulation": None if scope == "Product family" else point.product_label,
        "source_quote": point.source_quote,
        "product_mentioned_in_quote": True,
        "is_company_total": False,
        "confidence": TABLE_CONFIDENCE,
        "extraction_method": "prose_sentence" if from_prose else "table_fingerprint",
        "fingerprint_signature": point.fingerprint_signature,
        "_from_table": True,
    }


def extract_revenue_candidates(
    tables: Iterable[list[list[str]]],
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
    context: str = "",
    quarterly_only: bool = True,
    grids: Iterable[list[list[str | None]]] | None = None,
    captions: Iterable[str] | None = None,
    prose: str = "",
) -> tuple[list[dict[str, Any]], list[Finding], list[str]]:
    """Deterministic revenue candidates plus what the checks found.

    Returns (candidates, findings, skipped reasons). Skipped reasons name the
    tables that were passed over and why, so a source that produced nothing is
    distinguishable from a source that was never read.

    ``prose`` is the document's running text. Issuers disclosed product sales
    in sentences long before the product-sales exhibit existed, and a reader
    that only reads tables has nothing at all for those years - United
    Therapeutics stated Remodulin narratively from 2002 to 2009. A sentence
    carries its unit beside the amount, so it declares more than a table header
    does; what it must also do is name one period and one amount, or it is
    refused.
    """
    readouts = read_tables(
        tables,
        product=product,
        generic=generic,
        extra_aliases=extra_aliases,
        context=context,
        grids=grids,
        captions=captions,
    )
    values = [value for readout in readouts for value in readout.values]
    skipped = [readout.skipped_reason for readout in readouts if readout.skipped_reason]

    # Sentences are read after tables and add only the periods the tables did
    # not state, so a figure printed in a schedule is never displaced by the
    # same figure described around it.
    if prose:
        # A period, not a period-and-label. A sentence names the product one way
        # and the schedule another - "Tyvaso" against "Tyvaso (R)" - so keying
        # the fallback on both lets the same figure through twice, described
        # differently and sometimes scoped differently, and two candidates that
        # disagree are not an answer. Measured: pooling them cost eight rows and
        # turned nine more into contradictions.
        stated = {value.period for value in values}
        values += [
            value
            for value in read_prose(
                prose, product=product, generic=generic, extra_aliases=extra_aliases
            )
            if value.period not in stated
        ]

    points = normalize_all(values)
    findings = run_checks(points)

    # Datapoints failing a check are held back rather than published; the
    # finding says which period and why, so the run can be diagnosed.
    rejected = {period for finding in findings if finding.severity == "error" for period in finding.periods}
    kept = [
        point
        for point in points
        if point.period not in rejected
        and point.value_normalized_usd_millions is not None
        and (not quarterly_only or point.period_type == "quarterly")
    ]
    return [_as_candidate(point, product) for point in kept], findings, skipped
