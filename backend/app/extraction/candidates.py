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
        "extraction_method": "table_fingerprint",
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
) -> tuple[list[dict[str, Any]], list[Finding], list[str]]:
    """Deterministic revenue candidates plus what the checks found.

    Returns (candidates, findings, skipped reasons). Skipped reasons name the
    tables that were passed over and why, so a source that produced nothing is
    distinguishable from a source that was never read.
    """
    readouts = read_tables(
        tables,
        product=product,
        generic=generic,
        extra_aliases=extra_aliases,
        context=context,
    )
    values = [value for readout in readouts for value in readout.values]
    skipped = [readout.skipped_reason for readout in readouts if readout.skipped_reason]

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
