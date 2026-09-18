"""Stage 3b: complete a series from the arithmetic the issuer already published.

Reading every number a filing prints still leaves gaps, because issuers do not
print every quarter. Two patterns account for most of them:

* A fourth quarter is often never stated on its own. The issuer reports three
  quarters and then a full year, and Q4 is the difference. An issuer can report
  a product this way for years on end.
* Before a product line splits into formulations, the family total *is* the one
  formulation on sale: a product sold in a single form until a second one
  launches, so every family figure before that launch is also the first form's
  figure.

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
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from app.extraction.process import Datapoint
from app.parsing.labels import FLAG_NOT_UNDERSTOOD

_QUARTER_RE = re.compile(r"(\d{4})Q([1-4])")

# Quarters constituting each longer reporting period.
_QUARTERS_IN = {"annual": (1, 2, 3, 4), "nine_month": (1, 2, 3), "six_month": (1, 2)}

# A derived quarter is exact arithmetic over figures the issuer published, so
# it is scored just below a figure read straight off the page rather than as a
# guess - and it says which inputs produced it.
DERIVED_CONFIDENCE = 0.7

# Derived quarters inherit rounding from their inputs, so a residual this small
# is arithmetic noise rather than a real amount.
_NEGLIGIBLE = 0.05

# The spans a filer states beside its quarters, by the quarter each ends in,
# and the span each is the continuation of. A quarter is the difference of
# the two spans that meet at it: the year less the nine months is the fourth
# quarter, the nine months less the six is the third. Q1 is the six months
# less Q2, which the total-minus-quarters rule below already derives.
_SPAN_ENDS = {"six_month": 2, "nine_month": 3, "annual": 4}
_SPAN_WITHIN = {"annual": "nine_month", "nine_month": "six_month"}

# A derived figure is published with the bound its inputs' rounding gives it.
# Where that bound exceeds a tenth of the figure, the figure is not known to
# its first digit, and it is held for a person rather than published.
BOUND_FRACTION_HELD = 0.1
HELD_FOR_BOUND = "derived_bound_exceeds_tenth"


def _combined_uncertainty(points: Iterable[Datapoint]) -> float | None:
    """How far a difference of these figures may sit from the truth.

    Each input is only as good as the precision its source rounded to, and a
    subtraction inherits every one of them: a fourth quarter derived from a
    stated year and three stated quarters, each rounded to the nearest million,
    can be up to two million out. That is not error in the arithmetic - the
    arithmetic is exact on what was published - it is error the publisher
    already baked into the figures.

    Unknown if any input's precision is unknown. A bound computed from the
    subset that happened to declare one would be smaller than the truth, and a
    bound that understates is worse than no bound.
    """
    total = 0.0
    for point in points:
        share = point.rounding_uncertainty_usd_millions
        if share is None:
            return None
        total += share
    return total


@dataclass(frozen=True)
class DerivedFrom:
    """One derived quarter beside the figures it was computed from.

    The roles say what each input was in the arithmetic, so a reader of the
    lineage can reconstruct the subtraction without parsing the quote back.
    Kept out of ``Datapoint`` because it is a fact about a derivation rather
    than about an observation, and a derivation's inputs can themselves be
    derived.
    """

    output: Datapoint
    inputs: tuple[tuple[str, Datapoint], ...]


def _split(period: str) -> tuple[int, int] | None:
    match = _QUARTER_RE.fullmatch(period or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _year_of(period: str) -> int | None:
    match = re.match(r"(\d{4})", period or "")
    return int(match.group(1)) if match else None


# What a derived quarter inherits from the figure it was principally computed
# from, rather than states for itself. Every one of these is a column saying
# what the figure is a figure for; the period, the value, the quote and the
# precision are the derivation's own.
#
# `tests/test_a_derivation_says_what_it_subtracted.py` holds this against the
# reader that produces those inputs, so a column added there and not here is a
# column the derivation drops.
CARRIED_IDENTITY_KEYS = (
    "revenue_scope",
    "geography",
    "formulation",
    "route_of_administration",
    "reported_as",
    "combined_with",
    "source_id",
    "source_url",
    "product_label",
)


def complete_quarters_from_totals(
    points: list[Datapoint], *, commercial_start: str | None = None,
    product: str | None = None,
) -> list[DerivedFrom]:
    """Derive the one quarter an issuer left implicit against a stated total.

    Applied only when every other quarter of that total is present, so the
    result is the single value the issuer's own arithmetic requires.

    ``commercial_start`` names the quarter a product first sold. In its launch
    year the annual total covers only the quarters from that point on, because
    the earlier ones predate the product - they are structurally absent, not
    missing data. Without this the launch year always looks under-determined
    (two quarters unaccounted for rather than one) and never derives, which is
    why a launch year's Q4 stays a gap even though its full-year total is
    cited. Pass it only when the start is actually known; the default keeps the
    stricter all-four-quarters rule.

    Each derived quarter comes back beside the figures it subtracted. The
    caller is the only place that knows which document each of those figures
    was read from, so the arithmetic reports what it used and the caller says
    where it came from.
    """
    start = _split(commercial_start or "")
    # A figure whose label the reader could not account for is a number with
    # no claim attached about whose number it is. Subtracting it publishes an
    # amount nobody said was this product's, as this product's, with the
    # arithmetic standing in for the evidence - so it is not an input here,
    # for the same reason `check.py` does not let it contest a cell.
    usable = [
        p
        for p in points
        if p.value_normalized_usd_millions is not None
        and FLAG_NOT_UNDERSTOOD not in (p.flags or ())
    ]
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

    derived: list[DerivedFrom] = []
    # A quarter as the difference of two stated spans, before the rule that
    # needs every other quarter: a filer that states only the six-, nine- and
    # twelve-month figures still determines the third and fourth quarters.
    for (year, outer_type), outer in sorted(totals.items()):
        inner_type = _SPAN_WITHIN.get(outer_type)
        inner = totals.get((year, inner_type)) if inner_type else None
        target = _SPAN_ENDS.get(outer_type)
        if inner is None or target is None or target in quarters.get(year, {}):
            continue
        residual = outer.value_normalized_usd_millions - inner.value_normalized_usd_millions
        if residual < -_NEGLIGIBLE:
            continue
        uncertainty = _combined_uncertainty([outer, inner])
        point = _derived_point(
            outer, year, target, residual, uncertainty,
            f"{outer.period} {outer_type} total {outer.value_normalized_usd_millions:g} "
            f"less {inner.period} {inner_type} total {inner.value_normalized_usd_millions:g}",
            product=product,
        )
        derived.append(DerivedFrom(point, (("outer_span", outer), ("inner_span", inner))))
        quarters[year][target] = point

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
        uncertainty = _combined_uncertainty(
            [total, *(have[q] for q in members if q != target)]
        )
        point = _derived_point(
            total, year, target, residual, uncertainty,
            f"{total.period} {period_type} total {total.value_normalized_usd_millions:g} "
            f"less reported {inputs}",
            product=product,
        )
        derived.append(
            DerivedFrom(
                point,
                (("period_total", total),)
                + tuple(("quarter", have[q]) for q in members if q != target),
            )
        )
        quarters[year][target] = point
    return derived


def _named(source: Datapoint, product: str | None) -> str:
    """What to call the product in a derived quote."""
    return source.product_label or (product or "").strip() or "the product"


def _derived_point(
    source: Datapoint, year: int, target: int, residual: float,
    uncertainty: float | None, arithmetic: str, product: str | None = None,
) -> Datapoint:
    """One derived quarter, saying in its quote what it is and what it is worth.

    The quote names the product, because a quote that does not is vetoed as
    not being about it - which held every derived quarter, correct or not. The
    name is the product the series was derived for, not the label the source
    row happened to carry: a figure the filer tagged carries no row label at
    all, so every quarter derived from a tagged annual total opened its quote
    with a bare colon and was vetoed on the spot.
    The bound is said in the quote as well as carried in the field, because
    the quote is what a reader sees beside the number and the whole point is
    that a derived quarter is not as precise as a tagged one.
    """
    value = round(max(residual, 0.0), 6)
    bound = f", +/- {uncertainty:g} from input rounding" if uncertainty else ""
    return replace(
        source,
        period=f"{year}Q{target}",
        period_type="quarterly",
        value_normalized_usd_millions=value,
        value_as_reported=value,
        source_quote=f"{_named(source, product)}: {arithmetic} yields {year}Q{target} {value:g}{bound}",
        normalization_status="derived_from_period_total",
        rounding_uncertainty_usd_millions=uncertainty,
    )


def held_for_bound(point: Datapoint) -> bool:
    """Whether a derived figure's bound leaves it unknown to its first digit."""
    bound = point.rounding_uncertainty_usd_millions
    value = point.value_normalized_usd_millions
    return bool(bound and value and bound > BOUND_FRACTION_HELD * abs(value))


