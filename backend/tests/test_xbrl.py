"""Reading a filer's own tagged facts, rather than a table's layout.

The fixture is the shape United Therapeutics files: one product split into two
formulations and their total, each on the product axis, with the quarter and the
year to date as separate contexts and one regional figure carrying a geography.
"""

from __future__ import annotations

from datetime import date

from app.parsing.xbrl import Fact, filer_category, parse_facts, product_facts

INSTANCE = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:us-gaap="http://fasb.org/us-gaap/2024"
      xmlns:srt="http://fasb.org/srt/2024"
      xmlns:dei="http://xbrl.sec.gov/dei/2024"
      xmlns:uthr="http://www.unither.com/20240930">
  <context id="q3">
    <entity><identifier scheme="http://www.sec.gov/CIK">0001082554</identifier></entity>
    <period><startDate>2024-07-01</startDate><endDate>2024-09-30</endDate></period>
  </context>
  <context id="q3-neb">
    <entity><identifier scheme="http://www.sec.gov/CIK">0001082554</identifier>
      <segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">uthr:NebulizedTyvasoMember</xbrldi:explicitMember></segment>
    </entity>
    <period><startDate>2024-07-01</startDate><endDate>2024-09-30</endDate></period>
  </context>
  <context id="q3-dpi">
    <entity><identifier scheme="http://www.sec.gov/CIK">0001082554</identifier>
      <segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">uthr:TyvasoDPIMember</xbrldi:explicitMember></segment>
    </entity>
    <period><startDate>2024-07-01</startDate><endDate>2024-09-30</endDate></period>
  </context>
  <context id="q3-neb-us">
    <entity><identifier scheme="http://www.sec.gov/CIK">0001082554</identifier>
      <segment>
        <xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">uthr:NebulizedTyvasoMember</xbrldi:explicitMember>
        <xbrldi:explicitMember dimension="srt:StatementGeographicalAxis">srt:NorthAmericaMember</xbrldi:explicitMember>
      </segment>
    </entity>
    <period><startDate>2024-07-01</startDate><endDate>2024-09-30</endDate></period>
  </context>
  <context id="ytd-neb">
    <entity><identifier scheme="http://www.sec.gov/CIK">0001082554</identifier>
      <segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">uthr:NebulizedTyvasoMember</xbrldi:explicitMember></segment>
    </entity>
    <period><startDate>2024-01-01</startDate><endDate>2024-09-30</endDate></period>
  </context>
  <unit id="usd"><measure>iso4217:USD</measure></unit>
  <dei:EntityFilerCategory contextRef="q3">Large Accelerated Filer</dei:EntityFilerCategory>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="q3-neb" unitRef="usd" decimals="-5">159200000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="q3-dpi" unitRef="usd" decimals="-5">274600000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="q3-neb-us" unitRef="usd" decimals="-5">150000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="ytd-neb" unitRef="usd" decimals="-5">450000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:CashAndCashEquivalentsAtCarryingValue contextRef="q3" unitRef="usd" decimals="-5">900000000</us-gaap:CashAndCashEquivalentsAtCarryingValue>
</xbrl>
"""


def test_a_fact_keeps_what_the_filer_said_about_it():
    facts = {f.context_id: f for f in parse_facts(INSTANCE)
             if "Revenue" in f.element}
    neb = facts["q3-neb"]
    assert neb.value == 159_200_000
    assert neb.members["srt:ProductOrServiceAxis"] == "uthr:NebulizedTyvasoMember"
    assert neb.start == date(2024, 7, 1) and neb.end == date(2024, 9, 30)
    assert neb.unit == "USD"


def test_the_period_comes_from_the_context_not_from_a_heading():
    """The thing the table reader recovers from geometry is simply stated."""
    facts = {f.context_id: f for f in parse_facts(INSTANCE)}
    assert facts["q3-neb"].period == "2024Q3"
    assert facts["q3-neb"].months == 3
    assert facts["ytd-neb"].months == 9
    assert facts["ytd-neb"].period is None, "nine months is not a quarter or a year"


def test_worldwide_is_the_absence_of_a_geography():
    facts = {f.context_id: f for f in parse_facts(INSTANCE)}
    assert facts["q3-neb"].is_worldwide
    assert not facts["q3-neb-us"].is_worldwide


def test_product_facts_are_the_quarters_on_the_product_axis():
    facts = product_facts(parse_facts(INSTANCE))
    assert {(f.product_member.split(":")[-1], f.period, f.value) for f in facts} == {
        ("NebulizedTyvasoMember", "2024Q3", 159_200_000.0),
        ("TyvasoDPIMember", "2024Q3", 274_600_000.0),
    }


def test_a_regional_line_is_excluded_unless_it_is_asked_for():
    everywhere = product_facts(parse_facts(INSTANCE), worldwide_only=False)
    assert len(everywhere) == 3


def test_cash_is_not_revenue_and_carries_no_product():
    assert all("Cash" not in f.element for f in product_facts(parse_facts(INSTANCE)))


def test_the_filing_states_which_filer_it_is():
    """What decides when tagging became mandatory for this issuer."""
    assert filer_category(INSTANCE) == "Large Accelerated Filer"


def test_a_fact_can_be_cited_precisely_enough_to_check():
    fact = next(f for f in product_facts(parse_facts(INSTANCE))
                if "Nebulized" in (f.product_member or ""))
    citation = fact.citation
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" in citation
    assert "2024-07-01..2024-09-30" in citation
    assert "ProductOrServiceAxis=NebulizedTyvasoMember" in citation


def test_an_untagged_filing_states_nothing_rather_than_guessing():
    plain = b"""<?xml version="1.0"?><xbrl xmlns="http://www.xbrl.org/2003/instance">
      <context id="c"><period><startDate>2015-07-01</startDate><endDate>2015-09-30</endDate></period></context>
      <unit id="usd"><measure>iso4217:USD</measure></unit>
    </xbrl>"""
    assert product_facts(parse_facts(plain)) == []
    assert filer_category(plain) is None
