"""Candidates built from tagged facts rather than from a table's layout."""

from __future__ import annotations

from app.extraction.members import (
    VERDICT_NO_CANDIDATE_MATCH,
    Resolution,
    fingerprint,
)
from app.extraction.tagged import candidates_from_instance
from tests.test_xbrl import INSTANCE

ISSUER = "United Therapeutics"
REGISTER = {
    (ISSUER, "uthr:NebulizedTyvasoMember"): Resolution(
        "uthr:NebulizedTyvasoMember", "Nebulized Tyvaso", "exact", 1.0),
    (ISSUER, "uthr:TyvasoDPIMember"): Resolution(
        "uthr:TyvasoDPIMember", "Tyvaso DPI", "exact", 1.0),
}


def test_a_tagged_fact_becomes_a_candidate_with_its_period_and_unit():
    found, notes = candidates_from_instance(
        INSTANCE, product="Nebulized Tyvaso", issuer=ISSUER, register=REGISTER)
    assert len(found) == 1
    candidate = found[0]
    assert candidate["period"] == "2024Q3"
    assert candidate["value_normalized_usd_millions"] == 159.2
    assert candidate["currency"] == "USD"
    assert candidate["extraction_method"] == "xbrl_fact"
    assert not notes


def test_the_citation_names_the_fact_rather_than_quoting_prose():
    """A tagged fact has no line to quote; what it has is an assertion to name."""
    found, _ = candidates_from_instance(
        INSTANCE, product="Nebulized Tyvaso", issuer=ISSUER, register=REGISTER)
    quote = found[0]["source_quote"]
    assert "2024-07-01..2024-09-30" in quote
    assert "ProductOrServiceAxis=NebulizedTyvasoMember" in quote
    assert found[0]["xbrl_context"] == "q3-neb"


def test_a_sibling_product_is_not_answered_with_this_one():
    """The defect the register exists to prevent, at the point it would occur."""
    found, _ = candidates_from_instance(
        INSTANCE, product="Tyvaso DPI", issuer=ISSUER, register=REGISTER)
    assert [c["value_normalized_usd_millions"] for c in found] == [274.6]


def test_a_regional_line_is_never_the_product_s_revenue():
    """The instance holds a North America figure for the same product and quarter."""
    found, _ = candidates_from_instance(
        INSTANCE, product="Nebulized Tyvaso", issuer=ISSUER, register=REGISTER)
    assert 150.0 not in [c["value_normalized_usd_millions"] for c in found]


def test_a_product_the_register_does_not_place_yields_nothing():
    found, notes = candidates_from_instance(
        INSTANCE, product="Remodulin", issuer=ISSUER, register=REGISTER)
    assert found == []
    assert any("none resolving to Remodulin" in note for note in notes)


def test_an_untagged_filing_says_so_rather_than_looking_like_a_failure():
    plain = b"""<?xml version="1.0"?><xbrl xmlns="http://www.xbrl.org/2003/instance">
      <context id="c"><period><startDate>2015-07-01</startDate><endDate>2015-09-30</endDate></period></context>
    </xbrl>"""
    found, notes = candidates_from_instance(plain, product="Tyvaso", register={})
    assert found == []
    assert any("no product-level facts tagged" in note for note in notes)


def test_the_year_to_date_column_is_not_a_quarter():
    """The instance holds a nine-month figure for the same product."""
    found, _ = candidates_from_instance(
        INSTANCE, product="Nebulized Tyvaso", issuer=ISSUER, register=REGISTER)
    assert all(c["period_type"] == "quarterly" for c in found)
    assert 450.0 not in [c["value_normalized_usd_millions"] for c in found]


def test_the_same_figure_tagged_twice_is_one_candidate():
    """Filers tag a figure in the revenue note and again in the segment table."""
    twice = INSTANCE.replace(
        b'<us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="q3-neb" unitRef="usd" decimals="-5">159200000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>',
        b'<us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="q3-neb" unitRef="usd" decimals="-5">159200000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>'
        b'<us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="q3-neb" unitRef="usd" decimals="-5">159200000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>',
    )
    found, _ = candidates_from_instance(twice, product="Nebulized Tyvaso", issuer=ISSUER, register=REGISTER)
    assert len(found) == 1


