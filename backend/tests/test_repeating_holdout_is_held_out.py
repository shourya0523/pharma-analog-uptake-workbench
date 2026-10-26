"""The repeating-failure holdout must not touch an issuer any answer key uses.

Rule 4: score the royalty/partner-cash/local-peer/formulation change on a set it
was not built from. Gold and every spent holdout under seed/ are oracles for
other defects; this set is drawn from issuers none of them names.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.answer_keys import identifying, scored_words

REPO = Path(__file__).resolve().parents[2]
CASES = REPO / "seed" / "cases" / "holdout_2026_09_24_repeating.json"
EXCLUDING = (CASES,)


def test_no_pipeline_case_comes_from_a_scored_issuer():
    payload = json.loads(CASES.read_text())
    scored = scored_words(excluding=EXCLUDING)
    assert scored, "no answer keys found; this test would pass vacuously"
    for case in payload["cases"]:
        issuer = case.get("manufacturer") or ""
        overlap = scored & identifying(issuer)
        assert not overlap, f"{issuer} is already scored: {overlap}"


def test_pipeline_both_answers_are_represented():
    """Publish and refuse must both appear, or a system that always refuses passes."""
    cases = json.loads(CASES.read_text())["cases"]
    assert any(c.get("expect") for c in cases), "no product has figures to publish"
    assert any(not c.get("expect") for c in cases) or any(
        c.get("traps") for c in cases
    ), "no refusal or trap is represented"


def test_required_shapes_are_present():
    """The draw covers the four mechanisms this holdout was built to score."""
    cases = json.loads(CASES.read_text())["cases"]
    shapes = {s for case in cases for s in case.get("shapes") or []}
    blob = " ".join(shapes).lower()
    assert "product sales, net" in blob or "bare" in blob
    assert "license" in blob or "collaboration" in blob or "partner" in blob
    assert "combined" in blob or "rollup" in blob or "royalty" in blob
    assert "formulation" in blob or "ogsiveo" in blob or "brand" in blob


def test_every_published_expect_carries_a_citation():
    for case in json.loads(CASES.read_text())["cases"]:
        for row in case.get("expect") or []:
            assert row.get("source_url"), row
            assert row.get("source_quote"), row
            assert row.get("accession") or (row.get("sources") or [{}])[0].get(
                "accession"
            ), row
