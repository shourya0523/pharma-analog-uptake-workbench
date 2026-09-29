"""The 2026-09-29 IR/search admission holdout must not touch a scored issuer.

Rule 4: gating COMPANY_IR / LLM_SEARCH on asked quarters is scored on a set
it was not built from. Gold and every spent holdout under seed/ are oracles
for other defects.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.connectors.coverage import admits_asked_quarters
from tests.answer_keys import identifying, products_in, scored_words

REPO = Path(__file__).resolve().parents[2]
HOLDOUT = REPO / "seed" / "holdout_ir_window" / "2026_09_29.json"

_THREAD = ("Actelion", "Alexion", "Amphastar")


def _cases() -> list[dict]:
    return json.loads(HOLDOUT.read_text())["cases"]


def test_the_file_says_what_it_is_for_and_what_would_spend_it():
    note = json.loads(HOLDOUT.read_text()).get("note", "")
    assert "admission" in note.lower(), note
    assert "spend" in note, note
    assert "annual" in note.lower(), note


def test_no_case_comes_from_a_scored_issuer():
    scored = scored_words(excluding=HOLDOUT)
    assert scored, "no answer keys found; this test would pass vacuously"
    for case in _cases():
        overlap = scored & identifying(case["manufacturer"])
        assert not overlap, f"{case['manufacturer']} is already scored: {overlap}"


def test_both_answers_are_represented():
    cases = _cases()
    assert any(c["binds"] for c in cases), "no locator has to be kept"
    assert any(not c["binds"] for c in cases), "no locator has to be dropped"


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
    mine = {case["drug_name"] for case in _cases()}
    spent = set()
    from tests.answer_keys import answer_key_paths

    for path in answer_key_paths():
        if path.resolve() == HOLDOUT.resolve():
            continue
        spent |= products_in(path)
    overlap = {name for name in mine if name.lower() in {p.lower() for p in spent}}
    assert not overlap, overlap


def test_admission_agrees_with_the_locators_the_cases_name():
    """The holdout is checkable without a live eval: each locator's verdict
    is the procedure this change added."""
    for case in _cases():
        kept = admits_asked_quarters(
            asked=case["asked_quarters"],
            title="",
            url=case["locator"],
        )
        assert kept is case["binds"], case["drug_name"]
