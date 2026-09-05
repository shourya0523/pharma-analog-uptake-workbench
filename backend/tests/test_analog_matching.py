"""Tests for analog similarity scoring.

The behaviour worth protecting is the refusal: a product whose competitive
intensity was never assessed must not score as though it matched.
"""

import json
from pathlib import Path

from app.analytics.analog_matching import (
    ProductProfile,
    rank_analogs,
    score_analog,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD = REPO_ROOT / "seed" / "gold"


def profile(name, moa="prostacyclin_pathway", route="Inhaled", era="2005-2009", intensity="high"):
    return ProductProfile(name, moa, route, era, intensity)


def load_profiles() -> dict[str, ProductProfile]:
    rows = [
        json.loads(line)
        for line in (GOLD / "product_profiles.jsonl").read_text().splitlines()
        if line.strip()
    ]
    return {
        row["drug_name"]: ProductProfile(
            row["drug_name"],
            row["moa_class"],
            row["route_of_administration"],
            row["approval_era"],
            row["competitive_intensity_at_launch"],
        )
        for row in rows
    }


def test_identical_profiles_score_one_on_all_four_attributes():
    match = score_analog(profile("A"), profile("B"))
    assert match.score == 1.0
    assert match.attributes_compared == 4
    assert match.attributes_unknown == []


def test_a_product_is_not_its_own_analog():
    match = score_analog(profile("A"), profile("A"))
    assert match.score is None
    assert match.reason == "same_product"


def test_unknown_intensity_is_excluded_not_counted_as_agreement():
    """A comparator outside the catalog universe has no intensity label.

    Treating None as a match would rank the products we know least about
    highest, which is the opposite of what an analog search should do.
    """
    target = profile("Target", intensity="high")
    unassessed = ProductProfile("Comparator", "prostacyclin_pathway", "Inhaled", "2005-2009", None)

    match = score_analog(target, unassessed)

    assert match.attributes_unknown == ["competitive_intensity_at_launch"]
    assert match.attributes_compared == 3
    # The three attributes it does declare all agree, so it still scores 1.0 -
    # but on three attributes, and the count says so.
    assert match.score == 1.0
    assert "competitive_intensity_at_launch" not in match.matched


def test_ordered_attributes_earn_partial_credit_but_distant_ones_earn_none():
    target = profile("T", intensity="low", era="2000-2004")
    adjacent = profile("Adjacent", intensity="medium", era="2005-2009")
    distant = profile("Distant", intensity="high", era="2020-2024")

    assert score_analog(target, adjacent).score > score_analog(target, distant).score
    # low vs high is not a near miss in either direction.
    assert "competitive_intensity_at_launch" not in score_analog(target, distant).matched


def test_a_candidate_compared_on_too_little_is_dropped_not_ranked_first():
    """One coincidental agreement must not outrank a real four-way match."""
    target = profile("T")
    thin = ProductProfile("Thin", None, None, None, "high")
    genuine = profile("Genuine")

    ranked = rank_analogs(target, [thin, genuine])

    assert [match.candidate for match in ranked] == ["Genuine"]


def test_gold_profiles_rank_same_class_products_above_different_ones():
    """Against the real dataset, not a fixture."""
    profiles = load_profiles()
    ranked = rank_analogs(profiles["Tyvaso"], [p for p in profiles.values()])
    best = ranked[0]

    assert profiles[best.candidate].moa_class == "prostacyclin_pathway"
    # Winrevair is the only activin-pathway product and is subcutaneous, so it
    # must not surface as a close analog for an inhaled prostacyclin.
    winrevair = next(m for m in ranked if m.candidate == "Winrevair")
    assert winrevair.score < best.score


def test_every_gold_profile_can_be_scored_against_the_catalog():
    profiles = load_profiles()
    for name, target in profiles.items():
        ranked = rank_analogs(target, list(profiles.values()))
        assert ranked, f"{name} produced no analogs at all"
        assert all(match.candidate != name for match in ranked)
