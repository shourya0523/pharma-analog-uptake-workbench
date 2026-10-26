"""The Jev decision-backend holdouts must not touch an issuer any answer key uses.

Rule 4: score the chat→Jev change on a set it was not built from. Gold and every
spent holdout under seed/ are oracles for other defects; this set is drawn from
issuers none of them names. The property that makes the number mean anything is
checked here; the number itself is produced by scripts/eval.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.answer_keys import identifying, scored_words

REPO = Path(__file__).resolve().parents[2]
MEMBERS = REPO / "seed" / "holdout_members_jev" / "members.json"
CASES = REPO / "seed" / "cases" / "holdout_2026_09_22_jev.json"
# Both files are one held-out draw: member cases and pipeline cases for the
# same decision-backend change. Excluding both keeps either from spending the
# other's issuers when this guard runs.
EXCLUDING = (MEMBERS, CASES)


def test_no_member_case_comes_from_a_scored_issuer():
    payload = json.loads(MEMBERS.read_text())
    scored = scored_words(excluding=EXCLUDING)
    assert scored, "no answer keys found; this test would pass vacuously"
    for case in payload["cases"]:
        overlap = scored & identifying(case["issuer"])
        assert not overlap, f"{case['issuer']} is already scored: {overlap}"


def test_no_pipeline_case_comes_from_a_scored_issuer():
    payload = json.loads(CASES.read_text())
    scored = scored_words(excluding=EXCLUDING)
    assert scored, "no answer keys found; this test would pass vacuously"
    for case in payload["cases"]:
        issuer = case.get("manufacturer") or ""
        overlap = scored & identifying(issuer)
        assert not overlap, f"{issuer} is already scored: {overlap}"


def test_member_both_answers_are_represented():
    cases = json.loads(MEMBERS.read_text())["cases"]
    assert any(c["expected"] for c in cases), "no member has to resolve"
    assert any(not c["expected"] for c in cases), "no member has to be refused"


def test_pipeline_both_answers_are_represented():
    """Publish and refuse must both appear, or a system that always refuses passes."""
    cases = json.loads(CASES.read_text())["cases"]
    assert any(c.get("expect") for c in cases), "no product has figures to publish"
    assert any(not c.get("expect") for c in cases) or any(
        c.get("traps") for c in cases
    ), "no refusal or trap is represented"


def test_every_member_case_carries_its_own_evidence():
    for case in json.loads(MEMBERS.read_text())["cases"]:
        assert case["siblings"], case["member"]
        assert case["candidates"], case["member"]
        if case["expected"]:
            assert case["expected"] in case["candidates"], case["member"]


def test_a_refusal_member_offers_something_wrong_to_return():
    import re

    cases = json.loads(MEMBERS.read_text())["cases"]
    refusals = [c for c in cases if not c["expected"]]
    assert all(c["candidates"] for c in refusals), "a refusal with nothing to return"
    rollups = [
        c["member"]
        for c in refusals
        if any(
            re.sub(r"[^a-z0-9]", "", cand.lower())
            in re.sub(r"[^a-z0-9]", "", c["member"].split(":")[-1].lower())
            for cand in c["candidates"]
        )
    ]
    assert rollups, "no roll-up refusal that names a candidate"


def test_every_published_expect_carries_a_citation():
    for case in json.loads(CASES.read_text())["cases"]:
        for row in case.get("expect") or []:
            assert row.get("source_url"), row
            assert row.get("source_quote"), row
            assert row.get("accession") or (row.get("sources") or [{}])[0].get("accession"), row
