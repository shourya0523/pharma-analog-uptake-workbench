"""The foreign-XBRL holdout must not touch an issuer any answer key uses.

The set exists to score reading a product figure off a foreign private
issuer's own tagged axis, where there is no 10-Q and the member prefix is the
filer's. Its value depends on the issuers being ones nothing else has scored,
and until now nothing checked that - the set was not even discovered, because
the glob that finds answer keys was one directory level too deep.

Same shape as `test_combined_name_holdout_is_held_out.py`, and the same
reason: check the property that makes the number mean anything, not the
number.
"""

from __future__ import annotations

from tests.answer_keys import (
    SEED,
    accessions_in,
    answer_key_paths,
    cases_in,
    identifying,
    issuers_in,
    scored_words,
)

HOLDOUT = SEED / "holdout_foreign_xbrl.json"


def _the_same_set() -> list:
    """This holdout and any answer key built from the same filings.

    The eval case file is this set in a second shape - same issuer, same
    accessions, rewritten as something the pipeline can be pointed at. Derived
    from the accessions both cite rather than named, so regenerating either
    file cannot quietly turn one set into two and make the set spend itself.
    """
    mine = accessions_in(HOLDOUT)
    return [p for p in answer_key_paths() if accessions_in(p) & mine]


def test_the_holdout_is_discovered_at_all():
    assert HOLDOUT in answer_key_paths(), (
        "the holdout is not reached by answer_key_paths(), so no rule-4 guard "
        "can see the issuers it spends"
    )


def test_no_case_comes_from_a_scored_issuer():
    scored = scored_words(excluding=_the_same_set())
    assert scored, "no answer keys found; this test would pass vacuously"
    for issuer in sorted(issuers_in(HOLDOUT)):
        overlap = scored & identifying(issuer)
        assert not overlap, f"{issuer} is already scored: {overlap}"


def test_both_answers_are_represented():
    """A set that only refuses is passed by a reader that always refuses."""
    expectations = [case.get("expect") for case in cases_in(HOLDOUT)]
    assert sum(1 for e in expectations if e == "a figure") >= 3
    assert sum(1 for e in expectations if e == "nothing") >= 3


def test_every_case_cites_the_filing_it_was_read_from():
    """A case without an accession cannot be re-opened, so it is an assertion
    rather than evidence."""
    for case in cases_in(HOLDOUT):
        assert case.get("accession"), case.get("member") or case.get("product")
        assert case.get("instance"), case.get("member") or case.get("product")
