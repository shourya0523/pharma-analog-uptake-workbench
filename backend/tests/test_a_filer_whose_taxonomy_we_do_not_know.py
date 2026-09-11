"""Reading a filer whose taxonomy names nothing the way this code does.

A domestic filer states products on ``srt:ProductOrServiceAxis`` under a
us-gaap revenue element, and both of those were written down here. Neither is
a property of XBRL: another taxonomy names the axis and the element something
else, and a filer may invent an axis of its own that no published taxonomy
contains at all. A reader that decides by name reads such a filing as untagged
and reports the absence as the filer's.

So neither is decided by name. The axis is whichever one carries a member the
resolver can place, the element is the one the filing itself uses most for
those facts, and a slice is a fact carrying a qualifier the filing uses to
break that same product down.

Invented names throughout, so nothing here passes because a real taxonomy or a
real brand is spelled in the code.
"""

from __future__ import annotations

from datetime import date

from app.extraction.tagged import candidates_from_instance
from app.parsing.xbrl import Fact, product_facts, revenue_elements

QUARTER = {"start": date(2026, 4, 1), "end": date(2026, 6, 30)}
# An axis no published taxonomy contains, under an element none of them names.
OWN_AXIS = "acme:PortfolioByBrandAxis"
OWN_ELEMENT = "made-up:RevenueFromSaleOfGoods"


def _fact(member, value, *, axis=OWN_AXIS, element="ifrs-full:RevenueFromSaleOfGoods",
          extra=None, unit="USD", standard=True, **period):
    members = {axis: member} | (extra or {})
    return Fact(element=element, value=value, members=members, unit=unit,
                standard_element=standard, decimals="-6", **(period or QUARTER))


def _names(*products):
    """The resolver, as the reader supplies it: does this member name a product?"""
    wanted = {p.replace(" ", "").casefold() for p in products}
    return lambda member: member.split(":")[-1].replace("Member", "").casefold() in wanted


def test_an_axis_this_code_cannot_name_is_still_the_product_axis():
    facts = [_fact("acme:CalderonMember", 1_941_000_000.0)]
    assert product_facts(facts, names_a_product=_names("Calderon")) == facts
    # And named rather than found, the same filing states nothing at all.
    assert product_facts(facts) == []


def test_the_revenue_element_comes_from_what_the_filing_uses_it_for():
    """The filer breaks products out by sales far more than by anything else."""
    facts = [_fact("acme:CalderonMember", 1_941_000_000.0),
             _fact("acme:NuVessaMember", 845_000_000.0),
             _fact("acme:CalderonMember", 12_000_000.0,
                   element="ifrs-full:CostOfSales")]
    elements = revenue_elements(facts, names_a_product=_names("Calderon", "NuVessa"))
    assert elements == {"ifrs-full:RevenueFromSaleOfGoods"}


def test_a_measure_the_filer_invented_is_not_read_as_revenue():
    """An extension is a measure no taxonomy has a name for."""
    facts = [_fact("acme:CalderonMember", 9_999_000_000.0,
                   element=OWN_ELEMENT, standard=False),
             _fact("acme:CalderonMember", 1_941_000_000.0)]
    read = product_facts(facts, names_a_product=_names("Calderon"))
    assert [f.value for f in read] == [1_941_000_000.0]


def test_a_percentage_on_the_product_axis_is_not_a_figure():
    facts = [_fact("acme:CalderonMember", 7.0, unit="pure"),
             _fact("acme:CalderonMember", 1_941_000_000.0)]
    read = product_facts(facts, names_a_product=_names("Calderon"))
    assert [f.value for f in read] == [1_941_000_000.0]


def test_a_region_is_a_slice_when_the_filing_splits_the_product_on_that_axis():
    """The axis doing the splitting is read from the filing, not from a list.

    ``acme:MarketsAxis`` is not a geography axis this code knows. What says it
    is one is that the same product and quarter carry several of its members
    while one fact carries none.
    """
    whole = _fact("acme:CalderonMember", 1_941_000_000.0)
    facts = [
        whole,
        _fact("acme:CalderonMember", 845_000_000.0, extra={"acme:MarketsAxis": "US"}),
        _fact("acme:CalderonMember", 512_000_000.0, extra={"acme:MarketsAxis": "EU"}),
        _fact("acme:CalderonMember", 584_000_000.0, extra={"acme:MarketsAxis": "RoW"}),
    ]
    read = product_facts(facts, names_a_product=_names("Calderon"))
    assert [f.value for f in read] == [whole.value], (
        "publishing a region as the product understates it by the rest of the world"
    )


def test_a_member_naming_two_products_is_not_either_of_them():
    facts = [_fact("acme:CalderonAndNuVessaMember", 2_786_000_000.0)]
    both = _names("Calderon", "NuVessa", "CalderonAndNuVessa")
    # The resolver placing the joined member is the condition; two of a fact's
    # members naming products is what this refuses.
    crossed = [_fact("acme:CalderonMember", 1.0, extra={"acme:OtherAxis": "acme:NuVessaMember"})]
    assert product_facts(crossed, names_a_product=both) == []
    assert len(product_facts(facts, names_a_product=both)) == 1


