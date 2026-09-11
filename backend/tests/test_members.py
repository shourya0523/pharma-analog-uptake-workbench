"""Resolving a filer's private member names to the products we track.

The case that motivates every rule here: a filer tags Calderon, Calderon XR
and Nebulized Calderon as three separate products. Any rule loose enough to be
convenient answers a question about one of them with another's revenue.

Invented names throughout, so nothing here passes because a real brand is
spelled in the code.
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

ACME = ["Calderon", "Calderon XR", "Nebulized Calderon", "Tavoral", "Orenix", "Emrilex"]
BETA = ["Veltrexa", "Cordexa", "Velantis", "Pyrenil", "Sarnex"]


def test_a_member_and_a_product_name_become_the_same_words():
    assert words("acme:NebulizedCalderonMember") == ["nebulized", "calderon"]
    assert words("Nebulized Calderon") == ["nebulized", "calderon"]
    assert words("beta:RespiratoryProductsVeltrexaMember") == ["respiratory", "products", "veltrexa"]


def test_the_three_calderons_stay_apart():
    """The defect this module exists to prevent, stated as three assertions."""
    assert match("acme:CalderonMember", ACME).product == "Calderon"
    assert match("acme:CalderonXRMember", ACME).product == "Calderon XR"
    assert match("acme:NebulizedCalderonMember", ACME).product == "Nebulized Calderon"


def test_a_product_is_not_claimed_by_a_longer_sibling():
    """"Calderon" is a substring of CalderonXR, and must not match it."""
    resolution = match("acme:CalderonXRMember", ["Calderon"])
    assert resolution.product is None
    assert resolution.method == "unmatched"


def test_the_longest_match_wins_when_a_member_ends_in_a_shorter_product():
    """NebulizedCalderon ends in "calderon", so both products have a claim."""
    resolution = match("acme:NebulizedCalderonMember", ["Calderon", "Nebulized Calderon"])
    assert resolution.product == "Nebulized Calderon"


def test_a_category_prefix_is_read_past():
    resolution = match("beta:RespiratoryProductsVeltrexaMember", BETA)
    assert resolution.product == "Veltrexa"
    assert resolution.method == "suffix"


def test_two_products_that_read_the_same_resolve_to_nothing():
    """A product list holding "Calderon XR" and "Calderon-XR" is a data problem.

    Both reduce to the same words, so one of them would be picked by whichever
    the dictionary happened to keep last - which is a coin toss wearing the
    costume of a rule.
    """
    tie = match("acme:CalderonXRMember", ["Calderon XR", "Calderon-XR"])
    assert tie.product is None and tie.method == "ambiguous"
    assert "read the same" in tie.note


def test_a_catch_all_member_matches_no_product():
    resolution = match("us-gaap:ProductAndServiceOtherMember", ACME)
    assert resolution.product is None
    assert not resolution.resolved
    # It declines as "joined" rather than "unmatched": the member carries an
    # "And", and the rules stop at one before asking which trailing run wins.
    # Either way it names no product, which is the claim being made here.
    assert resolution.method in {"unmatched", "joined"}


def test_the_register_is_consulted_before_the_rules(tmp_path):
    """A decision once made is not remade, and can be corrected by hand."""
    path = tmp_path / "members.csv"
    save_register({
        ("Acme Pharma", "acme:LegacyNameMember"): Resolution(
            "acme:LegacyNameMember", "Tavoral", "human", 1.0, "renamed in 2021"
        )
    }, path=path)
    register = load_register(path)
    assert resolve("acme:LegacyNameMember", ACME, register,
                   issuer="Acme Pharma").product == "Tavoral"
    assert resolve("acme:LegacyNameMember", [], register,
                   issuer="Acme Pharma").product == "Tavoral"


def test_one_member_name_can_mean_different_things_to_different_filers():
    """us-gaap:ProductMember is Monovex for a one-product issuer and a total
    for everyone else, so the issuer has to be part of the key."""
    register = {
        ("Solo Biosciences", "us-gaap:ProductMember"): Resolution(
            "us-gaap:ProductMember", "Monovex", "llm", 1.0, "sole marketed product"
        )
    }
    assert resolve("us-gaap:ProductMember", [], register, issuer="Solo Biosciences").product == "Monovex"
    elsewhere = resolve("us-gaap:ProductMember", ACME, register, issuer="Acme Pharma")
    assert elsewhere.product is None


def test_a_model_that_hedges_is_not_believed():
    """Told to abstain when unsure, a model that answers anyway at low
    confidence has said it is guessing - as it does when it maps a member
    naming a product nobody tracks onto the nearest product that is."""
    hedged = Resolution("beta:OhtenzaMember", "NuVessa", "llm", 0.7, "likely a misspelling")
    assert not hedged.resolved
    confident = Resolution("beta:NolvirVexatanMember", "Nolvexa", "llm", 1.0, "generic name")
    assert confident.resolved


def test_the_register_round_trips(tmp_path):
    path = tmp_path / "members.csv"
    entries = {("Acme Pharma", name): match(name, ACME) for name in
               ("acme:CalderonMember", "acme:CalderonXRMember")}
    save_register(entries, path=path)
    assert {m: r.product for (_i, m), r in load_register(path).items()} == {
        "acme:CalderonMember": "Calderon",
        "acme:CalderonXRMember": "Calderon XR",
    }


def test_the_pipeline_resolves_a_member_against_every_product_it_tracks():
    """A one-name list gives `match` nothing to prefer, and it prefers wrongly.

    `match` decides between a member's possible readings by taking the longest
    product name that ends it, and that comparison needs the sibling to be in
    the list. The orchestrator omitted `products`, so `candidates_from_instance`
    fell back to `[product]` and, asked for the shorter of two sibling names,
    matched the longer one's member to it by suffix: a sibling formulation's
    tagged revenue accepted as the product's own, from the highest-trust reader
    there is.

    Only the register was masking it, and the register does not cover every
    issuer.

    The pair is found in the products file rather than named here, so the test
    is about the shape and not about which two products happen to have it.
    """
    from app.extraction.members import load_products, match

    products = load_products()
    trailing = lambda outer, inner: (
        words(outer)[-len(words(inner)):] == words(inner) if words(inner) else False
    )
    # A sibling pair no third product also claims, so what is asserted is the
    # two-way choice rather than a tie.
    short, long = next(
        (s, l)
        for s in sorted(products)
        for l in sorted(products)
        if l != s
        and trailing(l, s)
        and sum(1 for other in products if trailing(l, other)) == 2
    )
    member = "acme:" + "".join(word.capitalize() for word in words(long)) + "Member"

    impoverished = match(member, [short])
    assert impoverished.product == short, "the bug, pinned so the fix is legible"

    informed = match(member, products)
    assert informed.product == long


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

    A brand is not. "NuVessa" carries a capital as typography, so the splitter
    turned the product into two words while a filer writing it plainly gave
    one, and no member could ever match. Only a hand-added register entry was
    covering it, which is the register doing the string rules' job for whichever
    issuers somebody happened to seed.
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


def test_a_member_joining_two_names_is_not_the_last_one():
    """`CalderonAndNuVessa` is a line covering both, not NuVessa's revenue.

    The trailing-run rule reads the last name in a member, which is right for a
    category prefix ("RespiratoryProductsNuVessa") and wrong for a joined pair. The
    rules cannot tell those apart, so they decline and the model decides: one
    call, against a figure that includes another product being published as
    this one's.
    """
    known = ["Calderon", "NuVessa"]
    for member in ("CalderonAndNuVessa", "acme:CalderonAndNuVessaMember",
                   "Calderon&NuVessa", "Calderon+NuVessa"):
        outcome = match(member, known)
        assert not outcome.resolved, f"{member} resolved to {outcome.product}"

    # A category that merely ends in a product's name is still resolved.
    assert match("acme:RespiratoryProductsNuVessa", known).product == "NuVessa"


def test_a_decision_reaches_the_reader_that_did_not_make_it():
    """One member, two notations, and the register must answer to both.

    The bulk notes datasets store a segment stripped of prefix and suffix
    ("EmravirEmravix"); a filing's own instance names it in full
    ("beta:EmravirEmravixMember"). Keyed literally, a register built from one
    is invisible to the other, and the product reads through whichever reader
    happened to write it and through no other.
    """
    from app.extraction.members import canonical_member

    register = {("Beta Therapeutics", "EmravirEmravix"): Resolution(
        "EmravirEmravix", "Emravir", "llm", 0.9, "US and EU trade names"
    )}
    for spelling in ("EmravirEmravix", "beta:EmravirEmravixMember",
                     "EmravirEmravixMember", "Emraviremravix"):
        assert resolve(spelling, BETA, register, issuer="Beta Therapeutics").product == "Emravir", spelling
    assert canonical_member("beta:EmravirEmravixMember") == "emraviremravix"


def test_the_issuer_is_still_part_of_the_key():
    """Re-keying on identity must not merge two filers' taxonomies."""
    register = {("Solo Biosciences", "us-gaap:ProductMember"): Resolution(
        "us-gaap:ProductMember", "Monovex", "llm", 1.0, "sole marketed product"
    )}
    assert resolve("ProductMember", [], register, issuer="Solo Biosciences").product == "Monovex"
    assert resolve("ProductMember", ACME, register,
                   issuer="Acme Pharma").product is None


