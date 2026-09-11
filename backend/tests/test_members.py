"""Resolving a filer's private member names to the products we track.

The case that motivates every rule here: United Therapeutics tags Tyvaso,
Tyvaso DPI and Nebulized Tyvaso as three separate products. Any rule loose
enough to be convenient answers a question about one of them with another's
revenue.
"""

from __future__ import annotations

from app.extraction.members import (
    VERDICT_NO_CANDIDATE_MATCH,
    VERDICT_NOT_A_PRODUCT,
    VERDICT_PRODUCT,
    Resolution,
    fingerprint,
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


def test_a_no_match_does_not_outrank_the_rules_for_a_list_it_never_saw():
    """The defect that moved the register into the database.

    `gild:TrodelvyMember` was recorded as naming no product, because Trodelvy
    was not among the products tracked on the day the register was built. The
    register is consulted before the rules, so a run that *did* ask for
    Trodelvy read that answer and skipped a tagged fact the rules place on the
    first try - the drug silently absent from a filing that reports it.
    """
    judged_against = ["Biktarvy", "Descovy"]
    register = {
        ("Gilead", "gild:TrodelvyMember"): Resolution(
            "gild:TrodelvyMember", None, "llm", 1.0,
            "names a product not in the candidate list",
            verdict=VERDICT_NO_CANDIDATE_MATCH,
            candidates_fingerprint=fingerprint(judged_against),
        )
    }

    stands = resolve("gild:TrodelvyMember", judged_against, register, issuer="Gilead")
    assert stands.product is None, "same list, same answer - nothing has changed"

    uploaded = resolve("gild:TrodelvyMember", [*judged_against, "Trodelvy"],
                       register, issuer="Gilead")
    assert uploaded.product == "Trodelvy"
    assert uploaded.method == "exact"


def test_a_member_that_names_no_product_at_all_stays_settled():
    """The other half of the split, and the reason it is a reviewer's call.

    A category total is a fact about the member, so it holds against any list.
    A model asked which of these products a member names cannot report that -
    "none of these" is all it can see - so `not_a_product` is only ever set by
    a person, and this is what setting it buys them.
    """
    register = {
        ("Gilead", "gild:HIVProductSalesMember"): Resolution(
            "gild:HIVProductSalesMember", None, "human", 1.0, "a category, not a product",
            verdict=VERDICT_NOT_A_PRODUCT,
        )
    }
    for products in (["Biktarvy"], ["Biktarvy", "Trodelvy"], []):
        assert resolve("gild:HIVProductSalesMember", products,
                       register, issuer="Gilead").product is None


def test_a_negative_with_no_list_recorded_is_spent_rather_than_binding():
    """Provenance the row does not carry cannot be taken on trust."""
    register = {
        ("Gilead", "gild:TrodelvyMember"): Resolution(
            "gild:TrodelvyMember", None, "llm", 1.0, "no list recorded",
            verdict=VERDICT_NO_CANDIDATE_MATCH,
        )
    }
    assert resolve("gild:TrodelvyMember", ["Trodelvy"], register,
                   issuer="Gilead").product == "Trodelvy"


def test_the_fingerprint_tracks_the_list_and_not_how_it_was_written():
    assert fingerprint(["Tyvaso", "Remodulin"]) == fingerprint(["Remodulin", "Tyvaso ", "Tyvaso"])
    assert fingerprint(["Tyvaso"]) != fingerprint(["Tyvaso", "Remodulin"])


def test_a_positive_holds_whoever_is_asking():
    """Only negatives are relative to a list. A member that names a product
    names it whether or not the asker happens to track it."""
    register = {
        ("United Therapeutics", "uthr:TyvasoDPIMember"): Resolution(
            "uthr:TyvasoDPIMember", "Tyvaso DPI", "exact", 1.0, "",
            verdict=VERDICT_PRODUCT,
        )
    }
    assert resolve("uthr:TyvasoDPIMember", [], register,
                   issuer="United Therapeutics").product == "Tyvaso DPI"


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
    """The loader existing is not the fix; calling it is.

    The list is now the tracked products *and* the drugs this run was asked
    about, because a drug uploaded at run time is otherwise a drug no member
    can name - the rules would be asked to place `gild:TrodelvyMember` against
    a list with no Trodelvy in it.
    """
    import inspect

    from app.pipeline.orchestrator import PipelineOrchestrator

    source = inspect.getsource(PipelineOrchestrator._tagged_revenue)
    assert "products=products" in source, (
        "candidates_from_instance falls back to [product] when products is "
        "omitted, which is the condition the test above describes"
    )
    assert "load_products()" in source and "run_products" in source
