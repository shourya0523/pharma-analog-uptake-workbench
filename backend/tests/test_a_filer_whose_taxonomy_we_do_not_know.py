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
    assert product_facts(facts, names_a_product=_names("Calderon"), verdicts=REVENUE) == facts
    # And named rather than found, the same filing states nothing at all.
    assert product_facts(facts, verdicts=REVENUE) == []


def test_the_revenue_element_comes_from_what_the_filing_uses_it_for():
    """The filer breaks products out by sales far more than by anything else."""
    facts = [_fact("acme:CalderonMember", 1_941_000_000.0),
             _fact("acme:NuVessaMember", 845_000_000.0),
             _fact("acme:CalderonMember", 12_000_000.0,
                   element="ifrs-full:CostOfSales")]
    verdicts = {"ifrs-full:RevenueFromSaleOfGoods": True, "ifrs-full:CostOfSales": False}
    elements = revenue_elements(facts, names_a_product=_names("Calderon", "NuVessa"), verdicts=verdicts)
    assert elements == {"ifrs-full:RevenueFromSaleOfGoods"}


def test_a_measure_the_filer_invented_is_not_read_as_revenue():
    """An extension is a measure no taxonomy has a name for."""
    facts = [_fact("acme:CalderonMember", 9_999_000_000.0,
                   element=OWN_ELEMENT, standard=False),
             _fact("acme:CalderonMember", 1_941_000_000.0)]
    read = product_facts(facts, names_a_product=_names("Calderon"), verdicts=REVENUE)
    assert [f.value for f in read] == [1_941_000_000.0]


def test_a_percentage_on_the_product_axis_is_not_a_figure():
    facts = [_fact("acme:CalderonMember", 7.0, unit="pure"),
             _fact("acme:CalderonMember", 1_941_000_000.0)]
    read = product_facts(facts, names_a_product=_names("Calderon"), verdicts=REVENUE)
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
    read = product_facts(facts, names_a_product=_names("Calderon"), verdicts=REVENUE)
    assert [f.value for f in read] == [whole.value], (
        "publishing a region as the product understates it by the rest of the world"
    )


def test_a_member_naming_two_products_is_not_either_of_them():
    facts = [_fact("acme:CalderonAndNuVessaMember", 2_786_000_000.0)]
    both = _names("Calderon", "NuVessa", "CalderonAndNuVessa")
    # The resolver placing the joined member is the condition; two of a fact's
    # members naming products is what this refuses.
    crossed = [_fact("acme:CalderonMember", 1.0, extra={"acme:OtherAxis": "acme:NuVessaMember"})]
    assert product_facts(crossed, names_a_product=both, verdicts=REVENUE) == []
    assert len(product_facts(facts, names_a_product=both, verdicts=REVENUE)) == 1


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


CALCULATION = b"""<?xml version="1.0" encoding="UTF-8"?>
<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"
               xmlns:xlink="http://www.w3.org/1999/xlink">
  <link:calculationLink xlink:type="extended" xlink:role="http://acme.example/role/income">
    <link:loc xlink:type="locator" xlink:href="us-gaap-2024.xsd#us-gaap_GrossProfit" xlink:label="gp"/>
    <link:loc xlink:type="locator" xlink:href="us-gaap-2024.xsd#us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax" xlink:label="rev"/>
    <link:loc xlink:type="locator" xlink:href="us-gaap-2024.xsd#us-gaap_CostOfGoodsAndServicesSold" xlink:label="cogs"/>
    <link:loc xlink:type="locator" xlink:href="us-gaap-2024.xsd#us-gaap_CostsAndExpenses" xlink:label="costs"/>
    <link:loc xlink:type="locator" xlink:href="us-gaap-2024.xsd#us-gaap_ResearchAndDevelopmentExpense" xlink:label="rd"/>
    <link:loc xlink:type="locator" xlink:href="us-gaap-2024.xsd#us-gaap_OperatingIncomeLoss" xlink:label="op"/>
    <link:calculationArc xlink:type="arc" xlink:from="gp" xlink:to="rev" weight="1" order="1"/>
    <link:calculationArc xlink:type="arc" xlink:from="gp" xlink:to="cogs" weight="-1" order="2"/>
    <link:calculationArc xlink:type="arc" xlink:from="costs" xlink:to="rd" weight="1" order="1"/>
    <link:calculationArc xlink:type="arc" xlink:from="op" xlink:to="rev" weight="1" order="1"/>
    <link:calculationArc xlink:type="arc" xlink:from="op" xlink:to="costs" weight="-1" order="2"/>
  </link:calculationLink>
</link:linkbase>
"""


