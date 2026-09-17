"""The shapes holdout must not touch an issuer any answer key uses.

Every item in docs/plan-after-the-full-sweep.md was found on gold, on
seed/cases/unseen.json or on the sweep's database, so under rule 4 none of
those may score its fix. seed/cases/shapes_holdout.json is the set drawn for
them: about twenty product-years chosen by the *shape* of what the filing
prints, from issuers no other answer key names, every figure read by hand
from the filing it cites.

This checks the properties that make the number mean anything, not the
number: no issuer is scored elsewhere, both answers are represented, every
figure names the filing it was read from, and each shape the plan asked for
is carried by at least one case.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

from tests.answer_keys import identifying, scored_words

REPO = Path(__file__).resolve().parents[2]
HOLDOUT = REPO / "seed" / "cases" / "shapes_holdout.json"

_ACCESSION_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
_PERIOD_RE = re.compile(r"^(\d{4})Q([1-4])$")


def _cases() -> list[dict]:
    return json.loads(HOLDOUT.read_text())


def test_no_case_comes_from_a_scored_issuer():
    scored = scored_words(excluding=HOLDOUT)
    assert scored, "no answer keys found; this test would pass vacuously"
    for case in _cases():
        overlap = scored & identifying(case["manufacturer"])
        assert not overlap, f"{case['manufacturer']} is already scored: {overlap}"


def test_both_answers_are_represented():
    """A set that only refuses is passed by a system that always refuses, and
    a set that never refuses is passed by one that publishes anything."""
    figures = [e for c in _cases() for e in c["expect"]]
    stated = [e for e in figures if e["value_normalized_usd_millions"] is not None]
    empty = [e for e in figures if e["value_normalized_usd_millions"] is None]
    assert stated, "no quarter is expected to carry a figure"
    assert empty, "no quarter is expected empty"
    # An empty quarter from an issuer that keeps filing is the hard kind: the
    # product exists, the filer reports, and the figure still is not there.
    # Without one, "nothing found" and "correctly silent" are the same score.
    hard = [
        c for c in _cases()
        if any(e["value_normalized_usd_millions"] is None for e in c["expect"])
        and any(e["value_normalized_usd_millions"] is not None for e in c["expect"])
    ]
    assert hard, "no case mixes a stated quarter with an empty one"


def test_every_figure_names_the_filing_it_was_read_from():
    """A figure without its accession cannot be checked by the next reader,
    and an empty quarter without its reason cannot be told from a gap."""
    for case in _cases():
        for figure in case["expect"]:
            if figure["value_normalized_usd_millions"] is None:
                assert figure.get("why"), (case["drug_name"], figure["period"])
            else:
                cite = figure.get("cite", "")
                assert _ACCESSION_RE.search(cite), (case["drug_name"], figure["period"])
                assert "sec.gov/Archives/edgar/data/" in cite, (case["drug_name"], figure["period"])


def test_windows_reach_every_expected_quarter():
    """A quarter is reportable from the day after it ends; its 10-Q follows
    within about 45 days and its 10-K within about 90. A window that closes
    before the report exists, or opens after every report of the quarter,
    scores nothing about that quarter."""
    for case in _cases():
        since = date.fromisoformat(case["options"]["earnings_since"])
        until = date.fromisoformat(case["options"]["earnings_until"])
        for figure in case["expect"]:
            match = _PERIOD_RE.match(figure["period"])
            assert match, figure["period"]
            year, quarter = int(match.group(1)), int(match.group(2))
            end_month = quarter * 3
            quarter_end = (date(year + (end_month == 12), (end_month % 12) + 1, 1)
                           - timedelta(days=1))
            assert until >= quarter_end + timedelta(days=1), (case["drug_name"], figure["period"])
            assert since <= quarter_end + timedelta(days=120), (case["drug_name"], figure["period"])


# The shapes item 0 of docs/plan-after-the-full-sweep.md asked for, as the
# words a case's `shapes` tags carry. This is a snapshot of that list, and it
# goes stale when the plan's list changes. One shape the plan asked for -
# a Product - Region table whose total row is unlabelled - was found in no
# unspent issuer's filings and is deliberately absent here; gold's Gilead
# tables are its only instance and cannot score a fix.
_REQUIRED_SHAPES = (
    "footnote",                       # a footnote read, not stripped
    "joined by",                      # a label naming several brands
    "labelled Total row",             # Product - Region with a labelled total
    "tags thousands",                 # tagged in thousands, printed one decimal
    "own row above",                  # product name over US / Intl / WW rows
    "prose-only",                     # a release stating the figure in a sentence
    "cost row naming the product",    # a cost printed beside the product
    "milestone",                      # a milestone printed beside the product
    "52/53-week",                     # quarters ending April 1 / July 1
    "annual report",                  # a 10-K in the window
    "inline-xbrl cover pages",        # every recent 8-K carries one
    "non-SEC filer",                  # owned by a non-SEC filer for part of the window
    "must come back empty",           # quarters that must come back empty
)


def test_every_shape_the_plan_asked_for_is_carried_by_a_case():
    tags = [tag.lower() for c in _cases() for tag in c["shapes"]]
    missing = [s for s in _REQUIRED_SHAPES if not any(s.lower() in t for t in tags)]
    assert not missing, missing
    empty_quarters = sum(
        1 for c in _cases() if any("must come back empty" in t.lower() for t in c["shapes"])
        for e in c["expect"] if e["value_normalized_usd_millions"] is None
    )
    assert empty_quarters, (
        "a case is tagged as having to come back empty and expects a figure "
        "for every quarter"
    )


def test_cases_are_what_a_person_would_type():
    """The eval posts the drug, who makes it and the window; nothing here may
    hand the pipeline a document or a figure.

    What the options may be is `test_every_case_asks_for_the_configuration_that
    _ships`, over every file in `seed/cases/` rather than over this one.
    """
    for case in _cases():
        assert "known_source_url" not in case, case["drug_name"]