INSTANCE = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:ifrs-full="https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full"
      xmlns:acme="http://www.acme-pharma.example/20260630">
  <context id="q2">
    <entity><identifier scheme="http://www.sec.gov/CIK">0000000001</identifier>
      <segment><xbrldi:explicitMember dimension="acme:PortfolioByBrandAxis"
        >acme:CalderonMember</xbrldi:explicitMember></segment>
    </entity>
    <period><startDate>2026-04-01</startDate><endDate>2026-06-30</endDate></period>
  </context>
  <context id="q2-rebate">
    <entity><identifier scheme="http://www.sec.gov/CIK">0000000001</identifier>
      <segment><xbrldi:explicitMember dimension="acme:PortfolioByBrandAxis"
        >acme:ChargebacksMember</xbrldi:explicitMember></segment>
    </entity>
    <period><startDate>2026-04-01</startDate><endDate>2026-06-30</endDate></period>
  </context>
  <unit id="usd"><measure>iso4217:USD</measure></unit>
  <ifrs-full:RevenueFromSaleOfGoods contextRef="q2" unitRef="usd" decimals="-6"
    >1941000000</ifrs-full:RevenueFromSaleOfGoods>
  <ifrs-full:RevenueFromSaleOfGoods contextRef="q2-rebate" unitRef="usd" decimals="-6"
    sign="-">97000000</ifrs-full:RevenueFromSaleOfGoods>
</xbrl>
"""


def test_the_instance_reader_reads_such_a_filing_end_to_end():
    found, _notes = candidates_from_instance(
        INSTANCE, product="Calderon", issuer="Acme Pharma",
        products=["Calderon", "NuVessa"], register={},
    )
    assert [(c["period"], c["value_normalized_usd_millions"]) for c in found] == [
        ("2026Q2", 1941.0)
    ]


def test_a_deduction_tagged_beside_the_sales_it_reduces_is_not_revenue():
    """A chargeback sits on the product axis and is negative; revenue is not."""
    found, notes = candidates_from_instance(
        INSTANCE, product="Chargebacks", issuer="Acme Pharma",
        products=["Chargebacks"], register={},
    )
    assert found == []
    assert any("is not a revenue" in note for note in notes), notes


def test_the_tagged_reader_is_not_fed_the_document_ranking():
    """Instances reach it whether or not they rank as reading material.

    `prioritize_sources_for_revenue` ranks documents by how much prose and
    layout are worth reading, and it keeps only the source types in
    `REVENUE_PRIMARY_SOURCE_TYPES` once two of them exist. A retrieved XBRL
    instance is labelled by the report it belongs to, so it is never one of
    those - and handing the ranked subset to the tagged reader dropped every
    instance whenever the filer had two other filings in the window. The
    ranking bounds the expensive readers; this one costs a parse of a document
    already fetched.
    """
    import inspect

    from app.pipeline.orchestrator import PipelineOrchestrator

    source = inspect.getsource(PipelineOrchestrator._extract_revenue)
    call = "await self._tagged_revenue(job, "
    assert call + "sources)" in source, (
        "the tagged reader takes every retrieved source; `_tagged_revenue` "
        "already keeps only those carrying an instance"
    )
    assert call + "selected_sources)" not in source


def test_a_fact_cites_a_figure_that_can_be_found_in_its_own_citation():
    """A tagged fact has no prose, so its citation is the evidence.

    Every stage before publication checks that the value appears in the text
    cited for it. Rendered in the shortest form, anything from a million
    upwards becomes scientific notation - so the citation did not contain the
    figure it cites, the deterministic judge could not clear it, and the
    highest-trust claim the pipeline has went to a reviewer instead of being
    published.
    """
    from app.quality.checks import quote_contains_value
    from app.quality.fast_judge import try_deterministic_judgment

    fact = Fact(element="ifrs-full:Revenue", value=1_941_000_000.0, unit="USD",
                decimals="-6", members={OWN_AXIS: "acme:CalderonMember"}, **QUARTER)
    assert "e+" not in fact.citation, fact.citation
    assert quote_contains_value(fact.citation, fact.value)

    judgment = try_deterministic_judgment(
        product="Calderon", generic=None, quote=fact.citation,
        candidate={"period_type": "quarterly", "revenue_scope": "Product family",
                   "value_reported": fact.value},
    )
    assert judgment and judgment["validation_status"] == "auto_pass", judgment

    # A figure with real decimals keeps them rather than being rounded away.
    fraction = Fact(element="ifrs-full:Revenue", value=1_941_500_000.25, unit="USD",
                    members={OWN_AXIS: "acme:CalderonMember"}, **QUARTER)
    assert quote_contains_value(fraction.citation, fraction.value), fraction.citation


def test_a_cost_tagged_against_every_sale_is_not_read_as_the_sale():
    """Counting alone ties, and the shape that ties it is ordinary.

    A filer reporting product profitability tags one cost against every
    revenue, so both elements carry the same number of facts. What separates
    them is that a cost is a part of what it is taken from: for the same
    product and period it is the smaller of the two, every time.
    """
    facts = []
    for member, revenue, cost in (("acme:CalderonMember", 225_300_000.0, 15_000_000.0),
                                  ("acme:NuVessaMember", 205_100_000.0, 13_200_000.0)):
        facts.append(_fact(member, revenue,
                           element="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"))
        facts.append(_fact(member, cost, element="us-gaap:CostOfGoodsAndServicesSold"))
    both = _names("Calderon", "NuVessa")
    assert revenue_elements(facts, names_a_product=both) == {
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    }
    assert [f.value for f in product_facts(facts, names_a_product=both)] == [
        225_300_000.0, 205_100_000.0
    ]


def test_two_elements_a_filer_states_revenue_under_both_survive():
    """A real tie is not broken; dropping either would lose a product."""
    facts = [
        _fact("acme:CalderonMember", 225_300_000.0, element="us-gaap:Revenues"),
        _fact("acme:NuVessaMember", 205_100_000.0,
              element="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"),
    ]
    both = _names("Calderon", "NuVessa")
    assert revenue_elements(facts, names_a_product=both) == {
        "us-gaap:Revenues",
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    }, "neither element appears beside the other, so neither can be shown to be a part of it"