def test_the_linkbase_says_which_element_is_the_sale_and_which_the_cost_of_it():
    """The filer's own arithmetic, not a count and not a name.

    A filer reporting product profitability tags one cost against every
    revenue, so any rule that counts ties exactly. What the filing states
    outright is the sign each element carries in the total above it: revenue
    is added into gross profit and cost of goods is taken from it. An expense
    that is added into "costs and expenses" and then taken from operating
    income is a cost too, by the path rather than by its own arc.
    """
    from app.parsing.xbrl import parse_calculation

    calculation = parse_calculation(CALCULATION)
    assert calculation.settles("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax") is True
    assert calculation.settles("us-gaap:CostOfGoodsAndServicesSold") is False
    assert calculation.settles("us-gaap:ResearchAndDevelopmentExpense") is False
    assert calculation.settles("us-gaap:GrossProfit") is False, "a net is a result, not a base figure"
    assert calculation.settles("us-gaap:Revenues") is None, "an element it never mentions is left open"

    facts = []
    for member, revenue, cost in (("acme:CalderonMember", 225_300_000.0, 15_000_000.0),
                                  ("acme:NuVessaMember", 205_100_000.0, 13_200_000.0)):
        facts.append(_fact(member, revenue,
                           element="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"))
        facts.append(_fact(member, cost, element="us-gaap:CostOfGoodsAndServicesSold"))
    both = _names("Calderon", "NuVessa")
    assert revenue_elements(facts, names_a_product=both, calculation=calculation) == {
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    }
    assert [f.value for f in product_facts(facts, names_a_product=both, calculation=calculation, verdicts=REVENUE)] == [
        225_300_000.0, 205_100_000.0
    ]


def test_an_element_the_linkbase_leaves_open_is_asked_about_and_otherwise_left_out():
    """A royalty line tagged by product never enters the statements' arithmetic."""
    from app.parsing.xbrl import parse_calculation, unsettled_elements

    calculation = parse_calculation(CALCULATION)
    facts = [_fact("acme:CalderonMember", 744_000_000.0, element="ifrs-full:Revenue")]
    both = _names("Calderon")
    assert unsettled_elements(facts, names_a_product=both, calculation=calculation) == {"ifrs-full:Revenue"}
    assert revenue_elements(facts, names_a_product=both, calculation=calculation) == frozenset(), (
        "nothing settled it, so it is not read"
    )
    assert revenue_elements(facts, names_a_product=both, calculation=calculation,
                            verdicts={"ifrs-full:Revenue": True}) == {"ifrs-full:Revenue"}
    assert revenue_elements(facts, names_a_product=both, calculation=calculation,
                            verdicts={"ifrs-full:Revenue": False}) == frozenset()


def test_two_claims_of_equal_strength_that_disagree_are_both_held(monkeypatch):
    """A filer's own quarter and a later filing's comparative of it can differ.

    Both are tagged facts, so they rank identically, and reconciliation used
    to publish whichever sorted first while holding the other as the loser of
    a conflict it had not lost. Where the strongest claims in a group disagree
    by more than the precision their sources declared, nothing is published:
    the documents contradict each other and a person has to look.
    """
    import asyncio

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base, DatapointORM, DrugJobORM, ExtractionRunORM
    from app.domain.models import ValidationStatus, new_id
    from app.pipeline.orchestrator import PipelineOrchestrator

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon",
                     manufacturer="Acme Pharma", status="running", quality_flags=[])
    db.add_all([run, job]); db.commit()

    def point(value, accession):
        return DatapointORM(
            id=new_id(), job_id=job.id, period="2018Q1", period_type="quarterly",
            value_reported=value * 1e6, value_normalized_usd_millions=value,
            currency="USD", unit="units", revenue_scope="Product family",
            extraction_method="xbrl_fact", confidence_score=0.9,
            source_url="https://example.invalid/filing",
            validation_status=ValidationStatus.AUTO_PASS.value, issue_flags=[],
            source_quote=f"acme:Revenue = {value * 1e6:,.0f}",
            citation_json={"source_type": "quarterly_report", "accession": accession,
                           "rounding_uncertainty_usd_millions": 0.5},
        )

    own_quarter, later_comparative = point(52.2, "0001-18-000001"), point(18.0, "0001-19-000001")
    db.add_all([later_comparative, own_quarter]); db.commit()

    orch = PipelineOrchestrator(db, file_store=None)

    async def no_opinion(**_):
        return {"resolved": [], "conflicts": []}

    monkeypatch.setattr(orch.llm, "reconcile", no_opinion)
    asyncio.run(orch._reconcile_with_llm(job, [later_comparative, own_quarter]))

    statuses = {p.value_normalized_usd_millions: p.validation_status
                for p in db.query(DatapointORM).all()}
    assert statuses == {52.2: "needs_review", 18.0: "needs_review"}, statuses
    assert all("equal_strength_claims_disagree" in p.issue_flags
               for p in db.query(DatapointORM).all())


