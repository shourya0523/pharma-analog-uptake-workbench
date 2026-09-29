"""The 2026-09-29 identity holdout must not touch a scored issuer.

Rule 4: ticker-first CIK, browse-edgar fallback, cik_ticker_mismatch, and
IR keyed by the resolved issuer are scored on a set they were not built
from. Gold and every spent holdout under seed/ are oracles for other
defects. The property that makes a number from this set mean anything is
checked here, not the number.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.answer_keys import identifying, products_in, scored_words

REPO = Path(__file__).resolve().parents[2]
HOLDOUT = REPO / "seed" / "cases" / "holdout_2026_09_29_identity.json"

# Names the spent split-ownership thread used. A holdout that reused them
# would be scoring the change on the eval that found it.
_THREAD = ("Portola", "Alexion", "Actelion", "Amphastar")


def _cases() -> list[dict]:
    return json.loads(HOLDOUT.read_text())["cases"]


def test_the_file_says_what_it_is_for_and_what_would_spend_it():
    note = json.loads(HOLDOUT.read_text()).get("note", "")
    assert "ticker-first" in note.lower(), note
    assert "spend" in note, note
    assert "browse-edgar" in note, note
    assert "cik_ticker_mismatch" in note, note


def test_no_case_comes_from_a_scored_issuer():
    scored = scored_words(excluding=HOLDOUT)
    assert scored, "no answer keys found; this test would pass vacuously"
    for case in _cases():
        overlap = scored & identifying(case["manufacturer"])
        assert not overlap, f"{case['manufacturer']} is already scored: {overlap}"


def test_both_answers_are_represented():
    """A set that only refuses is passed by a resolver that always refuses."""
    cases = _cases()
    assert any(c["expected"] for c in cases), "no ticker has to bind"
    assert any(not c["expected"] for c in cases), "no ticker has to be refused"


def test_the_thread_that_found_the_defect_is_not_here():
    text = HOLDOUT.read_text().lower()
    banned = {w for name in _THREAD for w in identifying(name)}
    present = {w for w in banned if w in text}
    assert not present, present


def test_no_case_hands_the_pipeline_a_cik():
    for case in _cases():
        assert "cik" not in case, case["drug_name"]
        assert case.get("ticker"), case["drug_name"]


def test_no_product_is_already_an_answer():
    """Gold brands are the other way the spent thread would leak in."""
    mine = {case["drug_name"] for case in _cases()}
    spent = set()
    from tests.answer_keys import answer_key_paths

    for path in answer_key_paths():
        if path.resolve() == HOLDOUT.resolve():
            continue
        spent |= products_in(path)
    overlap = {name for name in mine if name.lower() in {p.lower() for p in spent}}
    assert not overlap, overlap
