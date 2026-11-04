"""Quarters no document prints, from year-to-date figures that documents do -
for issuers with no XBRL, where the figures are found by the grounded search.

The arithmetic is `derive.py`'s: a quarter is the stated year to date through
it less the stated year to date before it (Q4 = the year less the nine months).
Here each stated figure comes from a grounded Gemini answer, and is used only
once `verify.py` finds it printed beside the product in the document the
answer cites. Both figures must agree on currency and unit before they are
subtracted.
"""

from __future__ import annotations

from typing import Any, Callable

from app.retrieval.derive import Derived
from app.retrieval.gemini_search import QuotaExceeded, ask_revenue
from app.retrieval.verify import verify_answer
from app.retrieval.xbrl import quarter_end

SCALE = {"units": 1.0, "thousands": 1e3, "millions": 1e6, "billions": 1e9}


def year_to_date_label(year: int, n: int) -> str:
    """How `describe_period` names the year to date through calendar quarter n."""
    return {1: f"Q1 {year}", 2: f"6M {year}", 3: f"9M {year}", 4: f"{year}"}[n]


def _stated(product: str, label: str, issuer: str | None, ask: Callable, verify: Callable) -> dict[str, Any]:
    try:
        answer = ask(product, label, issuer)
    except QuotaExceeded:
        raise
    except Exception as exc:  # a failed search leaves this input missing, not the run broken
        return {"period": label, "value": None, "unit": "", "scaled": None, "currency": "", "source_url": None,
                "source_quote": None, "verification": f"error: {type(exc).__name__}", "near_text": None}
    parsed = answer.parsed or {}
    check = verify(parsed, product)
    unit = str(parsed.get("unit") or "").lower()
    value = parsed.get("value")
    scaled = float(value) * SCALE[unit] if value is not None and unit in SCALE else None
    return {"period": label, "value": value, "unit": unit, "scaled": scaled,
            "currency": str(parsed.get("currency") or "").upper(), "source_url": parsed.get("source_url"),
            "source_quote": parsed.get("source_quote"), "verification": check.status, "near_text": check.near_text}


def derive_from_printed(
    product: str,
    period: str,
    issuer: str | None = None,
    *,
    ask: Callable = ask_revenue,
    verify: Callable = verify_answer,
) -> Derived:
    """Compute calendar quarter `period` from two printed, verified year-to-date
    figures. `value` is in the figures' own currency, in units."""
    end = quarter_end(period)
    if end is None or end.month == 3:
        return Derived("missing_input", product, period, reason="only Q2-Q4 are a difference of years to date")
    n, year = end.month // 3, end.year
    current = _stated(product, year_to_date_label(year, n), issuer, ask, verify)
    previous = _stated(product, year_to_date_label(year, n - 1), issuer, ask, verify)
    inputs = [current, previous]
    method = "full_year_less_nine_months" if n == 4 else "ytd_less_previous_ytd"
    for stated in inputs:
        if stated["scaled"] is None:
            return Derived("missing_input", product, period, inputs=inputs, method=method,
                           reason=f"{stated['period']}: no printed figure found ({stated['verification']})")
        if stated["verification"] not in ("verified", "verified_from_archive"):
            return Derived("unverified_input", product, period, inputs=inputs, method=method,
                           reason=f"{stated['period']}: {stated['verification']} in the cited document")
    if current["currency"] != previous["currency"]:
        return Derived("inputs_differ", product, period, inputs=inputs, method=method,
                       reason=f"currencies differ: {current['currency']} and {previous['currency']}")
    value = current["scaled"] - previous["scaled"]
    if value < 0:
        return Derived("negative", product, period, value=value, unit=current["currency"], method=method,
                       inputs=inputs, reason="a later year to date smaller than the earlier one")
    return Derived("derived", product, period, value=value, unit=current["currency"], method=method, inputs=inputs)