def propagate_sole_formulation(
    family: list[Datapoint],
    *,
    formulation_periods: set[str],
    formulation_label: str,
) -> list[DerivedFrom]:
    """Attribute family totals to the one formulation that existed at the time.

    Before a second formulation launches, the family line and the formulation
    line are the same product, so the family's reported figure is the
    formulation's figure. Periods on or after the split are excluded: once two
    formulations share the line, the split is not recoverable from the total.

    Each reattributed figure comes back beside its one input, the family
    figure it was read from, in the same shape
    ``complete_quarters_from_totals`` answers in.
    """
    if not formulation_periods:
        return []
    split_at = min(formulation_periods)
    attributed: list[DerivedFrom] = []
    for point in family:
        if point.period >= split_at or point.value_normalized_usd_millions is None:
            continue
        moved = replace(
            point,
            product_label=formulation_label,
            source_quote=(
                f"{point.source_quote} (family total attributed to "
                f"{formulation_label}: sole formulation on sale before {split_at})"
            ),
            normalization_status="derived_sole_formulation",
        )
        attributed.append(DerivedFrom(moved, (("family_total", point),)))
    return attributed


# A quarter split by an ownership change is covered by two issuers' partial
# disclosures. The parts are dated, so the split can be checked rather than
# assumed: they must tile the quarter exactly, with no gap and no overlap.
_QUARTER_BOUNDS = {
    1: ("01-01", "03-31"),
    2: ("04-01", "06-30"),
    3: ("07-01", "09-30"),
    4: ("10-01", "12-31"),
}


