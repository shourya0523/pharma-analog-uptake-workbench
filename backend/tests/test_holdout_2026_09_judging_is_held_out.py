"""The 2026-09 judging holdout must not touch an issuer any other answer key uses.

The judging changes - the reader's metric gate, the reconciled evidence-judge
prompt and any change to the auto-pass gate - were all found on gold, on run13,
on the smoke run, on `seed/cases/unseen.json`, on `seed/cases/shapes_holdout.json`
and on `seed/cases/holdout_2026_09.json`. Under rule 4 none of those may score a
fix for what it found, so `seed/cases/holdout_2026_09_judging.json` is the set
drawn for them: issuers no other key names, figures read by hand off the filing
each one cites, and both answers - figures the pipeline must publish and figures
it must refuse.

This checks the properties that make a number from it mean anything, not the
number itself: no issuer is scored elsewhere, both answers are present and mixed
inside a case, every figure names the filing it was read from and every empty
one says why, a figure nobody prints says what it was computed from, the windows
reach the quarters, nothing hands the pipeline a document or an issuer, and the
shapes the three changes turn on are each carried by a case - including the rows
and sentences that must never become revenue.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

from tests.answer_keys import cases_in, identifying, scored_words

REPO = Path(__file__).resolve().parents[2]
HOLDOUT = REPO / "seed" / "cases" / "holdout_2026_09_judging.json"

_ACCESSION = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
_PERIOD = re.compile(r"^(\d{4})Q([1-4])$")


def _cases() -> list[dict]:
    return cases_in(HOLDOUT)


def _figures() -> list[dict]:
    return [figure for case in _cases() for figure in case["expect"]]


def _traps() -> list[dict]:
    return [trap for case in _cases() for trap in case.get("traps", ())]


def test_the_file_says_what_it_is_for_and_what_would_spend_it():
    """A set with no stated purpose is spent by whoever reads it next."""
    payload = json.loads(HOLDOUT.read_text())
    note = payload.get("note", "")
    assert "judging" in note, note
    assert "spend" in note, note
    # Naming the changes is what stops it being reused for the next one.
    for change in ("metric gate", "evidence_judge", "auto-pass gate"):
        assert change in note, change


def test_no_case_comes_from_a_scored_issuer():
    """The exclusion is derived from the tree, so a key added later counts."""
    scored = scored_words(excluding=HOLDOUT)
    assert scored, "no answer keys found; this test would pass vacuously"
    for case in _cases():
        overlap = scored & identifying(case["manufacturer"])
        assert not overlap, f"{case['manufacturer']} is already scored: {overlap}"


def test_no_issuer_appears_twice_under_two_spellings():
    """Two cases that name the same company differently are one issuer, and a
    set whose spread is a spelling is narrower than it reads."""
    seen: dict[frozenset[str], str] = {}
    for case in _cases():
        words = frozenset(identifying(case["manufacturer"]))
        assert words, case["manufacturer"]
        for other, name in seen.items():
            if words & other and words != other:
                raise AssertionError(f"{case['manufacturer']} and {name} share a name")
        seen[words] = case["manufacturer"]


def test_both_answers_are_represented():
    """A set that only refuses is passed by a system that always refuses, and
    a set that never refuses is passed by one that publishes anything."""
    figures = _figures()
    stated = [f for f in figures if f["value_normalized_usd_millions"] is not None]
    empty = [f for f in figures if f["value_normalized_usd_millions"] is None]
    assert stated, "no quarter is expected to carry a figure"
    assert empty, "no quarter is expected empty"
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
            assert figure["sources"][0]["form"] == figure["form"], where
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
        for field in posted & set(case):
            value = str(case[field])
            assert "sec.gov" not in value, (case["drug_name"], field)
            assert "api.fda.gov" not in value, (case["drug_name"], field)
            assert not _ACCESSION.search(value), (case["drug_name"], field)


def test_no_case_states_a_cik():
    """A person uploading a product types a ticker, not a CIK.

    Three of the shapes this set carries - an issuer whose revenue rows never
    name the brand, product detail that exists only in an 8-K EX-99, and a
    brand whose first row is an em dash - are only reached by way of the
    issuer's own filings, and a case that stated the CIK would hand the
    pipeline the step that finds them.
    """
    for case in _cases():
        assert "cik" not in case, case["drug_name"]


def test_every_trap_cites_the_filing_that_prints_it_and_says_why():
    """A trap is a row or a sentence the filings really print, and the claim
    that it is not revenue has to be checkable against the document.

    They are not expectations: the eval posts nothing from them and scores
    nothing against them. They are here so that the next reader can see what
    the metric gate is being asked to reject, in the issuer's own words.
    """
    traps = _traps()
    assert traps, "no case carries a row that must not become revenue"
    for case in _cases():
        for trap in case.get("traps", ()):
            where = (case["drug_name"], trap["accession"])
            assert _ACCESSION.match(trap["accession"]), where
            assert trap["source_url"].startswith(
                "https://www.sec.gov/Archives/edgar/data/"), where
            assert trap["source_quote"].strip(), where
            assert trap["why"].strip(), where


# The shapes this set was drawn to carry, as words a case's `shapes` tags
# spell. A snapshot of what the three judging changes turn on; it goes stale
# when one of those changes is redefined, and what makes it stale is a change
# to the reader, the prompt or the gate - not to this file.
_REQUIRED_SHAPES = (
    "expense schedule naming the product",      # the metric gate's own class
    "cost of sales sentence",                   # the same class in prose
    "guidance figure that must not become a quarter",
    "change % columns",                         # a difference is not a level
    "comparative table, current-quarter column",  # the prompt's two-span row
    "two-span heading",                         # what line 11 and :30-34 disagree on
    "must still auto-pass",                     # the gate must not close on these
    "must come back empty",                     # both answers, stated as a shape
    "em dash",                                  # the filer's own nil
    "the revenue rows never name the product",  # the opposite answer
    "under the pair's name",                    # a combined line
    "fourth quarter from annual less nine months",
)


def test_every_shape_the_judging_changes_turn_on_is_carried_by_a_case():
    tags = [tag.lower() for case in _cases() for tag in case["shapes"]]
    missing = [s for s in _REQUIRED_SHAPES if not any(s.lower() in t for t in tags)]
    assert not missing, missing


def test_the_shapes_that_name_a_trap_are_carried_by_a_case_that_has_one():
    """A tag claiming a row that must not become revenue is a claim about the
    filing, and the row itself has to be in the file beside it."""
    claims = ("expense schedule naming the product", "cost of sales sentence",
              "guidance figure that must not become a quarter", "change % columns")
    for claim in claims:
        carrying = [c for c in _cases()
                    if any(claim.lower() in tag.lower() for tag in c["shapes"])]
        assert carrying, claim
        assert any(c.get("traps") for c in carrying), claim


def test_the_set_spans_several_issuers_and_products():
    """One issuer's filing habits are not a measurement of the pipeline.

    More than one issuer, more products than issuers, and a series rather than
    a single quarter behind each product. How many of each is the set's own
    business: a bound here reads as a rule and is nobody's.
    """
    cases = _cases()
    issuers = {case["manufacturer"] for case in cases}
    assert len(issuers) > 1, sorted(issuers)
    assert len(cases) > len(issuers), len(cases)
    for case in cases:
        assert len(case["expect"]) > 1, (case["drug_name"], len(case["expect"]))
    # Both halves of the judging question need more than one issuer behind
    # them, or the number is one filer's house style.
    publishes = {c["manufacturer"] for c in cases
                 if any(f["value_normalized_usd_millions"] is not None
                        for f in c["expect"])}
    assert len(publishes) > 1, sorted(publishes)
