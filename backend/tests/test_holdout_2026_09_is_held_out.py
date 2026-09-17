"""The 2026-09 holdout must not touch an issuer any other answer key uses.

`docs/plans/2026-09-17-006-what-twelve-reviews-found.md` is a register of
defects found on gold, on `seed/cases/unseen.json`, on run13 and on
`seed/cases/shapes_holdout.json`. Under rule 4 none of those may score a fix
for what it found, so `seed/cases/holdout_2026_09.json` is the set drawn for
them: issuers no other key names, figures read by hand off the filing each one
cites, and both answers - figures the pipeline must publish and figures it must
refuse.

This checks the properties that make a number from it mean anything, not the
number itself: no issuer is scored elsewhere, both answers are present and
mixed inside a case, every figure names the filing it was read from and every
empty one says why, the windows reach the quarters, the options are the ones a
person gets, nothing hands the pipeline a document, and the layer-2
expectations cite the application they were read from or say why there is
none.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

from tests.answer_keys import cases_in, identifying, scored_words

REPO = Path(__file__).resolve().parents[2]
HOLDOUT = REPO / "seed" / "cases" / "holdout_2026_09.json"

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
    assert "006" in note or "what-twelve-reviews-found" in note, note
    assert "spend" in note, note


def test_no_case_comes_from_a_scored_issuer():
    scored = scored_words(excluding=HOLDOUT)
    assert scored, "no answer keys found; this test would pass vacuously"
    for case in _cases():
        overlap = scored & identifying(case["manufacturer"])
        assert not overlap, f"{case['manufacturer']} is already scored: {overlap}"


def test_both_answers_are_represented():
    """A set that only refuses is passed by a system that always refuses, and
    a set that never refuses is passed by one that publishes anything."""
    figures = _figures()
    stated = [f for f in figures if f["value_normalized_usd_millions"] is not None]
    empty = [f for f in figures if f["value_normalized_usd_millions"] is None]
    assert len(stated) >= 40, len(stated)
    assert len(empty) >= 20, len(empty)
    # The hard kind: one product whose series carries both, so "found nothing"
    # and "correctly silent" cannot score the same.
    mixed = [
        c["drug_name"] for c in _cases()
        if any(f["value_normalized_usd_millions"] is None for f in c["expect"])
        and any(f["value_normalized_usd_millions"] is not None for f in c["expect"])
    ]
    assert len(mixed) >= 3, mixed


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


def test_a_combined_line_comes_back_under_the_pair_name():
    """A row naming two brands is the pair's figure, not either brand's own.

    The check is on the shape rather than on a product: an expectation carrying
    `reported_as` must name something other than the product it sits under, and
    at least one case must carry one - otherwise nothing here exercises the
    rule that a combined line is published as the combination.
    """
    pairs = [(c["drug_name"], f["reported_as"])
             for c in _cases() for f in c["expect"] if f.get("reported_as")]
    assert pairs, "no expectation asks for a figure under a pair's name"
    for drug, reported_as in pairs:
        assert reported_as != drug, (drug, reported_as)
        assert drug.lower() in reported_as.lower(), (drug, reported_as)


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


def test_nothing_hands_the_pipeline_a_document_or_a_figure():
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
        # that carries one skips the identity step this set is drawn to score.
        assert "cik" not in case, case["drug_name"]
        for field in posted & set(case):
            value = str(case[field])
            assert "sec.gov" not in value, (case["drug_name"], field)
            assert "api.fda.gov" not in value, (case["drug_name"], field)
            assert not _ACCESSION.search(value), (case["drug_name"], field)


def test_every_layer_two_expectation_cites_an_application_or_says_why():
    """Route, first approval and indication are answers too, and each is either
    read from a named application or refused with the search that came back
    empty. A field left blank with no reason is a hole, not an expectation."""
    fields = ("route_of_administration", "first_approval_year", "indication_area")
    for case in _cases():
        profile = case["profile"]
        for field in fields:
            where = (case["drug_name"], field)
            if profile.get(field) is None:
                assert profile.get("why", {}).get(field), where
            else:
                assert profile["application_number"], where
                assert profile["application_source_url"].startswith(
                    "https://api.fda.gov/drug/"), where


def test_layer_two_holds_agreeing_and_disagreeing_route_fields():
    """`openfda.route` and `products[].route` are two readings of one record.

    A set where they always agree cannot tell the two field paths apart, and a
    fix that changed which one is read would score the same either way. Both
    outcomes have to be here, and so does a product drugsFDA holds no
    application for at all.
    """
    profiles = [c["profile"] for c in _cases()]
    with_application = [p for p in profiles if p["application_number"]]
    assert [p for p in with_application if p["route_fields_agree"] is True]
    assert [p for p in with_application if p["route_fields_agree"] is False]
    without = [p for p in profiles if not p["application_number"]]
    assert without
    # Two field paths that hold nothing neither agree nor disagree.
    assert all(p["route_fields_agree"] is None for p in without), without


# The shapes this set was drawn to carry, as words a case's `shapes` tags
# spell. A snapshot of what `006` asks for; it goes stale when the register's
# list changes, and what makes it stale is a change to that document.
_REQUIRED_SHAPES = (
    "must come back empty",                     # both answers, stated as a shape
    "pre-launch",                               # a quarter before the product existed
    "de minimis",                               # a quarter the issuer declines to state
    "the issuer stopped reporting the product separately",
    "under the pair's name",                    # a combined line, reported_as
    "8-K EX-99",                                # product detail outside the 10-Q
    "acquired product",                         # the acquisition bridge
    "8-K/A",                                    # the acquirer's retrospective statement
    "annual less nine months",                  # a fourth quarter nobody prints
    "Product - Region",                         # row label against a scope column
    "named in the filing, no figure anywhere",  # a name is not a figure
)


def test_every_shape_the_register_asked_for_is_carried_by_a_case():
    tags = [tag.lower() for case in _cases() for tag in case["shapes"]]
    missing = [s for s in _REQUIRED_SHAPES if not any(s.lower() in t for t in tags)]
    assert not missing, missing


def test_the_set_spans_several_issuers_and_products():
    """One issuer's filing habits are not a measurement of the pipeline."""
    cases = _cases()
    issuers = {case["manufacturer"] for case in cases}
    assert 3 <= len(issuers) <= 5, sorted(issuers)
    assert 8 <= len(cases) <= 12, len(cases)
    for case in cases:
        assert 6 <= len(case["expect"]) <= 10, (case["drug_name"], len(case["expect"]))