def test_two_claims_of_equal_strength_that_agree_within_precision_still_publish(monkeypatch):
    """The rule holds contradictions, not rounding."""
    import asyncio

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base, DatapointORM, DrugJobORM, ExtractionRunORM
    from app.domain.models import ValidationStatus, new_id
    from app.pipeline.orchestrator import PipelineOrchestrator

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon",
                     manufacturer="Acme Pharma", status="running", quality_flags=[])
    db.add_all([run, job]); db.commit()
    rows = []
    for value in (52.2, 52.0):
        rows.append(DatapointORM(
            id=new_id(), job_id=job.id, period="2018Q1", period_type="quarterly",
            value_reported=value * 1e6, value_normalized_usd_millions=value,
            currency="USD", unit="units", revenue_scope="Product family",
            extraction_method="xbrl_fact", confidence_score=0.9,
            source_url="https://example.invalid/filing",
            validation_status=ValidationStatus.AUTO_PASS.value, issue_flags=[],
            source_quote="x", citation_json={"source_type": "quarterly_report",
                                             "rounding_uncertainty_usd_millions": 0.5}))
    db.add_all(rows); db.commit()
    orch = PipelineOrchestrator(db, file_store=None)

    async def no_opinion(**_):
        return {"resolved": [], "conflicts": []}

    monkeypatch.setattr(orch.llm, "reconcile", no_opinion)
    asyncio.run(orch._reconcile_with_llm(job, rows))
    published = [p for p in db.query(DatapointORM).all() if p.validation_status == "auto_pass"]
    assert len(published) == 1, "one of two agreeing figures is published, the other is a duplicate"


def test_a_tagged_datapoint_says_which_document_it_came_from():
    """Reconciliation ranks by document before it ranks by claim.

    Every other producer writes `source_type` into the citation; the tagged
    reader did not, so its rows fell to the last document tier - below a
    number read off a page - and two tagged facts that contradicted each
    other were never the strongest claims in their group, so the rule that
    holds such a pair never saw them.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base, DrugJobORM, ExtractionRunORM
    from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
    from app.pipeline.orchestrator import PipelineOrchestrator

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon",
                     manufacturer="Acme Pharma", status="running", quality_flags=[])
    db.add_all([run, job]); db.commit()
    src = RetrievedSource(
        source_id=new_id(), source_type=SourceType.QUARTERLY_REPORT,
        url="https://example.invalid/acme-20260630_htm.xml", filing_type="6-K",
        accession_number="0001-26-000001", retrieval_status=RetrievalStatus.SUCCESS,
        metadata={"xbrl_instance": True},
    )
    candidate = {"period": "2026Q2", "period_type": "quarterly", "value_reported": 1_941_000_000.0,
                 "value_normalized_usd_millions": 1941.0, "currency": "USD", "unit": "units",
                 "revenue_scope": "Product family", "source_quote": "acme:Revenue = 1,941,000,000",
                 "extraction_method": "xbrl_fact", "confidence": 0.9}
    row = PipelineOrchestrator(db, file_store=None)._datapoint_from_candidate(job, src, candidate)
    assert row.citation_json["source_type"] == SourceType.QUARTERLY_REPORT.value
