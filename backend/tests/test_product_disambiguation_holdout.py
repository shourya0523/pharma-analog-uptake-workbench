"""The product-identity gate, run as part of the suite.

The end-to-end eval scores the same question through the API and prints the
detail. This keeps it from becoming a script nobody runs.

Gold is deliberately not opened. That the labels come from issuers no other
answer key spends is a claim about data, so it is checked here rather than
stated: `test_no_case_comes_from_a_scored_issuer` derives the spent issuers
from every other key under seed/ and fails if this set touches one.
"""

from __future__ import annotations

import json

from app.parsing.evidence import product_aliases
from app.quality.candidate_filters import names_a_competing_product
from tests.answer_keys import SEED, identifying, issuers_in, scored_words

FIXTURE = SEED / "holdout_labels" / "product_labels.json"


def test_no_case_comes_from_a_scored_issuer():
    """Rule 4, checked for this set the way the member holdout checks its own.

    The docstring above used to assert the property in prose. A held-out set
    whose issuers another key already scored measures nothing, and prose does
    not notice when a new key is added that spends one of them.
    """
    scored = scored_words(excluding=FIXTURE)
    assert scored, "no answer keys found; this test would pass vacuously"
    for issuer in sorted(issuers_in(FIXTURE)):
        overlap = scored & identifying(issuer)
        assert not overlap, f"{issuer} is already scored: {overlap}"


def _verdicts() -> list[tuple[dict, str]]:
    doc = json.loads(FIXTURE.read_text())
    cases = doc["cases"]
    by_issuer: dict[str, list[str]] = {}
    for case in cases:
        by_issuer.setdefault(case["issuer"], []).append(case["label"])
    out = []
    for case in cases:
        siblings = [
            label for label in by_issuer[case["issuer"]] if label != case["label"]
        ]
        competitor = names_a_competing_product(
            case["label"], product_aliases(case["product"], case.get("generic")), siblings
        )
        out.append((case, "shared" if competitor else "own"))
    return out


def test_a_products_own_line_is_never_refused_on_an_unseen_issuer():
    """A false refusal costs a real quarter, which is what the shape rule risks."""
    lost = [c["label"] for c, got in _verdicts() if c["expect"] == "own" and got != "own"]
    assert not lost, f"own lines refused: {lost}"


def test_a_line_covering_several_products_is_refused_on_an_unseen_issuer():
    """A line reading `Calderon, NuVessa and Calderon XR` is no one product's.

    Publishing it as one product's revenue - with a citation - is the failure
    this project rates worse than a gap, and it is what a catalogue of known
    brands does whenever the line names brands the catalogue does not hold.
    """
    missed = [
        c["label"] for c, got in _verdicts() if c["expect"] == "shared" and got != "shared"
    ]
    assert not missed, f"shared lines attributed to one product: {missed}"


def _names_printed(label: str) -> list[str]:
    """The names a row label prints, where it prints more than one.

    `Calderon/Calderon XR` prints two; `Nebulized Calderon` prints one. The
    separator is the only thing read here - what the names are is the
    fixture's business, not this test's.
    """
    return [part.strip() for part in label.split("/") if part.strip()]


def test_two_trade_names_for_one_product_are_not_two_products():
    """`Calderon/Calderonex` is one product under two trade names, not two.

    Taken as a property over the fixture - every label that prints more than
    one name and is still one product's line - rather than by naming the
    labels. Naming them is what `test_a_refusal_case_offers_something_wrong_to
    _return` records as having broken the moment its own set was rebuilt, and
    it teaches the next reader which brands the key holds. The set must hold
    such a label at all, or the rule is asserted over nothing.
    """
    several = [
        (case, got) for case, got in _verdicts()
        if len(_names_printed(case["label"])) > 1 and case["expect"] == "own"
    ]
    assert several, (
        "no label in this set prints more than one name for one product, so "
        "nothing here exercises the rule"
    )
    refused = [case["label"] for case, got in several if got != "own"]
    assert not refused, f"one product under more than one name, refused: {refused}"
