"""The product axis under the name it had before the 2018 taxonomy.

`srt:ProductOrServiceAxis` is the modern spelling. Until the 2018 us-gaap
release moved the reporting axes into the srt namespace, the same axis was
`us-gaap:ProductOrServiceAxis`, and that is what a filing from before then
says. The parser knew only the modern name, so every pre-2018 instance read as
carrying no product facts - indistinguishable, from the outside, from a filing
that had tagged nothing.

The two instances quoted below are real. Gilead's 2013 Q3 10-Q
(0000882095-13-000046) tags twelve products across 150 occurrences of the axis;
United Therapeutics' 2016 Q3 10-Q (0001104659-16-152292) tags five across 100.
Both spell the axis `us-gaap:`, and both tag the value as
`us-gaap:SalesRevenueGoodsNet`, the pre-606 element name.
"""

from __future__ import annotations

from app.parsing.xbrl import PRODUCT_AXIS, Fact, parse_facts, product_facts

# What a bare fact list cannot say for itself: a real filing's linkbase settles
# which element is the sale, and these tests hand the answer over directly.
REVENUE = {
    "us-gaap:Revenues": True,
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax": True,
    "us-gaap:SalesRevenueGoodsNet": True,
    "us-gaap:CostOfGoodsAndServicesSold": False,
    "ifrs-full:Revenue": True,
    "ifrs-full:RevenueFromSaleOfGoods": True,
    "ifrs-full:CostOfSales": False,
}



def _instance(axis: str) -> bytes:
    """One product, one quarter, in whichever spelling of the axis is asked."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:us-gaap="http://fasb.org/us-gaap/2013-01-31"
      xmlns:gild="http://www.gilead.com/20130930">
  <context id="q3-tru">
    <entity><identifier scheme="http://www.sec.gov/CIK">0000882095</identifier>
      <segment><xbrldi:explicitMember dimension="{axis}">gild:TruvadaMember</xbrldi:explicitMember></segment>
    </entity>
    <period><startDate>2013-07-01</startDate><endDate>2013-09-30</endDate></period>
  </context>
  <unit id="usd"><measure>iso4217:USD</measure></unit>
  <us-gaap:SalesRevenueGoodsNet contextRef="q3-tru" unitRef="usd" decimals="-3"
    >823583000</us-gaap:SalesRevenueGoodsNet>
</xbrl>
""".encode()


def test_the_pre_2018_spelling_is_the_same_axis():
    """The defect: this instance read as having no product facts at all."""
    facts = product_facts(parse_facts(_instance("us-gaap:ProductOrServiceAxis")), verdicts=REVENUE)
    assert [f.product_member for f in facts] == ["gild:TruvadaMember"]
    assert facts[0].period == "2013Q3"
    assert facts[0].value == 823_583_000.0


def test_the_modern_spelling_is_unchanged():
    facts = product_facts(parse_facts(_instance(PRODUCT_AXIS)), verdicts=REVENUE)
    assert [f.product_member for f in facts] == ["gild:TruvadaMember"]


def test_an_unrelated_axis_still_names_no_product():
    """Widening the axis must not widen it to anything that mentions a product.

    `us-gaap:StatementClassOfStockAxis` is what United Therapeutics' 2010 Q3
    instance actually carries, and that filing has no product breakdown - the
    correct reading of it is still nothing.
    """
    assert product_facts(parse_facts(_instance("us-gaap:StatementClassOfStockAxis")), verdicts=REVENUE) == []


def test_a_fact_keyed_under_either_name_reports_its_product():
    """`Fact` is built directly by the bulk reader as well as by the parser."""
    for axis in (PRODUCT_AXIS, "us-gaap:ProductOrServiceAxis"):
        assert Fact("us-gaap:Revenues", 1.0, {axis: "gild:TruvadaMember"}).product_member == (
            "gild:TruvadaMember"
        )
    assert Fact("us-gaap:Revenues", 1.0, {}).product_member is None


def test_the_instance_reader_consults_the_register_through_resolve():
    """Not by indexing it, which is how it missed the identity fallback.

    `candidates_from_instance` did its own `register.get((issuer, member))`,
    so it saw only members spelled exactly as the register wrote them. The
    register is largely built from the bulk extracts, which spell a member
    without its prefix or its "Member" suffix, and the mismatch cost a product
    every sampled quarter on this path while the bulk path had all of them.
    """
    import inspect

    from app.extraction import tagged

    source = inspect.getsource(tagged.candidates_from_instance)
    assert "resolve(member, known, register, issuer=issuer)" in source
    assert "register.get(" not in source, (
        "an inline lookup here is the defect: it bypasses the identity keying "
        "in members.resolve"
    )


def test_a_bulk_spelled_register_entry_resolves_an_instance_member():
    """End to end on the real member, with a one-entry register."""
    from app.extraction.members import Resolution
    from app.extraction.tagged import candidates_from_instance

    raw = _instance("us-gaap:ProductOrServiceAxis").replace(
        b"gild:TruvadaMember", b"gild:CompleraEvipleraMember"
    )
    register = {("Gilead", "CompleraEviplera"): Resolution(
        "CompleraEviplera", "Complera", "llm", 0.9, "US and EU trade names"
    )}
    got, notes = candidates_from_instance(
        raw, product="Complera", issuer="Gilead",
        products=["Complera", "Truvada"], register=register,
    )
    assert [c["period"] for c in got] == ["2013Q3"], notes
    assert got[0]["value_normalized_usd_millions"] == 823.583
