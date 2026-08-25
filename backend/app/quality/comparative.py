"""Recover the prior-year column that earnings tables print beside each figure.

Earnings tables state two periods per product row ("Total Tyvaso 398.2 318.9 79.3
25 %" is the current quarter, the prior-year quarter, then the change columns).
Models reliably return only the current column, so the comparative figure is
recovered here instead — but only when the row's own arithmetic proves the column
layout, so a number is never guessed into a datapoint.
"""

from __future__ import annotations

import re
from typing import Any

from app.parsing.periods import PeriodContext, normalize_period

_NUMBER_RE = re.compile(r"-?\(?\d[\d,]*\.?\d*\)?")
# Earnings tables round to one decimal, so allow a little slack in the checks
ABS_TOLERANCE = 0.15
PCT_TOLERANCE = 1.5


def parse_numbers(text: str) -> list[float]:
    """Numeric cells of a table row, with parentheses read as negatives.

    Leading footnote markers ("Tyvaso DPI (1) 258.3 ...") are dropped so that
    column positions line up; monetary cells in these tables always carry a
    decimal, footnote markers never do.
    """
    tokens: list[tuple[float, bool]] = []
    for token in _NUMBER_RE.findall(text or ""):
        negative = token.startswith("(") and token.endswith(")")
        cleaned = token.strip("()").replace(",", "")
        if not cleaned or cleaned in {".", "-"}:
            continue
        try:
            value = float(cleaned)
        except ValueError:
            continue
        tokens.append((-value if negative else value, "." in cleaned))

    start = 0
    for index, (value, has_decimal) in enumerate(tokens):
        if has_decimal:
            start = index
            break
        if abs(value) < 10 and float(value).is_integer():
            continue  # footnote marker
        start = index
        break
    return [value for value, _ in tokens[start:]]


def _is_change_row(numbers: list[float]) -> bool:
    """True for a "current, prior, $ change[, % change]" layout."""
    if len(numbers) < 3:
        return False
    current, prior, change = numbers[0], numbers[1], numbers[2]
    if abs((current - prior) - change) > ABS_TOLERANCE:
        return False
    if len(numbers) >= 4 and prior:
        expected_pct = 100 * (current - prior) / prior
        if abs(expected_pct - numbers[3]) > PCT_TOLERANCE:
            return False
    return True


def _parallel_halves(numbers: list[float]) -> tuple[list[float], list[float]] | None:
    """Split a row of two structurally identical period blocks, e.g. US/intl/total twice.

    Each block must total consistently, which also keeps a four-number change row
    (indistinguishable from a 2+2 split) out of this path.
    """
    if len(numbers) < 6 or len(numbers) % 2:
        return None
    half = len(numbers) // 2
    first, second = numbers[:half], numbers[half:]
    if abs(sum(first[:-1]) - first[-1]) > ABS_TOLERANCE:
        return None
    if abs(sum(second[:-1]) - second[-1]) > ABS_TOLERANCE:
        return None
    return first, second


def derive_comparative_value(quote: str, current_value: float) -> float | None:
    """The prior-period figure sitting beside ``current_value`` in ``quote``."""
    numbers = parse_numbers(quote)
    if not numbers or current_value is None:
        return None

    # The change layout is the more specific pattern, so test it first
    if _is_change_row(numbers) and abs(numbers[0] - current_value) <= ABS_TOLERANCE:
        return numbers[1]

    halves = _parallel_halves(numbers)
    if halves:
        first, second = halves
        for index, value in enumerate(first):
            if abs(value - current_value) <= ABS_TOLERANCE:
                return second[index]
    return None


def comparative_period(period: str | None, context: PeriodContext | None = None) -> str | None:
    """Same period one year earlier, in canonical form."""
    label = normalize_period(period, context=context)
    if not label:
        return None
    match = re.fullmatch(r"(\d{4})(Q[1-4]|H1|M9)?", label)
    if not match:
        return None
    year = int(match.group(1)) - 1
    return f"{year}{match.group(2) or ''}"


def derive_comparative_candidates(
    candidates: list[dict[str, Any]],
    *,
    context: PeriodContext | None = None,
) -> list[dict[str, Any]]:
    """Build prior-year candidates for rows whose arithmetic confirms the layout.

    Derived rows keep the same verbatim quote (which contains the value) and are
    flagged so a reviewer confirms them; they are never treated as model output.
    """
    derived: list[dict[str, Any]] = []
    seen = {
        (str(c.get("period")), round(float(c["value_reported"]), 3))
        for c in candidates
        if c.get("value_reported") is not None
    }
    for cand in candidates:
        value = cand.get("value_reported")
        quote = cand.get("source_quote") or ""
        if value is None:
            continue
        prior_period = comparative_period(cand.get("period"), context)
        if not prior_period:
            continue
        prior_value = derive_comparative_value(quote, float(value))
        if prior_value is None:
            continue
        key = (prior_period, round(prior_value, 3))
        if key in seen:
            continue
        seen.add(key)
        derived.append(
            {
                **cand,
                "period": prior_period,
                "value_reported": prior_value,
                "value_normalized_usd_millions": None,
                "confidence": min(float(cand.get("confidence") or 0.5), 0.6),
                "_derived_comparative": True,
            }
        )
    return derived
