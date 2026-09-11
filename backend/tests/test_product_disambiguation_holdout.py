"""The product-identity gate, run as part of the suite.

The end-to-end eval scores the same question through the API and prints the
detail. This keeps it from becoming a script nobody runs, the way three readers
here were once written, tested, measured and never called.

Gold is deliberately not opened: the labels come from Biogen, Jazz and Alkermes,
which appear in no dataset in this repository.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.parsing.evidence import product_aliases
from app.quality.candidate_filters import names_a_competing_product

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "seed"
    / "holdout_labels"
    / "product_labels.json"
)


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
    """Biogen prints one line for RITUXAN, GAZYVA and LUNSUMIO.

    The catalogue this replaced held none of those three, so it published that
    line as a single product's revenue - with a citation, which is the failure
    this project rates worse than a gap.
    """
    missed = [
        c["label"] for c, got in _verdicts() if c["expect"] == "shared" and got != "shared"
    ]
    assert not missed, f"shared lines attributed to one product: {missed}"


def test_two_trade_names_for_one_product_are_not_two_products():
    """Jazz prints "Epidiolex/Epidyolex" because it is one product, not two."""
    verdicts = {c["label"]: got for c, got in _verdicts()}
    assert verdicts["Epidiolex/Epidyolex"] == "own"
    assert verdicts["Rylaze/Enrylaze"] == "own"
    assert verdicts["Defitelio/defibrotide"] == "own"
