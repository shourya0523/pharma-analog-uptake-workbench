"""Score how alike two products are, for picking uptake analogs.

The point of an analog is that its uptake curve is evidence about another
product's. That only holds if the two are alike in ways that actually drive
uptake, so the score here is built from the four attributes the profile
carries: mechanism class, route, approval era, and how crowded the market was
at launch.

The one rule worth stating plainly: an unknown attribute is never a match. Our
competitive intensity is only assessed inside the catalog's own indication
universe, so for a comparator from another therapy area it is None - and
scoring None as "similar" would quietly promote products we know least about.
Unknown attributes are excluded from the denominator instead, and the count of
attributes actually compared travels with the score so a caller can tell a
four-attribute match from a two-attribute one.
"""

from __future__ import annotations

from dataclasses import dataclass

FORMULA_VERSION = "analog_similarity_v1"

# Mechanism carries the most signal about how a launch behaves - a first oral
# agent in a parenteral-only market ramps differently from the fourth product
# in an established class - so it is weighted hardest. Era matters least: it is
# a proxy for the commercial environment, not a property of the drug.
WEIGHTS = {
    "moa_class": 1.0,
    "route_of_administration": 0.75,
    "competitive_intensity_at_launch": 0.75,
    "approval_era": 0.5,
}

_INTENSITY_ORDER = ("low", "medium", "high")


@dataclass(frozen=True)
class ProductProfile:
    drug_name: str
    moa_class: str | None
    route_of_administration: str | None
    approval_era: str | None
    competitive_intensity_at_launch: str | None


@dataclass(frozen=True)
class AnalogMatch:
    candidate: str
    score: float | None
    attributes_compared: int
    attributes_unknown: list[str]
    matched: list[str]
    formula_version: str
    reason: str | None = None


def _era_start(era: str) -> int | None:
    head = era.replace("Pre-", "").replace("+", "").split("-")[0]
    return int(head) if head.isdigit() else None


def _similarity(field: str, left: str, right: str) -> float:
    """1.0 for an exact match, partial credit where the values are ordered."""
    if left == right:
        return 1.0
    if field == "competitive_intensity_at_launch":
        try:
            gap = abs(_INTENSITY_ORDER.index(left) - _INTENSITY_ORDER.index(right))
        except ValueError:
            return 0.0
        # low vs medium is a near miss; low vs high is not a match at all.
        return 0.5 if gap == 1 else 0.0
    if field == "approval_era":
        starts = (_era_start(left), _era_start(right))
        if None in starts:
            return 0.0
        # Adjacent five-year buckets are close enough to be worth half credit;
        # a decade apart is a different commercial era.
        return 0.5 if abs(starts[0] - starts[1]) <= 5 else 0.0
    return 0.0


def score_analog(target: ProductProfile, candidate: ProductProfile) -> AnalogMatch:
    """Weighted similarity over the attributes both products actually declare."""
    if target.drug_name == candidate.drug_name:
        return AnalogMatch(
            candidate.drug_name, None, 0, [], [], FORMULA_VERSION, "same_product"
        )

    earned = 0.0
    available = 0.0
    unknown: list[str] = []
    matched: list[str] = []
    for field, weight in WEIGHTS.items():
        left = getattr(target, field)
        right = getattr(candidate, field)
        if not left or not right:
            unknown.append(field)
            continue
        available += weight
        similarity = _similarity(field, left, right)
        earned += weight * similarity
        if similarity == 1.0:
            matched.append(field)

    if available == 0:
        return AnalogMatch(
            candidate.drug_name,
            None,
            0,
            sorted(unknown),
            [],
            FORMULA_VERSION,
            "no_comparable_attributes",
        )

    return AnalogMatch(
        candidate=candidate.drug_name,
        score=round(earned / available, 4),
        attributes_compared=len(WEIGHTS) - len(unknown),
        attributes_unknown=sorted(unknown),
        matched=matched,
        formula_version=FORMULA_VERSION,
    )


def rank_analogs(
    target: ProductProfile,
    candidates: list[ProductProfile],
    *,
    minimum_attributes: int = 2,
) -> list[AnalogMatch]:
    """Best analogs first, dropping any scored on too little to be meaningful.

    ``minimum_attributes`` is what stops a single coincidental agreement from
    ranking above a genuine four-attribute match: a candidate compared on one
    attribute can score 1.0 and mean nothing.
    """
    scored = [score_analog(target, candidate) for candidate in candidates]
    usable = [
        match
        for match in scored
        if match.score is not None and match.attributes_compared >= minimum_attributes
    ]
    return sorted(
        usable, key=lambda match: (-match.score, -match.attributes_compared, match.candidate)
    )
