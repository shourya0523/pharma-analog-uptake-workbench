"""The 2026-09-29 harvest-typing holdout must not touch a scored issuer.

Rule 4: guidance-from-quote and dropping unknown loci are scored on a set
they were not built from. Do not score on spent holdout_2026_09_judging.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.models import PeriodType
from app.llm.harvest import harvest_amount_loci
from tests.answer_keys import identifying, products_in, scored_words

REPO = Path(__file__).resolve().parents[2]
HOLDOUT = REPO / "seed" / "cases" / "holdout_2026_09_29_harvest.json"
SPENT_JUDGING = REPO / "seed" / "cases" / "holdout_2026_09_judging.json"

_THREAD = ("Actelion", "Alexion", "Amphastar", "Portola")


def _cases() -> list[dict]:
    return json.loads(HOLDOUT.read_text())["cases"]


def test_the_file_says_what_it_is_for_and_what_would_spend_it():
    note = json.loads(HOLDOUT.read_text()).get("note", "")
    assert "guidance" in note.lower(), note
    assert "spend" in note, note
    assert "holdout_2026_09_judging" in note, note


def test_no_case_comes_from_a_scored_issuer():
    scored = scored_words(excluding=HOLDOUT)
    assert scored, "no answer keys found; this test would pass vacuously"
    for case in _cases():
        overlap = scored & identifying(case["manufacturer"])
        assert not overlap, f"{case['manufacturer']} is already scored: {overlap}"


def test_both_answers_are_represented():
    cases = _cases()
    assert any(c["expected"] for c in cases), "no booked quarter to keep"
    assert any(not c["expected"] for c in cases), "no guidance sentence to refuse"


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


def test_the_judging_holdout_is_not_this_set():
    """That file already carries a guidance figure that must not become a
    quarter; scoring this change on it would spend it twice."""
    assert SPENT_JUDGING.exists()
    assert HOLDOUT.resolve() != SPENT_JUDGING.resolve()


def test_harvest_agrees_with_the_quotes_the_cases_name():
    for case in _cases():
        loci = harvest_amount_loci(case["quote"], product=case["drug_name"])
        if case["expected"]:
            assert loci, case["drug_name"]
            assert all(
                loc.get("period_type") != PeriodType.GUIDANCE.value for loc in loci
            )
        else:
            assert loci
            assert all(
                loc.get("period_type") == PeriodType.GUIDANCE.value for loc in loci
            )