def assemble_split_ownership_quarter(
    period: str, components: list[dict[str, Any]], *, fiscal_slack_days: int = 7
) -> float | None:
    """Sum the partial-period figures that together cover one quarter.

    When a company is acquired mid-quarter, neither issuer reports the whole
    quarter: the seller's last schedule stops at the closing date and the
    buyer's first one starts there. An acquisition closing on 16 June leaves
    that quarter stated only as an April 1 - June 15 figure plus a June 16
    onwards one.

    This is not the residual arithmetic the rest of this module does, and it is
    deliberately stricter about what it will add. Two numbers are easy to
    combine in a way that looks right - double-counting the days on both sides
    of the close, or silently dropping a stretch neither issuer covered - so
    the parts must be contiguous, must not overlap, and must start at the
    quarter's first day before they are summed.

    The one thing not required is that they stop exactly at the quarter's last
    day. Issuers on a 52/53-week fiscal calendar do not end quarters on month
    ends: J&J's second quarter of 2017 ran to July 2, so its stub reaches two
    days into calendar Q3 and no assembled figure can be exactly calendar Q2.
    That overshoot is bounded by ``fiscal_slack_days`` and is a real, small
    imprecision in any bridged quarter - it is documented rather than removed,
    because the alternative is to have no value for the quarter at all. An
    overshoot beyond the bound is a period mismatch, not a fiscal calendar, and
    returns None.
    """
    parsed = _split(period)
    if not parsed or not components:
        return None
    year, quarter = parsed
    first, last = _QUARTER_BOUNDS[quarter]
    quarter_start, quarter_end = f"{year}-{first}", f"{year}-{last}"

    spans: list[tuple[str, str, float]] = []
    for component in components:
        covers = str(component.get("covers", ""))
        value = component.get("value")
        if value is None or covers.count("/") != 1:
            return None
        span_start, span_end = covers.split("/")
        if not span_start <= span_end:
            return None
        spans.append((span_start, span_end, float(value)))

    spans.sort()
    if spans[0][0] != quarter_start:
        return None
    if spans[-1][1] < quarter_end or _days_between(quarter_end, spans[-1][1]) > fiscal_slack_days:
        return None
    for (_, earlier_end, _), (later_start, _, _) in zip(spans, spans[1:]):
        # One comparison rejects both failure modes: an overlap makes the next
        # part start on or before this one ends, and a gap makes it start more
        # than one day after.
        if _next_day(earlier_end) != later_start:
            return None
    return round(sum(value for _, _, value in spans), 6)