def test_two_spellings_decided_differently_answer_neither():
    """A disagreement in the register is not settled by row order."""
    register = {
        ("Acme", "CalderonXR"): Resolution("CalderonXR", "Calderon", "human", 1.0, ""),
        ("Acme", "acme:CalderonXRMember"): Resolution(
            "acme:CalderonXRMember", "NuVessa", "human", 1.0, "corrected"
        ),
    }
    assert resolve("acme:CalderonXRMember", [], register, issuer="Acme").product == "NuVessa", (
        "an exact key still wins outright; only the identity fallback abstains"
    )
    assert resolve("CalderonXRMember", [], register, issuer="Acme").product is None


def test_an_edit_to_the_register_is_not_answered_from_a_stale_index():
    register = {("Acme", "Foo"): Resolution("Foo", "NuVessa", "human", 1.0, "")}
    assert resolve("acme:FooMember", [], register, issuer="Acme").product == "NuVessa"
    register[("Acme", "Bar")] = Resolution("Bar", "Calderon", "human", 1.0, "")
    assert resolve("acme:BarMember", [], register, issuer="Acme").product == "Calderon"


def test_a_member_naming_a_product_to_exclude_it_is_not_that_product():
    """`ProductsExcludingCalderon` is everything BUT Calderon.

    The trailing-run rule reads the name at the end, so a residual line was
    resolved to the one product it is defined to leave out - and at the top of
    CLAIM_STRENGTH, as a fact the filer tagged. It is the `CalderonAndNuVessa`
    defect inverted: there the figure was too large, here it is the complement
    of what it gets published as.

    Found by auditing the branch; filers do tag members of this shape.
    """
    known = ["Calderon", "NuVessa", "Nebulized Calderon"]
    for member in ("acme:ProductsExcludingCalderonMember",
                   "acme:RevenueExcludingNuVessaMember",
                   "acme:AllProductsExceptCalderonMember",
                   "acme:ProductsOtherThanNuVessaMember"):
        outcome = match(member, known)
        assert not outcome.resolved, f"{member} resolved to {outcome.product}"
        assert outcome.method == "excluding"

    # The marker has to sit before the trailing run. A category prefix that
    # merely ends in a product's name still resolves, and a member ending in a
    # marker names no product either way.
    assert match("acme:RespiratoryProductsCalderonMember", known).product == "Calderon"
    assert match("acme:NebulizedCalderonMember", known).product == "Nebulized Calderon"
    assert not match("acme:CalderonOtherMember", known).resolved
