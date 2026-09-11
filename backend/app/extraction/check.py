"""Stage 4: check normalized datapoints against what the numbers must satisfy.

These are arithmetic and continuity properties a real revenue series cannot
violate, so a violation means the extraction is wrong even when no source was
misread in an obvious way. Each check corresponds to a defect class these
numbers have shown:

* ``quarters_sum_to_period_total`` - Merck's 2024 schedule listed Q2-Q4 plus the
  full year; reading it as Q1-Q4 booked the annual total as a quarter. Summing
  quarters against their own stated total catches that immediately.
* ``scale_continuity`` - United Therapeutics' exhibits switched from thousands
  to millions mid-2016; a run of quarters 1000x off its neighbours is the
  signature of a unit that was assumed rather than read.
* ``value_supported_by_quote`` - a number that does not appear in the text cited
  for it was produced by the reader, not the issuer.

Findings are advisory by design: they identify which datapoints to reject or
re-derive, and the pipeline decides what to do with them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from app.extraction.process import Datapoint

# A quarter that differs from a neighbouring quarter by at least this factor is
# not plausible as real revenue movement; it is a unit-scale error.
_SCALE_JUMP_FACTOR = 100.0
# What follows are two tolerances for the same underlying quantity: how far
# figures may legitimately differ because of the precision their sources were
# rounded to. Where the datapoints say what that precision was, it is summed
# and used directly. These fractions are the fallback for figures that never
# said - a table cell carries no equivalent of XBRL's `decimals` - and they are
# guesses at a quantity that is measurable, so they are wrong in both
# directions at once.
#
# Concretely, on figures each rounded to the nearest million, the true bound is
# two million whatever the product's size. `_ROUNDING_TOLERANCE` allows 2.69 on
# a 538 figure and 0.15 on a 30 one: too loose to catch a real conflict in the
# first, tight enough to invent one in the second. Scaling a rounding error by
# the size of the number is the mistake; rounding does not work that way.
_SUM_TOLERANCE = 0.02
_ROUNDING_TOLERANCE = 0.005
# A floor under both, absorbing representation noise rather than widening any
# claim: a difference of fifty thousand dollars between two readings of a
# revenue figure carries no signal at this scale, and a source declaring itself
# exact (`decimals="INF"`) would otherwise be held to bit-exact equality
# through a unit conversion and an FX rate.
_ROUNDING_ABSOLUTE = 0.05


def _declared_slack(*points: Datapoint) -> float | None:
    """How far these figures may jointly differ, from what their sources said.

    Each point may be off by its own rounding bound, and in a comparison
    between them those bounds add: two figures each within half a million of
    the truth can sit a million apart and still agree.

    None if any of them never declared a precision. A bound summed over the
    subset that did would be smaller than the truth, and a tolerance that
    understates turns rounding into a reported conflict - which is exactly the
    failure this replaces.
    """
    total = 0.0
    for point in points:
        share = point.rounding_uncertainty_usd_millions
        if share is None:
            return None
        total += share
    return total

_MONTHS_BY_PERIOD_TYPE = {"quarterly": 3, "six_month": 6, "nine_month": 9, "annual": 12}


@dataclass(frozen=True)
class Finding:
    """One failed expectation, tied to the datapoints that produced it."""

    code: str
    severity: str
    message: str
    periods: tuple[str, ...] = ()

    def __str__(self) -> str:
        scope = f" [{', '.join(self.periods)}]" if self.periods else ""
        return f"{self.severity.upper()} {self.code}{scope}: {self.message}"


def _quarter_index(period: str) -> int | None:
    match = re.fullmatch(r"(\d{4})Q([1-4])", period or "")
    return int(match.group(2)) if match else None


def _year_of(period: str) -> int | None:
    match = re.match(r"(\d{4})", period or "")
    return int(match.group(1)) if match else None


def _usable(points: list[Datapoint]) -> list[Datapoint]:
    return [p for p in points if p.value_normalized_usd_millions is not None]


def quarters_sum_to_period_total(points: list[Datapoint]) -> list[Finding]:
    """Quarters must add up to the longer period reported alongside them."""
    findings: list[Finding] = []
    quarters: dict[int, dict[int, Datapoint]] = defaultdict(dict)
    totals: dict[tuple[int, str], Datapoint] = {}

    for point in _usable(points):
        year = _year_of(point.period)
        if year is None:
            continue
        if point.period_type == "quarterly":
            index = _quarter_index(point.period)
            if index:
                quarters[year][index] = point
        elif point.period_type in _MONTHS_BY_PERIOD_TYPE:
            totals[(year, point.period_type)] = point

    for (year, period_type), total in sorted(totals.items()):
        needed = _MONTHS_BY_PERIOD_TYPE[period_type] // 3
        available = quarters.get(year, {})
        if not all(index in available for index in range(1, needed + 1)):
            continue
        summed = sum(
            available[index].value_normalized_usd_millions for index in range(1, needed + 1)
        )
        expected = total.value_normalized_usd_millions
        if expected == 0:
            continue
        members = [available[index] for index in range(1, needed + 1)]
        declared = _declared_slack(total, *members)
        allowed = (
            max(declared, _ROUNDING_ABSOLUTE)
            if declared is not None
            else abs(expected) * _SUM_TOLERANCE
        )
        if abs(summed - expected) > allowed:
            findings.append(
                Finding(
                    code="quarters_sum_to_period_total",
                    severity="error",
                    message=(
                        f"{year} quarters sum to {summed:,.3f} but the reported "
                        f"{period_type} total is {expected:,.3f}, a gap of "
                        f"{abs(summed - expected):,.3f} against {allowed:,.3f} "
                        f"allowed; a period is misattributed, duplicated, or "
                        f"missing"
                    ),
                    periods=tuple(
                        available[index].period for index in range(1, needed + 1)
                    )
                    + (total.period,),
                )
            )
    return findings


def scale_continuity(points: list[Datapoint]) -> list[Finding]:
    """Consecutive quarters must not jump by orders of magnitude."""
    findings: list[Finding] = []
    quarterly = sorted(
        (p for p in _usable(points) if p.period_type == "quarterly"),
        key=lambda p: p.period,
    )
    for previous, current in zip(quarterly, quarterly[1:], strict=False):
        before = previous.value_normalized_usd_millions
        after = current.value_normalized_usd_millions
        if not before or not after:
            continue
        ratio = max(abs(before), abs(after)) / min(abs(before), abs(after))
        if ratio >= _SCALE_JUMP_FACTOR:
            findings.append(
                Finding(
                    code="scale_continuity",
                    severity="error",
                    message=(
                        f"{previous.period}={before:,.4f} then {current.period}="
                        f"{after:,.4f} is a {ratio:,.0f}x jump; the unit was "
                        f"read as {previous.source_unit} then {current.source_unit}"
                    ),
                    periods=(previous.period, current.period),
                )
            )
    return findings


def value_supported_by_quote(points: list[Datapoint]) -> list[Finding]:
    """The as-reported number must appear in the text cited for it."""
    findings: list[Finding] = []
    for point in points:
        quote = (point.source_quote or "").replace(",", "")
        value = point.value_as_reported
        forms = {f"{value:g}", f"{value:.1f}", f"{value:.3f}", str(abs(value))}
        if not any(re.search(rf"(?<!\d){re.escape(form)}(?!\d)", quote) for form in forms):
            findings.append(
                Finding(
                    code="value_supported_by_quote",
                    severity="error",
                    message=(
                        f"{value:g} does not appear in its own cited text, so it "
                        "was not read from the source"
                    ),
                    periods=(point.period,),
                )
            )
    return findings


def normalization_succeeded(points: list[Datapoint]) -> list[Finding]:
    """Every datapoint must have reached a comparable USD-millions value."""
    return [
        Finding(
            code="normalization_failed",
            severity="warning",
            message=(
                f"{point.period} stayed unnormalized ({point.normalization_status}); "
                "it cannot be compared to other products"
            ),
            periods=(point.period,),
        )
        for point in points
        if point.value_normalized_usd_millions is None
    ]


def conflicting_values(points: list[Datapoint]) -> list[Finding]:
    """One period must not carry two materially different values."""
    findings: list[Finding] = []
    by_period: dict[tuple[str, str], list[Datapoint]] = defaultdict(list)
    for point in _usable(points):
        by_period[(point.period, point.period_type)].append(point)

    for (period, _), group in sorted(by_period.items()):
        values = sorted(p.value_normalized_usd_millions for p in group)
        if not values:
            continue
        low, high = values[0], values[-1]
        # An issuer restating a figure at coarser precision in a later exhibit
        # (121.718 becoming 121.7) is agreement, not conflict, so the two are
        # only in conflict once they differ by more than rounding could explain.
        #
        # How much rounding could explain is a thing the datapoints may know:
        # a quarter derived from a year and three quarters each tagged to the
        # nearest million is two million wide, and the issuer's own printed
        # figure for that quarter sitting one million away agrees with it. Where
        # they know, that governs; where any of them does not, the fraction
        # above stands in.
        # The two points actually being compared, not two that happen to share
        # the low value - picking by equality summed one bound twice whenever a
        # reading was duplicated.
        lowest = min(group, key=lambda p: p.value_normalized_usd_millions)
        highest = max(group, key=lambda p: p.value_normalized_usd_millions)
        declared = _declared_slack(lowest, highest) if lowest is not highest else None
        allowed = (
            max(declared, _ROUNDING_ABSOLUTE)
            if declared is not None
            else max(abs(high) * _ROUNDING_TOLERANCE, _ROUNDING_ABSOLUTE)
        )
        if high - low > allowed:
            findings.append(
                Finding(
                    code="conflicting_values",
                    severity="error",
                    message=(
                        f"{period} extracted as {sorted(set(values))} from "
                        f"{len({p.fingerprint_signature for p in group})} table "
                        f"layouts; they differ by {high - low:,.3f} against "
                        f"{allowed:,.3f} that rounding could explain"
                    ),
                    periods=(period,),
                )
            )
    return findings


CHECKS = (
    quarters_sum_to_period_total,
    scale_continuity,
    value_supported_by_quote,
    normalization_succeeded,
    conflicting_values,
)


def run_checks(points: list[Datapoint]) -> list[Finding]:
    """Every check, most severe first."""
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(points))
    return sorted(findings, key=lambda f: (f.severity != "error", f.code))
