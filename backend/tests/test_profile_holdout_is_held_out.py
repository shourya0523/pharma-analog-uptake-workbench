"""The profile holdout must not touch an issuer or product any answer key uses.

`seed/gold/product_profiles.jsonl` is `seed/product_attributes.csv` copied - the
gold builder reads that file and writes those rows - so gold finds defects in
the derivation and cannot score a fix: every product it holds is one somebody
curated the answer for. This set was built for the change that made the pipeline
derive those attributes, and it is spent on it.

So, as with the member holdout, what is checked here is the property that makes
the number mean anything rather than the number.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.answer_keys import identifying, scored_products, scored_words

REPO = Path(__file__).resolve().parents[2]

# Every profile holdout, found rather than named. A set added later is guarded
# by all of this without anyone remembering to add it, which is the failure the
# member holdout's own guard was written after.
HOLDOUTS = sorted(REPO.glob("seed/holdout_profiles*/product_profiles.json"))


def _cases(path: Path) -> list[dict]:
    return json.loads(path.read_text())["cases"]


def test_there_is_a_set_to_check():
    """Every check below loops over the sets, so an empty glob passes them all."""
    assert HOLDOUTS, "no profile holdout found under seed/"


def test_no_case_comes_from_a_scored_issuer():
    for path in HOLDOUTS:
        scored = scored_words(excluding=path)
        assert scored, "no answer keys found; this test would pass vacuously"
        for case in _cases(path):
            overlap = scored & identifying(case["manufacturer"])
            assert not overlap, f"{path.parent.name}: {case['manufacturer']} is scored: {overlap}"


def test_no_case_is_a_product_another_key_answers_for():
    """An unscored issuer is not enough: a key can name a product without
    naming who sells it, which is how a member case names one."""
    for path in HOLDOUTS:
        scored = {name.casefold() for name in scored_products(excluding=path) if name}
        for case in _cases(path):
            assert case["drug_name"].casefold() not in scored, (
                f"{path.parent.name}: {case['drug_name']}"
            )


def test_both_answers_are_represented():
    """A derivation that resolves nothing passes a set that expects nothing.

    Route carries both answers here: products whose label states one route and
    products approved as an injection and as a capsule, whose label states two
    and therefore states no single route.
    """
    for path in HOLDOUTS:
        cases = _cases(path)
        resolved = [c for c in cases if c["expect"]["route_of_administration"]]
        refused = [c for c in cases if not c["expect"]["route_of_administration"]]
        assert len(resolved) >= 6, f"{path.parent.name}: too few that must resolve"
        assert len(refused) >= 2, f"{path.parent.name}: nothing that must stay unresolved"


def test_every_case_cites_what_its_answer_came_from():
    for path in HOLDOUTS:
        for case in _cases(path):
            assert case.get("source"), f"{path.parent.name}: {case['drug_name']}"
            assert case.get("why"), f"{path.parent.name}: {case['drug_name']}"


def test_the_grouping_keys_group_and_separate():
    """A partition that puts every product in one group, or each in its own, is
    passed by a classifier that always answers the same way and by one that
    never repeats itself. Both must be wrong here."""
    for path in HOLDOUTS:
        for attribute in ("moa_class", "indication_area"):
            values = [case["expect"][attribute] for case in _cases(path)]
            groups = {value: values.count(value) for value in set(values)}
            assert any(count > 1 for count in groups.values()), (
                f"{path.parent.name}: {attribute} groups nothing"
            )
            assert len(groups) > 1, f"{path.parent.name}: {attribute} separates nothing"


def test_the_peer_counts_follow_from_the_other_labels():
    """marketed_peers_at_launch is the roster rule over this file's own rows.

    Recomputed here rather than trusted, because a hand-written count that does
    not follow from the years and areas beside it would score the pipeline
    against a typo. The rule is spelled out again instead of imported: a count
    checked with the code that produced it checks nothing.
    """
    for path in HOLDOUTS:
        cases = _cases(path)
        for case in cases:
            want = case["expect"]
            peers = [
                other for other in cases
                if other["drug_name"] != case["drug_name"]
                and other["expect"]["indication_area"] == want["indication_area"]
                and other["expect"]["first_approval_year"] < want["first_approval_year"]
                and other["expect"]["peer_universe_role"] == "distinct_product"
            ]
            assert want["marketed_peers_at_launch"] == len(peers), (
                f"{path.parent.name}: {case['drug_name']} says "
                f"{want['marketed_peers_at_launch']}, its own rows give {len(peers)} "
                f"({[other['drug_name'] for other in peers]})"
            )


def test_the_era_follows_from_the_year():
    """The era is a five-year bucket, so a row whose era and year disagree is a
    typo that would be scored as a pipeline failure."""
    for path in HOLDOUTS:
        for case in _cases(path):
            year = case["expect"]["first_approval_year"]
            start = year - (year % 5)
            assert case["expect"]["approval_era"] == f"{start}-{start + 4}", (
                f"{path.parent.name}: {case['drug_name']}"
            )