def test_the_same_member_means_different_things_to_different_filers():
    """us-gaap:ProductMember is one filer's only product and another's total.

    Keyed by member alone, whichever issuer was written to the register last
    would answer for all of them - which is how this shipped, silently, with
    every model-resolved mapping counting for nothing.
    """
    generic = INSTANCE.replace(b"uthr:NebulizedTyvasoMember", b"us-gaap:ProductMember")
    register = {
        ("Liquidia", "us-gaap:ProductMember"): Resolution(
            "us-gaap:ProductMember", "Yutrepia", "llm", 0.9, "sole marketed product"),
        ("Gilead", "us-gaap:ProductMember"): Resolution(
            "us-gaap:ProductMember", None, "llm", 1.0, "Gilead markets many products"),
    }
    for_liquidia, _ = candidates_from_instance(
        generic, product="Yutrepia", issuer="Liquidia", register=register)
    assert [c["value_normalized_usd_millions"] for c in for_liquidia] == [159.2]

    for_gilead, _ = candidates_from_instance(
        generic, product="Yutrepia", issuer="Gilead", register=register)
    assert for_gilead == []


# A Gilead instance tagging a product the register was built without. The
# member is unambiguous - the filer wrote the product's name into it - so the
# string rules place it the moment the product is on the list.
GILEAD_INSTANCE = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:us-gaap="http://fasb.org/us-gaap/2024"
      xmlns:srt="http://fasb.org/srt/2024"
      xmlns:dei="http://xbrl.sec.gov/dei/2024"
      xmlns:gild="http://www.gilead.com/20240930">
  <context id="q3">
    <entity><identifier scheme="http://www.sec.gov/CIK">0000882095</identifier></entity>
    <period><startDate>2024-07-01</startDate><endDate>2024-09-30</endDate></period>
  </context>
  <context id="q3-trod">
    <entity><identifier scheme="http://www.sec.gov/CIK">0000882095</identifier>
      <segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">gild:TrodelvyMember</xbrldi:explicitMember></segment>
    </entity>
    <period><startDate>2024-07-01</startDate><endDate>2024-09-30</endDate></period>
  </context>
  <unit id="usd"><measure>iso4217:USD</measure></unit>
  <dei:EntityFilerCategory contextRef="q3">Large Accelerated Filer</dei:EntityFilerCategory>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="q3-trod" unitRef="usd" decimals="-5">332300000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
</xbrl>
"""


def test_a_drug_uploaded_at_run_time_is_read_rather_than_vetoed():
    """The same rule at the read path, where the fact is either kept or lost.

    A register entry saying `gild:TrodelvyMember` named nothing in a list
    without Trodelvy must not answer for a run that uploads Trodelvy: the rules
    place that member outright, and the alternative is a fact Gilead tagged
    going unread.
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

    vetoed, _ = candidates_from_instance(
        GILEAD_INSTANCE, product="Trodelvy", issuer="Gilead",
        products=judged_against, register=register)
    assert vetoed == [], "the list the decision was made against is unchanged"

    learned: dict[tuple[str, str], Resolution] = {}
    found, _ = candidates_from_instance(
        GILEAD_INSTANCE, product="Trodelvy", issuer="Gilead",
        products=[*judged_against, "Trodelvy"], register=register, learned=learned)

    assert [c["value_normalized_usd_millions"] for c in found] == [332.3]
    assert learned[("Gilead", "gild:TrodelvyMember")].product == "Trodelvy"


def test_what_the_register_already_holds_is_not_relearned():
    """`learned` is for decisions with nowhere to go, not a copy of the store."""
    learned: dict[tuple[str, str], Resolution] = {}
    candidates_from_instance(
        INSTANCE, product="Nebulized Tyvaso", issuer=ISSUER,
        register=REGISTER, learned=learned)
    assert learned == {}
