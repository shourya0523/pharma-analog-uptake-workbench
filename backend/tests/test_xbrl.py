"""The XBRL first pass, against invented filings served from memory."""

from __future__ import annotations

import datetime as dt

from app.retrieval.xbrl import choose, first_pass, member_match, parse_facts, product_facts

INLINE = b"""<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:us-gaap="http://fasb.org/us-gaap/2023" xmlns:acme="http://acme.example/2023">
<body><ix:header><ix:resources>
 <xbrli:context id="q"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2023-04-01</xbrli:startDate><xbrli:endDate>2023-06-30</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="q_cal"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>
  <xbrli:segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">acme:RespiratoryProductsCalderonMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
  <xbrli:period><xbrli:startDate>2023-04-01</xbrli:startDate><xbrli:endDate>2023-06-30</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="q_cal_us"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>
  <xbrli:segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">acme:RespiratoryProductsCalderonMember</xbrldi:explicitMember>
  <xbrldi:explicitMember dimension="srt:StatementGeographicalAxis">country:US</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
  <xbrli:period><xbrli:startDate>2023-04-01</xbrli:startDate><xbrli:endDate>2023-06-30</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="h_cal"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>
  <xbrli:segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">acme:RespiratoryProductsCalderonMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
  <xbrli:period><xbrli:startDate>2023-01-01</xbrli:startDate><xbrli:endDate>2023-06-30</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="q_xr"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>
  <xbrli:segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">acme:CalderonXRMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
  <xbrli:period><xbrli:startDate>2023-04-01</xbrli:startDate><xbrli:endDate>2023-06-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:unit id="U_USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
</ix:resources></ix:header>
<table>
 <tr><td>Calderon</td><td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="q_cal" unitRef="U_USD" scale="6" decimals="-5" format="ixt:num-dot-decimal">1,234.5</ix:nonFraction></td></tr>
 <tr><td>Calderon US</td><td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="q_cal_us" unitRef="U_USD" scale="6" format="ixt:num-dot-decimal">900.0</ix:nonFraction></td></tr>
 <tr><td>Calderon six months</td><td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="h_cal" unitRef="U_USD" scale="6" format="ixt:num-dot-decimal">2,400.0</ix:nonFraction></td></tr>
 <tr><td>Calderon XR</td><td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="q_xr" unitRef="U_USD" scale="6" format="ixt:num-dot-decimal">77.0</ix:nonFraction></td></tr>
 <tr><td>Returns reserve</td><td><ix:nonFraction name="acme:CalderonReturnsReserve" contextRef="q_cal" unitRef="U_USD" scale="6" sign="-" format="ixt:num-dot-decimal">5</ix:nonFraction></td></tr>
 <tr><td>Total</td><td><ix:nonFraction name="us-gaap:Revenues" contextRef="q" unitRef="U_USD" scale="6" format="ixt:num-dot-decimal">9,999</ix:nonFraction></td></tr>
</table></body></html>"""


# The same filing with its second Calderon line replaced by another product,
# so the category-prefixed Calderon member is the brand's only line.
ONE_FORM = INLINE.replace(b"acme:CalderonXRMember", b"acme:NuVessaMember")


def test_member_match_follows_the_shape_rule():
    assert member_match("acme:RespiratoryProductsCalderonMember", "Calderon") == "exact"
    assert member_match("acme:CalderonXRMember", "Calderon") is None
    assert member_match("acme:CalderonXRMember", "Calderon XR") == "exact"
    assert member_match("acme:CalderonAndNuVessaMember", "Calderon") == "combined"
    assert member_match("acme:NuVessaAndCalderonMember", "Calderon") == "combined"
    assert member_match("acme:CALDERONAndNUVESSAMember", "Calderon") == "combined"
    assert member_match("acme:ProductsCALDERONMember", "Calderon") == "exact"
    # A brand with an inner capital reads as two CamelCase words, so a product
    # named for its second half resolves to it. The limit of reading names.
    assert member_match("acme:NuVessaMember", "Vessa") == "exact"
    assert member_match("acme:Calderon2Member", "Calderon") is None
    assert member_match("beta:NUVESSAMember", "NuVessa") == "exact"
    assert member_match("acme:CalderonXRMember", "Calderon / Calderon XR") == "exact"


