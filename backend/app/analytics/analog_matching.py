"""Score how alike two products are, for picking uptake analogs.

The point of an analog is that its uptake curve is evidence about another
product's. That only holds if the two are alike in ways that actually drive
uptake, so the score is built from the attributes the profile carries: what
disease it treats, its mechanism class, its route, its approval era, and how
crowded the market was when it launched.

The rule worth stating plainly: an attribute a candidate does not declare costs
it. Where the target declares something the candidate does not, the weight
stays in the denominator and the candidate earns none of it. The older design
dropped such attributes from the denominator instead, which let a product we
knew two things about outscore one we knew five things about - a ranking that
rewards ignorance, and rewards it invisibly. Where the *target* declares
nothing, there is nothing to compare either way and the weight is out of the
denominator for every candidate alike, so no candidate gains by it.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace

FORMULA_VERSION = "analog_similarity_v2"

# Reasons a candidate carries no score. Each is returned rather than dropped:
# an analyst who never learns a product was considered cannot tell a thin
# catalog from a strict one.
SAME_PRODUCT = "same_product"
NO_COMPARABLE_ATTRIBUTES = "no_comparable_attributes"
TOO_FEW_COMPARABLE_ATTRIBUTES = "too_few_comparable_attributes"


@dataclass(frozen=True)
class ProductProfile:
    drug_name: str
    indication_area: str | None
    moa_class: str | None
    route_of_administration: str | None
    approval_era: str | None
    competitive_intensity_at_launch: str | None


# Every field of the profile but its name. Derived from the dataclass so an
# attribute added to the profile has to be given a weight below, rather than
# being scored by nobody and noticed by nobody.
SCORED_ATTRIBUTES = tuple(item.name for item in fields(ProductProfile) if item.name != "drug_name")

# Disease and mechanism carry the most signal about how a launch behaves - a
# first oral agent in a parenteral-only market ramps differently from the
# fourth product in an established class, and neither comparison means anything
# across two different diseases - so those two are weighted hardest. Era
# matters least: it is a proxy for the commercial environment, not a property
# of the drug.
WEIGHTS = {
    "indication_area": 1.0,
    "moa_class": 1.0,
    "route_of_administration": 0.75,
    "competitive_intensity_at_launch": 0.75,
    "approval_era": 0.5,
}

_unweighted = set(SCORED_ATTRIBUTES) - set(WEIGHTS)
_unscored = set(WEIGHTS) - set(SCORED_ATTRIBUTES)
if _unweighted or _unscored:
    raise RuntimeError(
        "WEIGHTS and ProductProfile disagree about what is scored: "
        f"unweighted={sorted(_unweighted)} not-on-the-profile={sorted(_unscored)}"
    )

_INTENSITY_ORDER = ("low", "medium", "high")

# Spellings of one route folded to a single form before comparison. The right
# hand side is the vocabulary the curated reference data uses; the left hand
# side is what the other sources spell it as - clinical shorthand, and the
# route openFDA reports as a site rather than a manner. Case is handled by
# folding and is not listed here.
#
# Snapshot of the route vocabulary as of this commit. It goes stale when either
# source starts spelling a route a way that is not on it, which shows up as a
# confident 0.0 between two products that share a route.
ROUTE_SYNONYMS = {
    "po": "oral",
    "by mouth": "oral",
    "iv": "intravenous",
    "intravenous infusion": "intravenous",
    "sc": "subcutaneous",
    "sq": "subcutaneous",
    "subq": "subcutaneous",
    "subcutaneous injection": "subcutaneous",
    "im": "intramuscular",
    "inhalation": "inhaled",
    "respiratory (inhalation)": "inhaled",
    "nebulized": "inhaled",
}

# Two candidates whose scores fall in the same band of this width are treated
# as equally close, and the one compared on more attributes wins. Without a
# band, a hundredth of a point earned on two attributes outranks a whole extra
# attribute that agrees.
SCORE_BAND = 0.05

# How much of the profile must actually be comparable before a ranking means
# anything. Two agreements out of two known is still two coincidences.
MINIMUM_ATTRIBUTES = 3


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
    head = era.replace("pre-", "").replace("+", "").split("-")[0]
    return int(head) if head.isdigit() else None


def normalise(field: str, value: str | None) -> str | None:
    """Fold a declared attribute to the form comparisons are made in.

    Case and surrounding space never mean anything here, and for route neither
    does the spelling: a product given as `ORAL` and one given as `Oral` share
    a route, and scoring them 0.0 is a confident wrong answer rather than an
    unknown one.
    """
    if value is None:
        return None
    folded = " ".join(value.split()).casefold()
    if not folded:
        return None
    if field == "route_of_administration":
        return ROUTE_SYNONYMS.get(folded, folded)
    return folded


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
    if field == "indication_area":
        # One indication is written several ways - a bare disease name in one
        # source and the same name with a qualifier in another. "Calderon's
        # disease" inside "Calderon's disease (stage II)" is the same disease
        # named more precisely, which is worth more than nothing and less than
        # a stated agreement.
        return 0.5 if left in right or right in left else 0.0
    return 0.0


def score_analog(target: ProductProfile, candidate: ProductProfile) -> AnalogMatch:
    """Weighted similarity over the attributes the target declares.

    ``attributes_compared`` counts the attributes both products declare;
    ``attributes_unknown`` names the rest, including the ones the candidate is
    silent about and is charged for.
    """
    if target.drug_name == candidate.drug_name:
        return AnalogMatch(candidate.drug_name, None, 0, [], [], FORMULA_VERSION, SAME_PRODUCT)

    earned = 0.0
    available = 0.0
    compared = 0
    unknown: list[str] = []
    matched: list[str] = []
    for field in SCORED_ATTRIBUTES:
        weight = WEIGHTS[field]
        left = normalise(field, getattr(target, field))
        right = normalise(field, getattr(candidate, field))
        if left is None:
            # Nothing to compare against, for this candidate or any other.
            unknown.append(field)
            continue
        available += weight
        if right is None:
            # In the denominator, earning nothing: silence is not agreement.
            unknown.append(field)
            continue
        compared += 1
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
            NO_COMPARABLE_ATTRIBUTES,
        )

    return AnalogMatch(
        candidate=candidate.drug_name,
        score=round(earned / available, 4),
        attributes_compared=compared,
        attributes_unknown=sorted(unknown),
        matched=matched,
        formula_version=FORMULA_VERSION,
    )


def _by_score(match: AnalogMatch) -> tuple[float, int, str]:
    return (-(match.score or 0.0), -match.attributes_compared, match.candidate)


def _by_depth(match: AnalogMatch) -> tuple[int, float, str]:
    return (-match.attributes_compared, -(match.score or 0.0), match.candidate)


def _ranked_in_bands(matches: list[AnalogMatch]) -> list[AnalogMatch]:
    """Score first, but depth ahead of score among candidates that are close.

    Bands are grown from the best score down, each holding the candidates
    within SCORE_BAND of the one that opened it, and inside a band the deeper
    comparison leads. A hundredth of a point is not a reason to prefer the
    product less is known about.
    """
    ordered = sorted(matches, key=_by_score)
    result: list[AnalogMatch] = []
    band: list[AnalogMatch] = []
    for match in ordered:
        if band and (band[0].score or 0.0) - (match.score or 0.0) > SCORE_BAND:
            result.extend(sorted(band, key=_by_depth))
            band = []
        band.append(match)
    result.extend(sorted(band, key=_by_depth))
    return result


def rank_analogs(
    target: ProductProfile,
    candidates: list[ProductProfile],
    *,
    minimum_attributes: int = MINIMUM_ATTRIBUTES,
) -> list[AnalogMatch]:
    """Every candidate, ranked ones first, refusals after with their reason.

    A candidate compared on too little is not ranked and not dropped: it comes
    back with its score withheld and ``too_few_comparable_attributes`` said out
    loud, so an empty ranking can be told apart from an empty catalog.
    """
    ranked: list[AnalogMatch] = []
    refused: list[AnalogMatch] = []
    for candidate in candidates:
        match = score_analog(target, candidate)
        if match.score is None:
            refused.append(match)
        elif match.attributes_compared < minimum_attributes:
            refused.append(replace(match, score=None, reason=TOO_FEW_COMPARABLE_ATTRIBUTES))
        else:
            ranked.append(match)
    refused.sort(key=lambda match: (match.reason or "", match.candidate))
    return _ranked_in_bands(ranked) + refused
