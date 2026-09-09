"""Resolving a filer's private member names to the products we track.

The case that motivates every rule here: United Therapeutics tags Tyvaso,
Tyvaso DPI and Nebulized Tyvaso as three separate products. Any rule loose
enough to be convenient answers a question about one of them with another's
revenue.
"""

from __future__ import annotations

from app.extraction.members import (
    Resolution,
    load_register,
    match,
    resolve,
    save_register,
    words,
)

UTHR = ["Tyvaso", "Tyvaso DPI", "Nebulized Tyvaso", "Remodulin", "Orenitram", "Adcirca"]
GILEAD = ["Biktarvy", "Descovy", "Genvoya", "Odefsey", "Truvada"]


def test_a_member_and_a_product_name_become_the_same_words():
    assert words("uthr:NebulizedTyvasoMember") == ["nebulized", "tyvaso"]
    assert words("Nebulized Tyvaso") == ["nebulized", "tyvaso"]
    assert words("gild:HIVProductsBiktarvyMember") == ["hiv", "products", "biktarvy"]


def test_the_three_tyvasos_stay_apart():
    """The defect this module exists to prevent, stated as three assertions."""
    assert match("uthr:TyvasoMember", UTHR).product == "Tyvaso"
    assert match("uthr:TyvasoDPIMember", UTHR).product == "Tyvaso DPI"
    assert match("uthr:NebulizedTyvasoMember", UTHR).product == "Nebulized Tyvaso"


def test_a_product_is_not_claimed_by_a_longer_sibling():
    """"Tyvaso" is a substring of TyvasoDPI, and must not match it."""
    resolution = match("uthr:TyvasoDPIMember", ["Tyvaso"])
    assert resolution.product is None
    assert resolution.method == "unmatched"


def test_the_longest_match_wins_when_a_member_ends_in_a_shorter_product():
    """NebulizedTyvaso ends in "tyvaso", so both products have a claim."""
    resolution = match("uthr:NebulizedTyvasoMember", ["Tyvaso", "Nebulized Tyvaso"])
    assert resolution.product == "Nebulized Tyvaso"


def test_a_category_prefix_is_read_past():
    resolution = match("gild:HIVProductsBiktarvyMember", GILEAD)
    assert resolution.product == "Biktarvy"
    assert resolution.method == "suffix"


def test_two_products_that_read_the_same_resolve_to_nothing():
    """A product list holding "Tyvaso DPI" and "Tyvaso-DPI" is a data problem.

    Both reduce to the same words, so one of them would be picked by whichever
    the dictionary happened to keep last - which is a coin toss wearing the
    costume of a rule.
    """
    tie = match("uthr:TyvasoDPIMember", ["Tyvaso DPI", "Tyvaso-DPI"])
    assert tie.product is None and tie.method == "ambiguous"
    assert "read the same" in tie.note


def test_a_catch_all_member_matches_no_product():
    resolution = match("us-gaap:ProductAndServiceOtherMember", UTHR)
    assert resolution.product is None
    assert resolution.method == "unmatched"


def test_the_register_is_consulted_before_the_rules(tmp_path):
    """A decision once made is not remade, and can be corrected by hand."""
    path = tmp_path / "members.csv"
    save_register({
        ("United Therapeutics", "uthr:LegacyNameMember"): Resolution(
            "uthr:LegacyNameMember", "Remodulin", "human", 1.0, "renamed in 2021"
        )
    }, path=path)
    register = load_register(path)
    assert resolve("uthr:LegacyNameMember", UTHR, register,
                   issuer="United Therapeutics").product == "Remodulin"
    assert resolve("uthr:LegacyNameMember", [], register,
                   issuer="United Therapeutics").product == "Remodulin"


def test_one_member_name_can_mean_different_things_to_different_filers():
    """us-gaap:ProductMember is Yutrepia for a one-product issuer and a total
    for everyone else, so the issuer has to be part of the key."""
    register = {
        ("Liquidia", "us-gaap:ProductMember"): Resolution(
            "us-gaap:ProductMember", "Yutrepia", "llm", 1.0, "sole marketed product"
        )
    }
    assert resolve("us-gaap:ProductMember", [], register, issuer="Liquidia").product == "Yutrepia"
    elsewhere = resolve("us-gaap:ProductMember", UTHR, register, issuer="United Therapeutics")
    assert elsewhere.product is None