def test_parse_facts_applies_scale_and_sign():
    contexts, facts = parse_facts(INLINE)
    by_ctx = {(f["concept"], f["context"]): f["value"] for f in facts}
    assert by_ctx[("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "q_cal")] == 1_234_500_000
    assert by_ctx[("acme:CalderonReturnsReserve", "q_cal")] == -5_000_000
    assert contexts["q_cal_us"]["members"]["srt:StatementGeographicalAxis"] == "country:US"


def test_choose_takes_the_quarter_on_the_product_alone():
    contexts, facts = parse_facts(INLINE)
    found = product_facts(contexts, facts, "Calderon")
    assert {f.value for f in found} == {1_234_500_000, 900_000_000, 2_400_000_000, -5_000_000}
    status, fact, candidates = choose(found, dt.date(2023, 6, 30))
    assert status == "found" and fact.value == 1_234_500_000
    assert all(c.concept.endswith("ExcludingAssessedTax") for c in candidates)


def _fake_sec(filer_documents):
    def get(url, **params):
        class Response:
            def __init__(self, content=b"", body=None):
                self.content, self._body = content, body

            def json(self):
                return self._body

        if url.endswith("index.json"):
            return Response(body={"directory": {"item": []}})
        if "search-index" in url:
            hits = [
                {"_id": f"{adsh}:{doc}", "_source": {"adsh": adsh, "ciks": [f"000000{cik}"], "display_names": [name],
                                                     "period_ending": "2023-06-30"}}
                for adsh, cik, name, doc, _ in filer_documents
            ]
            return Response(body={"hits": {"hits": hits}})
        for adsh, cik, name, doc, content in filer_documents:
            if url.endswith(doc):
                return Response(content)
        raise AssertionError(url)

    return get


def test_first_pass_picks_the_filer_that_tags_the_product():
    competitor = b"<html><body>We compete with Calderon.</body></html>"
    get = _fake_sec([
        ("0000000002-23-000001", 2, "BETA BIO (CIK 2)", "beta-20230630.htm", competitor),
        ("0000000001-23-000001", 1, "ACME PHARMA (CIK 1)", "acme-20230630.htm", ONE_FORM),
    ])
    answer = first_pass("Calderon", "Q2 2023", get=get)
    assert answer.status == "found"
    assert answer.value == 1_234_500_000 and answer.unit == "iso4217:USD"
    assert answer.filer.startswith("ACME") and answer.member == "acme:RespiratoryProductsCalderonMember"
    assert answer.filing_url.endswith("acme-20230630.htm")
    assert len(answer.filings_checked) == 2


def test_q4_and_unmentioned_products_are_their_own_statuses():
    assert first_pass("Calderon", "Q4 2023", get=_fake_sec([])).status == "not_a_quarter_in_10q"
    assert first_pass("Calderon", "Q2 2023", get=_fake_sec([])).status == "no_filing"


def _fact(value, **members):
    from app.retrieval.xbrl import Fact

    return Fact("us-gaap:Revenues", value, "iso4217:USD", "2023-04-01", "2023-06-30", members, "exact")


def test_the_whole_figure_is_the_root_of_the_split_not_a_part():
    product = {"srt:ProductOrServiceAxis": "acme:CalderonMember", "acme:PortfolioAxis": "acme:RespiratoryMember"}
    found = [
        _fact(700, **product, **{"srt:StatementGeographicalAxis": "country:US"}),
        _fact(300, **product, **{"srt:StatementGeographicalAxis": "us-gaap:NonUsMember"}),
        _fact(1000, **product),
    ]
    status, fact, candidates = choose(found, dt.date(2023, 6, 30))
    assert (status, fact.value) == ("found", 1000)
    from app.retrieval.xbrl import geography_parts

    assert geography_parts(fact, candidates) == {"country:US": 700, "us-gaap:NonUsMember": 300}

    status, fact, _ = choose(found[1:2], dt.date(2023, 6, 30))
    assert (status, fact) == ("dimensioned_only", None)


def test_a_royalty_on_the_product_is_not_its_sales():
    from app.retrieval.xbrl import Fact

    royalty = Fact("us-gaap:RoyaltyRevenue", 2.3e6, "iso4217:USD", "2017-01-01", "2017-03-31",
                   {"us-gaap:ProductOrServiceAxis": "beta:CalderonMember"}, "exact")
    assert choose([royalty], dt.date(2017, 3, 31))[0] == "no_revenue_fact"


def test_names_issuer_needs_every_distinctive_word():
    from app.retrieval.xbrl import names_issuer

    assert names_issuer("ACME PHARMA INC  (ACME)  (CIK 0000000001)", "Acme Pharma")
    assert names_issuer("ACME & CO  (CIK 1)", "Acme & Co.")
    assert not names_issuer("ACME & CO  (CIK 1)", "Acme KGaA")
    assert not names_issuer("BETA BIO (CIK 2)", "Acme")
    assert names_issuer("NUVESSA BIO INC  (NVB)  (CIK 3)", "NuVessa Bio")
    assert names_issuer("ACME PHARMA INC (CIK 1)", "Calderon Labs/Acme Pharma")


def test_a_named_issuer_that_does_not_tag_the_product_yields_other_filer():
    get = _fake_sec([("0000000001-23-000001", 1, "ACME PHARMA (CIK 1)", "acme-20230630.htm", ONE_FORM)])
    answer = first_pass("Calderon", "Q2 2023", "Beta Bio", get=get)
    assert answer.status == "other_filer" and answer.value is None
    assert answer.candidates[0]["value"] == 1_234_500_000
    assert first_pass("Calderon", "Q2 2023", "Acme Pharma", get=get).status == "found"


def test_us_plus_non_us_is_summed_into_the_whole_when_no_whole_is_tagged():
    product = {"srt:ProductOrServiceAxis": "acme:CalderonMember"}
    found = [
        _fact(700, **product, **{"srt:StatementGeographicalAxis": "country:US"}),
        _fact(300, **product, **{"srt:StatementGeographicalAxis": "us-gaap:NonUsMember"}),
    ]
    status, fact, _ = choose(found, dt.date(2023, 6, 30))
    assert (status, fact.value, fact.members) == ("found", 1000, product)

    europe = [found[0], _fact(200, **product, **{"srt:StatementGeographicalAxis": "srt:EuropeMember"})]
    assert choose(europe, dt.date(2023, 6, 30))[0] == "dimensioned_only"


def test_an_allocated_transaction_price_is_not_revenue():
    from app.retrieval.xbrl import is_revenue

    assert not is_revenue("acme:RevenuePerformanceObligationAllocatedTransactionPrice", "iso4217:USD")
    assert is_revenue("us-gaap:Revenues", "iso4217:USD")


def test_the_most_precise_fact_wins_over_a_rounded_one_in_prose():
    from app.retrieval.xbrl import Fact

    product = {"srt:ProductOrServiceAxis": "acme:CalderonMember"}
    prose = Fact("us-gaap:Revenues", 2.3e9, "iso4217:USD", "2023-04-01", "2023-06-30", product, "exact", -8)
    table = Fact("us-gaap:Revenues", 2.3146e9, "iso4217:USD", "2023-04-01", "2023-06-30", product, "exact", -5)
    status, fact, _ = choose([prose, table], dt.date(2023, 6, 30))
    assert fact.value == 2.3146e9 and fact.decimals == -5


def test_two_members_resolving_to_the_product_are_two_scopes_and_declined():
    from app.retrieval.xbrl import Fact

    def f(value, member):
        return Fact("us-gaap:Revenues", value, "iso4217:USD", "2023-04-01", "2023-06-30",
                    {"srt:ProductOrServiceAxis": member}, "exact", None, member)

    status, fact, _ = choose([f(500, "acme:NebulizedCalderonMember"), f(1200, "acme:CalderonMember")], dt.date(2023, 6, 30))
    assert (status, fact) == ("several_members", None)


def test_an_adjustment_for_changes_in_estimates_is_not_the_periods_sales():
    from app.retrieval.xbrl import is_revenue

    assert not is_revenue("acme:PriorPeriodAdjustmentsForProductSalesResultingFromChangesInEstimates", "iso4217:USD")


def test_product_facts_records_the_member_that_resolved():
    contexts, facts = parse_facts(INLINE)
    found = product_facts(contexts, facts, "Calderon")
    assert {f.resolved for f in found} == {"acme:RespiratoryProductsCalderonMember"}


def test_initials_name_the_filer_whose_words_they_abbreviate():
    from app.retrieval.xbrl import names_issuer

    assert names_issuer("ACME & NUVESSA INC (CIK 1)", "Calderon Labs/A&N")
    assert not names_issuer("ACME & BETA INC (CIK 1)", "A&N")
    assert not names_issuer("ACME INC (CIK 1)", "A")


def test_in_scope_swaps_in_the_tagged_us_part_or_declines():
    from app.retrieval.xbrl import XbrlAnswer, in_scope

    found = XbrlAnswer("found", "Calderon", "Q2 2023", value=1000, by_geography={"country:US": 700})
    assert (in_scope(found, "us").value, in_scope(found, "us").scope) == (700, "us")
    assert in_scope(found, "worldwide").value == 1000
    bare = XbrlAnswer("found", "Calderon", "Q2 2023", value=1000)
    assert (in_scope(bare, "us").status, in_scope(bare, "us").value, in_scope(bare, "us").geography) == (
        "found", 1000, "no_geographic_split")
    abroad = XbrlAnswer("found", "Calderon", "Q2 2023", value=1000, by_geography={"country:DE": 300})
    assert (in_scope(abroad, "us").status, in_scope(abroad, "us").value) == ("no_us_part", None)


def test_a_us_question_is_answered_from_a_us_part_tagged_without_a_whole():
    from app.retrieval.xbrl import Fact, XbrlAnswer, _parts_without_whole, in_scope

    member = "acme:CalderonMember"
    us = Fact("us-gaap:Revenues", 470, "iso4217:USD", "2026-01-01", "2026-03-31",
              {"srt:ProductOrServiceAxis": member, "srt:StatementGeographicalAxis": "country:US"}, "exact", None, member)
    parts = _parts_without_whole([us])
    assert parts == {"country:US": 470}
    answer = XbrlAnswer("dimensioned_only", "Calderon", "Q1 2026", by_geography=parts)
    assert (in_scope(answer, "us").status, in_scope(answer, "us").value) == ("found", 470)
    assert in_scope(answer, "worldwide").status == "dimensioned_only"


def test_a_combined_member_counts_as_found_and_says_so():
    from app.retrieval.xbrl import XbrlAnswer, in_scope

    combined = XbrlAnswer("dimensioned_only", "Calderon", "Q1 2026", match="combined",
                          by_geography={"country:US": 941})
    scoped = in_scope(combined, "us")
    assert (scoped.status, scoped.value, scoped.match) == ("found", 941, "combined")

    inline_doc = INLINE.replace(b"RespiratoryProductsCalderonMember", b"CalderonAndNuVessaMember")
    get = _fake_sec([("0000000001-23-000001", 1, "ACME PHARMA (CIK 1)", "acme-20230630.htm", inline_doc)])
    answer = first_pass("Calderon", "Q2 2023", "Acme Pharma", get=get)
    assert answer.status == "found" and answer.match == "combined" and answer.value == 1_234_500_000


def test_a_brands_second_half_is_not_a_product_the_filing_never_prints_alone():
    contexts, facts = parse_facts(INLINE.replace(b"RespiratoryProductsCalderonMember", b"NuVessaMember"))
    assert product_facts(contexts, facts, "Vessa", text="net sales of nuvessa rose") == []
    assert product_facts(contexts, facts, "Vessa", text="net sales of vessa rose")
    contexts, facts = parse_facts(INLINE)
    assert product_facts(contexts, facts, "Calderon", text="respiratory products: calderon")
    assert product_facts(contexts, facts, "Calderon", text=None)


# ---------------------------------------------------------------- family policy


def _member_fact(value, member, decimals=None, **extra):
    from app.retrieval.xbrl import Fact

    return Fact("us-gaap:Revenues", value, "iso4217:USD", "2023-04-01", "2023-06-30",
                {"srt:ProductOrServiceAxis": member, **extra}, "exact", decimals, member)


def test_family_resolves_a_brand_followed_by_a_further_word():
    assert member_match("acme:CalderonCreamMember", "Calderon") is None
    assert member_match("acme:CalderonCreamMember", "Calderon", family=True) == "family"
    assert member_match("acme:CALDERONFoamMember", "Calderon", family=True) == "family"
    assert member_match("acme:Calderon0.15Member", "Calderon", family=True) == "family"
    assert member_match("acme:CalderonXRMember", "Calderon", family=True) == "family"
    assert member_match("acme:CalderonMember", "Calderon", family=True) == "exact"
    assert member_match("acme:NebulizedCalderonMember", "Calderon", family=True) == "exact"
    assert member_match("acme:CalderonFoamAndNuVessaMember", "Calderon", family=True) == "combined"
    assert member_match("acme:CalderoneMember", "Calderon", family=True) is None


def test_family_sums_each_members_whole_and_brand_still_declines():
    found = [
        _member_fact(300, "acme:CalderonCreamMember"),
        _member_fact(500, "acme:CalderonFoamMember"),
        _member_fact(200, "acme:CalderonFoamMember", **{"srt:StatementGeographicalAxis": "country:US"}),
    ]
    status, fact, _ = choose(found, dt.date(2023, 6, 30), family=True)
    assert (status, fact.value, fact.match) == ("found", 800, "family")
    assert {p.resolved for p in fact.parts} == {"acme:CalderonCreamMember", "acme:CalderonFoamMember"}
    assert choose(found, dt.date(2023, 6, 30))[0] == "several_members"


def test_a_family_total_over_its_presentations_is_taken_alone():
    found = [_member_fact(1000, "acme:CalderonMember"), _member_fact(400, "acme:CalderonCreamMember"),
             _member_fact(600, "acme:CalderonFoamMember")]
    status, fact, _ = choose(found, dt.date(2023, 6, 30), family=True)
    assert (status, fact.value, fact.resolved, fact.parts) == ("found", 1000, "acme:CalderonMember", [])


def test_a_family_missing_a_members_whole_is_declined():
    found = [_member_fact(300, "acme:CalderonCreamMember"),
             _member_fact(200, "acme:CalderonFoamMember", **{"srt:StatementGeographicalAxis": "srt:EuropeMember"})]
    assert choose(found, dt.date(2023, 6, 30), family=True)[0] == "several_members"


def test_two_equal_members_are_not_read_as_a_total_and_its_part():
    found = [_member_fact(500, "acme:CalderonMember"), _member_fact(500, "acme:NebulizedCalderonMember")]
    assert choose(found, dt.date(2023, 6, 30), family=True)[0] == "several_members"


def test_family_geography_sums_the_places_every_member_splits_out():
    from app.retrieval.xbrl import family_geography

    us = {"srt:StatementGeographicalAxis": "country:US"}
    eu = {"srt:StatementGeographicalAxis": "srt:EuropeMember"}
    found = [
        _member_fact(300, "acme:CalderonCreamMember"), _member_fact(250, "acme:CalderonCreamMember", **us),
        _member_fact(500, "acme:CalderonFoamMember"), _member_fact(450, "acme:CalderonFoamMember", **us),
        _member_fact(50, "acme:CalderonFoamMember", **eu),
    ]
    _, fact, candidates = choose(found, dt.date(2023, 6, 30), family=True)
    assert family_geography(fact, candidates) == {"country:US": 700}


def test_first_pass_reads_the_family_only_when_asked():
    doc = INLINE.replace(b"RespiratoryProductsCalderonMember", b"CalderonCreamMember").replace(
        b"acme:CalderonXRMember", b"acme:CalderonFoamMember")
    get = _fake_sec([("0000000001-23-000001", 1, "ACME PHARMA (CIK 1)", "acme-20230630.htm", doc)])
    brand = first_pass("Calderon", "Q2 2023", "Acme Pharma", get=get)
    assert brand.status == "no_member"
    family = first_pass("Calderon", "Q2 2023", "Acme Pharma", get=get, family=True)
    assert (family.status, family.value, family.match) == ("found", 1_234_500_000 + 77_000_000, "family")
    assert family.member == "acme:CalderonCreamMember + acme:CalderonFoamMember"


def test_one_forms_member_does_not_answer_for_a_brand_whose_other_forms_are_tagged():
    two_forms = INLINE.replace(b"RespiratoryProductsCalderonMember", b"NebulizedCalderonMember").replace(
        b"acme:CalderonXRMember", b"acme:CalderonDPIMember")
    get = _fake_sec([("0000000001-23-000001", 1, "ACME PHARMA (CIK 1)", "acme-20230630.htm",
                      two_forms.replace(b"</table>", b"</table><p>Calderon</p>"))])
    assert first_pass("Calderon", "Q2 2023", "Acme Pharma", get=get).status == "several_members"


def test_revenue_is_a_word_of_the_concept_not_letters_inside_it():
    from app.retrieval.xbrl import is_revenue

    usd = "iso4217:USD"
    assert is_revenue("us-gaap:Revenues", usd) and is_revenue("us-gaap:SalesRevenueGoodsNet", usd)
    assert is_revenue("acme:NetSales", usd) and is_revenue("acme:netproductsales", usd)
    assert not is_revenue("us-gaap:ProceedsFromSaleAndMaturityOfAvailableForSaleSecurities", usd)


def test_a_slash_between_figures_is_part_of_the_name():
    from app.retrieval.xbrl import first_name, parse_facts

    assert first_name("Calderon 100/3.6") == "Calderon 100/3.6"
    assert first_name("Calderon/NuVessa") == first_name("Calderon / NuVessa") == "Calderon"
    assert parse_facts(b"") == ({}, [])
