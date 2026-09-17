"""The retrieval holdout must not touch an issuer, or a filing, any other key uses.

Section 12e of `docs/plans/2026-09-17-006-what-twelve-reviews-found.md` was
written against `seed/cases/holdout_2026_09.json` and the runs behind it: the
exhibit the filename rule rejects, the coverage verdict, and the filings a
picker chooses by recency instead of by the quarters asked were all found
there. Under rule 4 none of those may score the fix, so
`seed/cases/holdout_2026_09_retrieval.json` is the set drawn for them.

This checks the properties that make a number from it mean anything, not the
number itself: no issuer and no filing is spent elsewhere, both answers are
present and mixed inside a case, every figure names the filing it was read
from and every empty one says why, the windows reach the quarters, the options
are the ones a person gets as shipped, nothing hands the pipeline a document, a
figure or an issuer identity, and the shapes 12e asks for are each carried by a
case - so an edit that drops the issuer whose exhibits the filename rule misses
fails here rather than quietly scoring a fix that changes nothing.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

from tests.answer_keys import (
    accessions_in,
    answer_key_paths,
    cases_in,
    identifying,
    scored_words,
)

REPO = Path(__file__).resolve().parents[2]
HOLDOUT = REPO / "seed" / "cases" / "holdout_2026_09_retrieval.json"

_ACCESSION = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
_PERIOD = re.compile(r"^(\d{4})Q([1-4])$")


def _cases() -> list[dict]:
    return cases_in(HOLDOUT)


def _figures() -> list[dict]:
    return [figure for case in _cases() for figure in case["expect"]]


def test_the_file_says_what_it_is_for_and_what_would_spend_it():
    """A set with no stated purpose is spent by whoever reads it next."""
    payload = json.loads(HOLDOUT.read_text())
    note = payload.get("note", "")
    assert "12e" in note, note
    assert "spend" in note, note


def test_no_case_comes_from_a_scored_issuer():
    scored = scored_words(excluding=HOLDOUT)
    assert scored, "no answer keys found; this test would pass vacuously"
    for case in _cases():
        overlap = scored & identifying(case["manufacturer"])
        assert not overlap, f"{case['manufacturer']} is already scored: {overlap}"


def test_no_filing_here_is_cited_by_another_answer_key():
    """Two keys that cite one filing are two views of one set.

    The issuer check is on names, which a differently spelled manufacturer can
    slip past; an accession cannot be spelled two ways. The other keys are
    discovered from the tree rather than named, so a set added later is
    compared here without this test being edited.
    """
    mine = accessions_in(HOLDOUT)
    assert mine, "no filing is cited, so this test would pass vacuously"
    for path in answer_key_paths():
        if path.samefile(HOLDOUT):
            continue
        shared = mine & accessions_in(path)
        assert not shared, f"{path.name} already cites {sorted(shared)}"


def test_both_answers_are_represented():
    """A set that only refuses is passed by a system that always refuses, and
    a set that never refuses is passed by one that publishes anything."""
    figures = _figures()
    assert [f for f in figures if f["value_normalized_usd_millions"] is not None]
    assert [f for f in figures if f["value_normalized_usd_millions"] is None]
    # The hard kind: one product whose series carries both, so "found nothing"
    # and "correctly silent" cannot score the same.
    mixed = [
        c["drug_name"] for c in _cases()
        if any(f["value_normalized_usd_millions"] is None for f in c["expect"])
        and any(f["value_normalized_usd_millions"] is not None for f in c["expect"])
    ]
    assert mixed, (
        "no product's series carries both, so 'found nothing' and 'correctly "
        "silent' cannot be told apart"
    )


def test_every_figure_names_the_filing_it_was_read_from():
    """A figure without its filing cannot be re-checked by the next reader, and
    an empty quarter without its reason cannot be told from a gap nobody filled.

    Every document a figure rests on carries its own quote. A quarter that is
    one filing's year less another's nine months rests on two, and a single
    quote stitched from both cannot be checked against either.
    """
    for case in _cases():
        for figure in case["expect"]:
            where = (case["drug_name"], figure["period"])
            assert figure["sources"], where
            assert figure["sources"][0]["source_url"] == figure["source_url"], where
            assert figure["sources"][0]["accession"] == figure["accession"], where
            for source in figure["sources"]:
                assert _ACCESSION.match(source["accession"]), where
                assert source["source_url"].startswith(
                    "https://www.sec.gov/Archives/edgar/data/"), where
                assert source["source_quote"].strip(), where
            if figure["value_normalized_usd_millions"] is None:
                assert figure.get("why"), where
            else:
                assert not figure.get("why"), where


def test_a_figure_that_is_not_printed_says_how_it_was_computed():
    """A quarter read off a row is in the row; a quarter nobody prints is a
    subtraction, and has to name the two figures it came from and rest on the
    documents that print them.

    The value is looked for in the quotes rather than asserted to be absent, so
    the check follows the figure rather than a convention about which rows
    carry a `derivation`.
    """
    for case in _cases():
        for figure in case["expect"]:
            value = figure["value_normalized_usd_millions"]
            if value is None:
                continue
            where = (case["drug_name"], figure["period"])
            # A one- or two-character rendering of the value matches by
            # accident inside any larger number in the row, so only the
            # distinctive spellings count.
            printed = {form for form in (f"{value:.1f}", f"{value:.3f}",
                                         f"{round(value * 1000):,}")
                       if len(form) >= 3}
            quotes = [source["source_quote"] for source in figure["sources"]]
            if any(form in quote for quote in quotes for form in printed):
                continue
            derivation = figure.get("derivation")
            assert derivation, where
            assert any(form in derivation for form in printed), (where, derivation)
            # The two figures it subtracts have to be in the documents it cites.
            inputs = [n for n in re.findall(r"\d[\d,]*\.?\d*", derivation)
                      if len(n) >= 4 and n not in printed]
            assert inputs, (where, derivation)
            for number in inputs:
                assert any(number in quote for quote in quotes), (where, number)


def test_windows_reach_every_expected_quarter():
    """A quarter is reportable from the day after it ends; its 10-Q follows
    within about 45 days and its 10-K within about 90. A window that closes
    before any report of the quarter exists, or opens after the last one,
    scores nothing about that quarter."""
    for case in _cases():
        since = date.fromisoformat(case["options"]["earnings_since"])
        until = date.fromisoformat(case["options"]["earnings_until"])
        for figure in case["expect"]:
            match = _PERIOD.match(figure["period"])
            assert match, figure["period"]
            year, quarter = int(match.group(1)), int(match.group(2))
            end_month = quarter * 3
            quarter_end = (date(year + (end_month == 12), (end_month % 12) + 1, 1)
                           - timedelta(days=1))
            where = (case["drug_name"], figure["period"])
            assert until >= quarter_end + timedelta(days=1), where
            assert since <= quarter_end + timedelta(days=120), where


def test_every_case_declares_the_window_and_the_shipped_options():
    """The set scores the product a person gets, so the switches it states are
    the ones that are on by default.

    The defaults are read off `ExtractionOptions` rather than written down
    here: a switch whose default is flipped there makes this set a measurement
    of something else, and that is the failure this catches.
    """
    from app.domain.models import ExtractionOptions

    shipped = ExtractionOptions()
    stated = set()
    for case in _cases():
        options = case["options"]
        where = case["drug_name"]
        assert options.get("earnings_since"), where
        assert options.get("earnings_until"), where
        for name, value in options.items():
            if isinstance(value, bool):
                stated.add(name)
                assert getattr(shipped, name) == value, (where, name)
    assert stated, "no case states a switch, so this test would pass vacuously"


def test_nothing_hands_the_pipeline_a_document_a_figure_or_an_issuer():
    """The eval posts what a person types; the evidence stays on this side.

    `scripts/eval.py` forwards only the fields it calls DRUG_FIELDS, so a URL
    or a quote that reached one of those would be the answer key going in
    through the front door. The field list is read out of the eval rather than
    copied, so a field added there is checked here the same day.
    """
    import ast

    source = (REPO / "scripts" / "eval.py").read_text()
    posted: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "DRUG_FIELDS" for t in node.targets):
            posted = {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
    assert posted, "eval.py no longer declares DRUG_FIELDS under that name"

    for case in _cases():
        assert "known_source_url" not in case, case["drug_name"]
        # A person uploading a product types a ticker, not a CIK, and a case
        # that carried one would skip the issuer resolution this set is drawn
        # to score - which is half of what choosing filings by the quarters
        # asked has to get right.
        assert "cik" not in case, case["drug_name"]
        assert case.get("ticker"), case["drug_name"]
        for field in posted & set(case):
            value = str(case[field])
            assert "sec.gov" not in value, (case["drug_name"], field)
            assert "api.fda.gov" not in value, (case["drug_name"], field)
            assert not _ACCESSION.search(value), (case["drug_name"], field)


# What 12e asks a set for it to contain, as words a case's `shapes` tags spell.
# A snapshot of that section's requirements; it goes stale when the register's
# requirements change, and what makes it stale is a change to that document.
#
# Each line is a retrieval defect the set exists to expose. Drop the case that
# carries one and the change scores as a success on a set that cannot see it -
# which is the whole reason for the list.
_REQUIRED_SHAPES = (
    "must come back empty",                    # both answers, stated as a shape
    "acquired product",                        # the pre-acquisition history
    "8-K/A item 9.01",                         # where that history is filed
    "annual less nine months",                 # a fourth quarter nobody prints
    "ex99 filename rule misses",               # the exhibit the name test rejects
    "ex_NNNNNN.htm",                           # the other agent's naming
    "product detail only in the earnings exhibit",   # not in the periodic report
    "no quarterly product revenue tagged in XBRL",   # the instance answers nothing
    "named in the filing, no figure anywhere",  # a name is not a figure
)


def test_every_shape_the_register_asked_for_is_carried_by_a_case():
    tags = [tag.lower() for case in _cases() for tag in case["shapes"]]
    missing = [s for s in _REQUIRED_SHAPES if not any(s.lower() in t for t in tags)]
    assert not missing, missing


def test_the_set_spans_several_issuers_and_products():
    """One issuer's filing habits are not a measurement of the pipeline.

    More than one issuer, more products than issuers, and a series rather than
    a single quarter behind each product. How many of each is the set's own
    business.
    """
    cases = _cases()
    issuers = {case["manufacturer"] for case in cases}
    assert len(issuers) > 1, sorted(issuers)
    assert len(cases) > len(issuers), len(cases)
    for case in cases:
        assert len(case["expect"]) > 1, (case["drug_name"], len(case["expect"]))
