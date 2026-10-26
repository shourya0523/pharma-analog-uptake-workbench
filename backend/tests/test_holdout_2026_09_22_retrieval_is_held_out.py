"""The 2026-09-22 retrieval residual holdout must not touch a scored issuer.

Rule 4: score the residual retrieval changes (own-year annual, 6-K budget from
asked quarters, exact reportDate / HTML twin ranking, YoY 2.02 reach, 8-K/A
2.01|9.01 exhibits) on a set they were not built from. Gold and every spent
holdout under seed/ are oracles for other defects; this set is drawn from
issuers none of them names. The property that makes the number mean anything
is checked here; the number itself is produced by scripts/eval.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests.answer_keys import accessions_in, answer_key_paths, identifying, scored_words

REPO = Path(__file__).resolve().parents[2]
HOLDOUT = REPO / "seed" / "cases" / "holdout_2026_09_22_retrieval.json"

_ACCESSION = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")


def _cases() -> list[dict]:
    return json.loads(HOLDOUT.read_text())["cases"]


def test_the_file_says_what_it_is_for_and_what_would_spend_it():
    note = json.loads(HOLDOUT.read_text()).get("note", "")
    assert "retrieval" in note.lower(), note
    assert "spend" in note, note


def test_no_case_comes_from_a_scored_issuer():
    scored = scored_words(excluding=HOLDOUT)
    assert scored, "no answer keys found; this test would pass vacuously"
    for case in _cases():
        overlap = scored & identifying(case["manufacturer"])
        assert not overlap, f"{case['manufacturer']} is already scored: {overlap}"


def test_no_filing_here_is_cited_by_another_answer_key():
    mine = accessions_in(HOLDOUT)
    assert mine, "no filing is cited, so this test would pass vacuously"
    for path in answer_key_paths():
        if path.resolve() == HOLDOUT.resolve():
            continue
        shared = mine & accessions_in(path)
        assert not shared, f"{path.name} already cites {sorted(shared)}"


def test_both_answers_are_represented():
    figures = [f for case in _cases() for f in case["expect"]]
    assert any(f["value_normalized_usd_millions"] is not None for f in figures)
    assert any(f["value_normalized_usd_millions"] is None for f in figures)
    mixed = [
        c["drug_name"] for c in _cases()
        if any(f["value_normalized_usd_millions"] is None for f in c["expect"])
        and any(f["value_normalized_usd_millions"] is not None for f in c["expect"])
    ]
    assert mixed, "no product's series carries both publish and refuse"


def test_every_published_expect_carries_a_citation():
    for case in _cases():
        for row in case["expect"]:
            if row["value_normalized_usd_millions"] is None:
                assert row.get("why"), (case["drug_name"], row["period"])
                continue
            assert row.get("source_url"), row
            assert row.get("source_quote"), row
            assert _ACCESSION.match(row.get("accession") or ""), row
            assert "sec.gov/Archives/edgar" in row["source_url"], row


def test_no_case_hands_the_pipeline_a_cik_or_document():
    for case in _cases():
        assert "cik" not in case, case["drug_name"]
        assert "known_source_url" not in case, case["drug_name"]
        assert case.get("ticker"), case["drug_name"]


def test_the_set_spans_several_issuers():
    issuers = {case["manufacturer"] for case in _cases()}
    assert len(issuers) > 1, sorted(issuers)
