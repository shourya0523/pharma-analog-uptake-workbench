"""Tests for analog similarity scoring.

The behaviour worth protecting is that not knowing something costs a candidate.
A product we hold two attributes for must not outrank one we hold five for, and
a difference in spelling must not read as a difference in substance.
"""

import json
from pathlib import Path

from app.analytics.analog_matching import (
    MINIMUM_ATTRIBUTES,
    SCORED_ATTRIBUTES,
    WEIGHTS,
    ProductProfile,
    normalise,
    rank_analogs,
    score_analog,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD = REPO_ROOT / "seed" / "gold"


def profile(
    name,
    indication="Calderon's disease",
    moa="acme:pathway",
    route="Inhaled",
    era="2005-2009",
    intensity="high",
):
    return ProductProfile(name, indication, moa, route, era, intensity)


def load_profiles() -> dict[str, ProductProfile]:
    rows = [
        json.loads(line)
        for line in (GOLD / "product_profiles.jsonl").read_text().splitlines()
        if line.strip()
    ]
    return {
        row["drug_name"]: ProductProfile(
            row["drug_name"],
            row["indication_area"],
            row["moa_class"],
            row["route_of_administration"],
            row["approval_era"],
            row["competitive_intensity_at_launch"],
        )
        for row in rows
    }


def test_every_scored_attribute_carries_a_weight():
    """A new attribute on the profile cannot be silently unscored.

    The module refuses to import if the two disagree; this states the property
    the import-time check exists to hold.
    """
    assert set(WEIGHTS) == set(SCORED_ATTRIBUTES)
    assert "drug_name" not in SCORED_ATTRIBUTES


def test_identical_profiles_score_one_on_every_attribute():
    match = score_analog(profile("A"), profile("B"))
    assert match.score == 1.0
    assert match.attributes_compared == len(SCORED_ATTRIBUTES)
    assert match.attributes_unknown == []


def test_a_product_is_not_its_own_analog():
    match = score_analog(profile("A"), profile("A"))
    assert match.score is None
    assert match.reason == "same_product"


def test_an_attribute_the_candidate_withholds_costs_it():
    """Silence is not agreement, and it is not a smaller denominator either.

    A candidate that declares everything but the intensity used to score the
    same 1.0 as one that declared everything, because the attribute it was
    missing left the denominator with it.
    """
    target = profile("Target")
    silent = profile("Silent", intensity=None)
    complete = profile("Complete")

    withheld = score_analog(target, silent)
    full = score_analog(target, complete)

    assert withheld.attributes_unknown == ["competitive_intensity_at_launch"]
    assert withheld.attributes_compared == len(SCORED_ATTRIBUTES) - 1
    assert withheld.score < 1.0
    assert full.score == 1.0
    assert [match.candidate for match in rank_analogs(target, [silent, complete])][0] == "Complete"


def test_an_attribute_the_target_withholds_costs_nobody():
    """What the target never declared is out of the denominator for everyone.

    No candidate gains by it, so it cannot be a way to win by not knowing.
    """
    target = profile("Target", intensity=None)
    candidate = profile("Candidate")

    match = score_analog(target, candidate)

    assert match.attributes_unknown == ["competitive_intensity_at_launch"]
    assert match.score == 1.0


def test_two_agreements_out_of_two_do_not_outrank_a_deep_match():
    """The two-attribute case, which is the one that used to score 1.0.

    The deep match here is deliberately not itself a perfect score: on an exact
    tie the depth term would decide it anyway, and that is not the property
    being tested.
    """
    target = profile("Calderon")
    thin = ProductProfile("NuVessa", None, None, "Inhaled", "2005-2009", None)
    deep = profile("Veltrexa", moa="beta:pathway")

    assert score_analog(target, thin).score < 1.0
    assert score_analog(target, deep).score < 1.0
    assert score_analog(target, deep).score > score_analog(target, thin).score

    ranked = rank_analogs(target, [thin, deep])
    assert [match.candidate for match in ranked] == ["Veltrexa", "NuVessa"]
    assert ranked[1].score is None
    assert ranked[1].reason == "too_few_comparable_attributes"


def test_a_candidate_compared_on_too_little_comes_back_with_a_reason():
    """Dropping it silently leaves an empty catalog and a thin one identical."""
    target = profile("Calderon")
    thin = ProductProfile("NuVessa", None, None, "Inhaled", None, None)

    ranked = rank_analogs(target, [thin])

    assert len(ranked) == 1
    assert ranked[0].candidate == "NuVessa"
    assert ranked[0].score is None
    assert ranked[0].reason == "too_few_comparable_attributes"
    assert ranked[0].attributes_compared < MINIMUM_ATTRIBUTES


def test_depth_wins_inside_a_band_of_closeness():
    target = profile("Calderon")
    # One withholds its era and agrees on everything else; the other declares
    # everything, with an era a bucket away and an intensity one step away. The
    # first scores fractionally higher on fewer attributes.
    shallow = profile("NuVessa", era=None)
    deeper = profile("Veltrexa", era="2010-2014", intensity="medium")

    ranked = rank_analogs(target, [shallow, deeper])

    assert score_analog(target, shallow).score > score_analog(target, deeper).score
    assert abs(score_analog(target, shallow).score - score_analog(target, deeper).score) < 0.05
    assert [match.candidate for match in ranked] == ["Veltrexa", "NuVessa"]


def test_a_spelling_difference_is_not_a_confident_zero():
    assert normalise("route_of_administration", "ORAL") == normalise(
        "route_of_administration", "Oral"
    )
    assert normalise("route_of_administration", "RESPIRATORY (INHALATION)") == normalise(
        "route_of_administration", "Inhaled"
    )

    target = profile("Calderon", route="ORAL")
    candidate = profile("Veltrexa", route="Oral")

    assert score_analog(target, candidate).score == 1.0
    assert "route_of_administration" in score_analog(target, candidate).matched


def test_indication_area_is_scored_and_a_qualifier_is_a_near_miss():
    target = profile("Calderon", indication="Calderon's disease")
    same = profile("Veltrexa", indication="Calderon's disease")
    qualified = profile("NuVessa", indication="Calderon's disease (stage II)")
    other = profile("Acme One", indication="NuVessa syndrome")

    assert "indication_area" in score_analog(target, same).matched
    assert score_analog(target, same).score > score_analog(target, qualified).score
    assert score_analog(target, qualified).score > score_analog(target, other).score


def test_ordered_attributes_earn_partial_credit_but_distant_ones_earn_none():
    target = profile("T", intensity="low", era="2000-2004")
    adjacent = profile("Adjacent", intensity="medium", era="2005-2009")
    distant = profile("Distant", intensity="high", era="2020-2024")

    assert score_analog(target, adjacent).score > score_analog(target, distant).score
    # low vs high is not a near miss in either direction.
    assert "competitive_intensity_at_launch" not in score_analog(target, distant).matched


def test_gold_profiles_rank_same_class_products_above_different_ones():
    """A diagnostic against the curated profiles, not a score.

    These profiles are the oracle that found the defects this module was
    changed for, so under rule 4 they cannot measure the fix. What they can
    still do is show the method running on real values rather than on a
    fixture. A number from this file is not a result.
    """
    profiles = load_profiles()
    ranked = rank_analogs(profiles["Tyvaso"], list(profiles.values()))
    best = next(match for match in ranked if match.score is not None)

    assert profiles[best.candidate].moa_class == "prostacyclin_pathway"
    assert profiles[best.candidate].indication_area == profiles["Tyvaso"].indication_area
    winrevair = next(match for match in ranked if match.candidate == "Winrevair")
    assert winrevair.score < best.score


def test_every_gold_profile_can_be_scored_against_the_catalog():
    """Also a diagnostic. It says the method runs, not that it is right."""
    profiles = load_profiles()
    for name, target in profiles.items():
        ranked = rank_analogs(target, list(profiles.values()))
        scored = [match for match in ranked if match.score is not None]
        assert scored, f"{name} produced no ranked analog at all"
        assert all(match.candidate != name for match in scored)
        # Nothing is dropped: every candidate comes back, ranked or refused.
        assert len(ranked) == len(profiles)
