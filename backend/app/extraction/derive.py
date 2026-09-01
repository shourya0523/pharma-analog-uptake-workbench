"""Stage 3b: complete a series from the arithmetic the issuer already published.

Reading every number a filing prints still leaves gaps, because issuers do not
print every quarter. Two patterns account for most of them:

* A fourth quarter is often never stated on its own. The issuer reports three
  quarters and then a full year, and Q4 is the difference. United Therapeutics
  disclosed Remodulin this way for seven straight years.
* Before a product line splits into formulations, the family total *is* the one
  formulation on sale. Tyvaso was nebulized-only from 2009 until the DPI
  inhaler launched in 2022Q2, so every family figure in those 50 quarters is
  also the nebulized figure.

Both are exact arithmetic over values already extracted, not estimates, so they
carry the same confidence as a directly reported number - but they are marked
as derived, with the inputs that produced them, because a reader deserves to
know which figures the issuer printed and which the pipeline computed.

A derivation is only applied when it is uniquely determined: exactly one
missing quarter against a stated total, or a period provably before a split.
Anything under-determined is left as a gap, since inventing a plausible number
is worse than reporting an honest absence.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace

from app.extraction.process import Datapoint

_QUARTER_RE = re.compile(r"(\d{4})Q([1-4])")

# Quarters constituting each longer reporting period.
_QUARTERS_IN = {"annual": (1, 2, 3, 4), "nine_month": (1, 2, 3), "six_month": (1, 2)}

# Derived quarters inherit rounding from their inputs, so a residual this small
# is arithmetic noise rather than a real amount.
_NEGLIGIBLE = 0.05


def _split(period: str) -> tuple[int, int] | None:
    match = _QUARTER_RE.fullmatch(period or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _year_of(period: str) -> int | None:
    match = re.match(r"(\d{4})", period or "")
    return int(match.group(1)) if match else None


def complete_quarters_from_totals(
    points: list[Datapoint], *, commercial_start: str | None = None
) -> list[Datapoint]:
    """Derive the one quarter an issuer left implicit against a stated total.

    Applied only when every other quarter of that total is present, so the
    result is the single value the issuer's own arithmetic requires.

    ``commercial_start`` names the quarter a product first sold. In its launch
    year the annual total covers only the quarters from that point on, because
    the earlier ones predate the product - they are structurally absent, not
    missing data. Without this the launch year always looks under-determined
    (two quarters unaccounted for rather than one) and never derives, which is
    why Remodulin's 2002Q4 stayed a gap even though its full-year total was
    cited. Pass it only when the start is actually known; the default keeps the
    stricter all-four-quarters rule.
    """
    start = _split(commercial_start or "")
    usable = [p for p in points if p.value_normalized_usd_millions is not None]
    quarters: dict[int, dict[int, Datapoint]] = defaultdict(dict)
    totals: dict[tuple[int, str], Datapoint] = {}

    for point in usable:
        year = _year_of(point.period)
        if year is None:
            continue
        if point.period_type == "quarterly":
            parsed = _split(point.period)
            if parsed:
                quarters[year][parsed[1]] = point
        elif point.period_type in _QUARTERS_IN:
            totals[(year, point.period_type)] = point

    derived: list[Datapoint] = []
    for (year, period_type), total in sorted(totals.items()):
        members = _QUARTERS_IN[period_type]
        if start is not None:
            start_year, start_quarter = start
            if year < start_year:
                # A total for a year the product did not sell in says nothing
                # about any quarter; deriving from it would invent a figure.
                continue
            if year == start_year:
                members = tuple(q for q in members if q >= start_quarter)
                if not members:
                    continue
        have = quarters.get(year, {})
        missing = [q for q in members if q not in have]
        if len(missing) != 1:
            continue
        target = missing[0]
        residual = total.value_normalized_usd_millions - sum(
            have[q].value_normalized_usd_millions for q in members if q != target
        )
        if residual < -_NEGLIGIBLE:
            # A negative quarter means the inputs disagree; report nothing
            # rather than a figure that cannot be real.
            continue
        inputs = ", ".join(have[q].period for q in members if q != target)
        point = replace(
            total,
            period=f"{year}Q{target}",
            period_type="quarterly",
            value_normalized_usd_millions=round(max(residual, 0.0), 6),
            value_as_reported=round(max(residual, 0.0), 6),
            source_quote=(
                f"{total.period} {period_type} total "
                f"{total.value_normalized_usd_millions:g} less reported {inputs} "
                f"yields {year}Q{target} {max(residual, 0.0):g}"
            ),
            normalization_status="derived_from_period_total",
        )
        derived.append(point)
        quarters[year][target] = point
    return derived


def propagate_sole_formulation(
    family: list[Datapoint],
    *,
    formulation_periods: set[str],
    formulation_label: str,
) -> list[Datapoint]:
    """Attribute family totals to the one formulation that existed at the time.

    Before a second formulation launches, the family line and the formulation
    line are the same product, so the family's reported figure is the
    formulation's figure. Periods on or after the split are excluded: once two
    formulations share the line, the split is not recoverable from the total.
    """
    if not formulation_periods:
        return []
    split_at = min(formulation_periods)
    return [
        replace(
            point,
            product_label=formulation_label,
            source_quote=(
                f"{point.source_quote} (family total attributed to "
                f"{formulation_label}: sole formulation on sale before {split_at})"
            ),
            normalization_status="derived_sole_formulation",
        )
        for point in family
        if point.period < split_at and point.value_normalized_usd_millions is not None
    ]