def _days_between(earlier: str, later: str) -> int:
    return (_as_date(later) - _as_date(earlier)).days


def _as_date(value: str):
    from datetime import date as _date

    year, month, day = (int(part) for part in value.split("-"))
    return _date(year, month, day)


def _next_day(date: str) -> str:
    from datetime import date as _date
    from datetime import timedelta

    year, month, day = (int(part) for part in date.split("-"))
    return (_date(year, month, day) + timedelta(days=1)).isoformat()


def _as_datapoint(candidate: dict[str, Any]) -> Datapoint | None:
    """A candidate dict read back as the observation it was made from."""
    value = candidate.get("value_normalized_usd_millions")
    if value is None or not candidate.get("period"):
        return None
    return Datapoint(
        product_label=candidate.get("product_label") or candidate.get("formulation") or "",
        period=str(candidate["period"]),
        period_type=candidate.get("period_type") or "quarterly",
        value_normalized_usd_millions=float(value),
        value_as_reported=float(candidate.get("value_reported") or value),
        source_unit=candidate.get("unit") or "millions",
        source_currency=candidate.get("currency") or "USD",
        fx_rate_to_usd=None,
        source_quote=candidate.get("source_quote") or "",
        fingerprint_signature=candidate.get("fingerprint_signature") or "",
        normalization_status="reported",
        rounding_uncertainty_usd_millions=candidate.get("rounding_uncertainty_usd_millions"),
        # What the label said about the figure. A derivation subtracts the
        # figures, not the questions about them: a total nobody could account
        # for, or one that named two products, still names two products after
        # the subtraction, and `_derived_point` carries these across with the
        # rest of the row.
        scope=candidate.get("geography"),
        combined_with=tuple(candidate.get("combined_with") or ()),
        flags=tuple(candidate.get("label_flags") or ()),
    )