def test_a_model_that_hedges_is_not_believed():
    """Told to abstain when unsure, a model that answers anyway at low
    confidence has said it is guessing - as it did mapping Merck's Ohtuvayre,
    a COPD drug, onto Winrevair."""
    hedged = Resolution("mrk:OhtuvayreMember", "Winrevair", "llm", 0.7, "likely a misspelling")
    assert not hedged.resolved
    confident = Resolution("gild:SofosbuvirVelpatasvirMember", "Epclusa", "llm", 1.0, "generic name")
    assert confident.resolved


def test_the_register_round_trips(tmp_path):
    path = tmp_path / "members.csv"
    entries = {("United Therapeutics", name): match(name, UTHR) for name in
               ("uthr:TyvasoMember", "uthr:TyvasoDPIMember")}
    save_register(entries, path=path)
    assert {m: r.product for (_i, m), r in load_register(path).items()} == {
        "uthr:TyvasoMember": "Tyvaso",
        "uthr:TyvasoDPIMember": "Tyvaso DPI",
    }


def test_the_pipeline_resolves_a_member_against_every_product_it_tracks():
    """A one-name list gives `match` nothing to prefer, and it prefers wrongly.

    `match` decides between a member's possible readings by taking the longest
    product name that ends it — the comment in members.py says
    "NebulizedTyvaso is Nebulized Tyvaso, not Tyvaso". That comparison needs
    the sibling to be in the list. The orchestrator omitted `products`, so
    `candidates_from_instance` fell back to `[product]` and, asked for Tyvaso,
    resolved `uthr:NebulizedTyvasoMember` to Tyvaso by suffix: a sibling
    formulation's tagged revenue accepted as the product's own, from the
    highest-trust reader there is.

    Only the register was masking it, and the register does not cover every
    issuer.
    """
    from app.extraction.members import load_products, match

    products = load_products()
    assert "Nebulized Tyvaso" in products and "Tyvaso" in products

    impoverished = match("uthr:NebulizedTyvasoMember", ["Tyvaso"])
    assert impoverished.product == "Tyvaso", "the bug, pinned so the fix is legible"

    informed = match("uthr:NebulizedTyvasoMember", products)
    assert informed.product == "Nebulized Tyvaso"
    assert informed.method == "exact"


def test_the_orchestrator_passes_that_list_to_the_tagged_reader():
    """The loader existing is not the fix; calling it is."""
    import inspect

    from app.pipeline.orchestrator import PipelineOrchestrator

    source = inspect.getsource(PipelineOrchestrator._tagged_revenue)
    assert "products=load_products()" in source, (
        "candidates_from_instance falls back to [product] when products is "
        "omitted, which is the condition the test above describes"
    )


def test_a_brand_that_capitalises_inside_its_own_name_still_resolves():
    """`words` splits CamelCase because a member is a machine identifier.

    A brand is not. "AmBisome" carries a capital as typography, so the splitter
    turned the product into two words while a filer writing it plainly gave
    one, and no member could ever match. Only a hand-added register entry was
    covering it, which is the register doing the string rules' job for the five
    issuers somebody happened to seed.

    Invented names, so nothing here passes because a real brand is spelled in
    the code.
    """
    known = ["NuVessa", "Calderon", "Nebulized Calderon"]

    # However the filer spells the capital, it is the same product.
    assert match("NuVessa", known).product == "NuVessa"
    assert match("Nuvessa", known).product == "NuVessa"
    assert match("acme:RespiratoryProductsNuVessaMember", known).product == "NuVessa"
    assert match("acme:RespiratoryProductsNuvessaMember", known).product == "NuVessa"

    # And the rule the splitter exists to enforce is untouched: a product is
    # still only a *trailing* run, so a sibling formulation is not the parent.
    assert match("acme:CalderonXRMember", known).product is None
    assert match("acme:NebulizedCalderonMember", known).product == "Nebulized Calderon"
