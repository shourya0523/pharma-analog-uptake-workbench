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
