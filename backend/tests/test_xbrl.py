"""Reading a filer's own tagged facts, rather than a table's layout.

The fixture is the shape United Therapeutics files: one product split into two
formulations and their total, each on the product axis, with the quarter and the
year to date as separate contexts and one regional figure carrying a geography.
"""

from __future__ import annotations

from datetime import date

from app.parsing.xbrl import (
    PRODUCT_AXIS,
    Fact,
    filer_category,
    parse_facts,
    product_facts,
)

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
    facts = product_facts(parse_facts(INSTANCE), verdicts=REVENUE)
    assert {(f.product_member.split(":")[-1], f.period, f.value) for f in facts} == {
        ("NebulizedTyvasoMember", "2024Q3", 159_200_000.0),
        ("TyvasoDPIMember", "2024Q3", 274_600_000.0),
    }


def test_a_regional_line_is_excluded_unless_it_is_asked_for():
    everywhere = product_facts(parse_facts(INSTANCE), worldwide_only=False, verdicts=REVENUE)
    assert len(everywhere) == 3


def test_cash_is_not_revenue_and_carries_no_product():
    assert all("Cash" not in f.element for f in product_facts(parse_facts(INSTANCE), verdicts=REVENUE))


def test_the_filing_states_which_filer_it_is():
    """What decides when tagging became mandatory for this issuer."""
    assert filer_category(INSTANCE) == "Large Accelerated Filer"


def test_a_fact_can_be_cited_precisely_enough_to_check():
    fact = next(f for f in product_facts(parse_facts(INSTANCE), verdicts=REVENUE)
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
    assert product_facts(parse_facts(plain), verdicts=REVENUE) == []
    assert filer_category(plain) is None


def test_a_fiscal_year_ending_in_january_is_the_year_it_covers():
    """A 52/53-week filer closes its year days into the next one.

    Reading the label off the end date files fiscal 2022 as 2023, on top of
    the real 2023, with a citation apiece saying both are tagged.
    """
    fiscal_2022 = Fact(
        element="us-gaap:Revenues", value=1.0,
        start=date(2022, 1, 3), end=date(2023, 1, 1),
    )
    assert fiscal_2022.period == "2022"
    fourth_quarter = Fact(
        element="us-gaap:Revenues", value=1.0,
        start=date(2022, 10, 3), end=date(2023, 1, 1),
    )
    assert fourth_quarter.period == "2022Q4"


def test_a_gross_profit_is_not_a_revenue():
    """The element has to be a revenue, not merely contain the word."""
    facts = [
        Fact(element="uthr:GrossProfitExcludingOtherRevenue", value=113_700_000.0,
             members={PRODUCT_AXIS: "uthr:RemodulinMember"},
             start=date(2020, 4, 1), end=date(2020, 6, 30)),
        Fact(element="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
             value=119_000_000.0, members={PRODUCT_AXIS: "uthr:RemodulinMember"},
             start=date(2020, 4, 1), end=date(2020, 6, 30)),
    ]
    assert [f.value for f in product_facts(facts, verdicts=REVENUE)] == [119_000_000.0]


def test_the_total_is_the_least_qualified_statement_about_a_product():
    """An arrangement's share of a product is a part of it, not another view.

    Which axes subset a figure is not knowable from a list of axis names, so
    the total is whichever statement the filer qualified least.
    """
    plain = Fact(element="us-gaap:Revenues", value=41_300_000.0,
                 members={PRODUCT_AXIS: "uthr:AdcircaMember"},
                 start=date(2022, 1, 1), end=date(2022, 12, 31))
    lilly = Fact(element="us-gaap:Revenues", value=1_000_000.0,
                 members={PRODUCT_AXIS: "uthr:AdcircaMember",
                          "us-gaap:TypeOfArrangementAxis": "uthr:EliLillyAndCompanyMember"},
                 start=date(2022, 1, 1), end=date(2022, 12, 31))
    assert [f.value for f in product_facts([plain, lilly], verdicts=REVENUE)] == [41_300_000.0]


def test_a_segment_qualifier_takes_nothing_away():
    """J&J tags every product under its segment; there is no plainer fact."""
    segmented = Fact(element="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                     value=10_858_000_000.0,
                     members={PRODUCT_AXIS: "jnj:StelaraMember",
                              "us-gaap:StatementBusinessSegmentsAxis": "jnj:InnovativeMedicineMember"},
                     start=date(2023, 1, 2), end=date(2023, 12, 31))
    assert [f.value for f in product_facts([segmented], verdicts=REVENUE)] == [10_858_000_000.0]


def test_a_forecast_is_not_a_report():
    forecast = Fact(element="us-gaap:Revenues", value=5.0,
                    members={PRODUCT_AXIS: "mrk:KoselugoMember",
                             "srt:StatementScenarioAxis": "srt:ScenarioForecastMember"},
                    start=date(2026, 1, 1), end=date(2026, 3, 31))
    assert product_facts([forecast], verdicts=REVENUE) == []