def complete_series(
    reported: dict[str, list[dict[str, Any]]],
    *,
    product: str,
    family: str | None = None,
    siblings: Iterable[str] = (),
    commercial_start: str | None = None,
    sibling_first_year: int | None = None,
) -> list[dict[str, Any]]:
    """The quarters this product's own series implies, beyond those reported.

    ``reported`` holds the candidates already extracted, keyed by the product
    they were extracted for: this product, and - where the caller knows of them
    - the family line it belongs to and the sibling formulations that share it.

    Two derivations apply, and both are arithmetic over figures the issuer
    published rather than estimates:

    * the quarter left implicit against a stated total, when every other quarter
      of that total is present;
    * the family total, before any sibling formulation appears in the data, when
      this product is a formulation of that family. When the split happened is
      read off the siblings' own first appearance rather than from a date in
      this file, so a formulation launching earlier or later than expected moves
      the boundary by itself.

    Nothing under-determined is derived. A period on or after the split, or a
    year missing two quarters, stays the gap it is.

    Each returned candidate says what it was computed from and what the figures
    it was computed from were about. A derivation is a claim about the same
    product, geography and formulation as the total it subtracted, and it is
    read from the documents those figures were read from - so both travel with
    it rather than being defaulted by whoever stores it.
    """
    origin: dict[int, dict[str, Any]] = {}

    def observed(candidates: Iterable[dict[str, Any]]) -> list[Datapoint]:
        """The candidates as observations, each remembered by the dict it came from."""
        points: list[Datapoint] = []
        for candidate in candidates:
            point = _as_datapoint(candidate)
            if point is None:
                continue
            origin[id(point)] = candidate
            points.append(point)
        return points

    own = observed(reported.get(product, []))
    records = list(
        complete_quarters_from_totals(own, commercial_start=commercial_start, product=product)
    )

    if family and family != product:
        split_periods = {
            str(candidate["period"])
            for name in siblings
            for candidate in reported.get(name, [])
            if candidate.get("period")
        }
        if sibling_first_year:
            # A sibling sells for a quarter or two before it is large enough to
            # be reported on its own line. Its first appearance in the data is
            # therefore later than its launch, and the quarters in between hold
            # a family total that is no longer only this formulation. The year
            # the sibling was approved bounds the split from the other side.
            split_periods = split_periods | {f"{sibling_first_year}Q1"}
        if split_periods:
            family_points = [
                point
                for point in observed(reported.get(family, []))
                if point.period_type == "quarterly"
            ]
            already = {point.period for point in own} | {r.output.period for r in records}
            records += [
                record
                for record in propagate_sole_formulation(
                    family_points,
                    formulation_periods=split_periods,
                    formulation_label=product,
                )
                if record.output.period not in already
            ]

    from_point = {id(record.output): record for record in records}
    derived = [record.output for record in records]

    def carried(point: Datapoint) -> dict[str, Any]:
        """The identity and the provenance a derived quarter inherits.

        A derivation is a claim about whatever its left-hand side was a claim
        about: the same product, the same geography, the same formulation, read
        from the same document. Stored without them, the row asserts the
        identity the arithmetic just dropped - a region's total published as
        the family's, a combined line published as one product's own - and
        cites whichever document happened to be first.
        """
        # Every point here came out of a record in `records`, which is what
        # `from_point` is keyed on, so there is no point without one.
        record = from_point[id(point)]
        principal = next(
            (source for role, source in record.inputs if role != "quarter"),
            record.inputs[0][1] if record.inputs else None,
        )
        head = origin.get(id(principal)) if principal is not None else None
        carried_fields = {key: (head or {}).get(key) for key in CARRIED_IDENTITY_KEYS}
        carried_fields["_inputs"] = [
            {
                "role": role,
                "period": source.period,
                "period_type": source.period_type,
                "value_normalized_usd_millions": source.value_normalized_usd_millions,
                "extraction_method": (origin.get(id(source)) or {}).get("extraction_method"),
                "datapoint_id": (origin.get(id(source)) or {}).get("_datapoint_id"),
                "source_id": (origin.get(id(source)) or {}).get("source_id"),
                "source_url": (origin.get(id(source)) or {}).get("source_url"),
            }
            for role, source in record.inputs
        ]
        return carried_fields

    return [
        {
            "period": point.period,
            "period_type": point.period_type,
            "value_reported": point.value_as_reported,
            "value_normalized_usd_millions": point.value_normalized_usd_millions,
            "currency": point.source_currency,
            "unit": point.source_unit,
            "source_quote": point.source_quote,
            "confidence": DERIVED_CONFIDENCE,
            "extraction_method": point.normalization_status,
            "rounding_uncertainty_usd_millions": point.rounding_uncertainty_usd_millions,
            "_derived": True,
            "label_flags": (
                ([HELD_FOR_BOUND] if held_for_bound(point) else [])
                + [flag for flag in point.flags if flag != HELD_FOR_BOUND]
            ),
            **carried(point),
        }
        for point in derived
        # A quarter that derives to nothing is not a quarter the issuer left
        # implicit. It is a total that does not cover the year: an acquirer's
        # first year with a product states only the months it owned it, so
        # subtracting the quarters it did report leaves zero where a real
        # figure belongs. Negative is impossible, and zero here has always
        # been that.
        if (point.value_normalized_usd_millions or 0) > 0
    ]
