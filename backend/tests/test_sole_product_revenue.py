"""Sole-product revenue path: empty peers and ProductMember.

Invented names: Calderon, NuVessa. No gold brands.
"""

from __future__ import annotations

from app.extraction.tagged import candidates_from_instance
from app.llm.client import apply_judge_hard_vetoes
from app.quality.candidate_filters import filter_revenue_candidates
from app.quality.fast_judge import try_deterministic_judgment
from tests.test_xbrl import REVENUE

SOLE_PRODUCT_INSTANCE = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:us-gaap="http://fasb.org/us-gaap/2024"
      xmlns:srt="http://fasb.org/srt/2024"
      xmlns:dei="http://xbrl.sec.gov/dei/2024"
      xmlns:acme="http://www.acmepharma.example/20240930">
  <context id="q1">
    <entity><identifier scheme="http://www.sec.gov/CIK">0001234567</identifier></entity>
    <period><startDate>2025-01-01</startDate><endDate>2025-03-31</endDate></period>
  </context>
  <context id="q1-prod">
    <entity><identifier scheme="http://www.sec.gov/CIK">0001234567</identifier>
      <segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">us-gaap:ProductMember</xbrldi:explicitMember></segment>
    </entity>
    <period><startDate>2025-01-01</startDate><endDate>2025-03-31</endDate></period>
  </context>
  <unit id="usd"><measure>iso4217:USD</measure></unit>
  <dei:EntityFilerCategory contextRef="q1">Large Accelerated Filer</dei:EntityFilerCategory>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="q1-prod" unitRef="usd" decimals="-5">212800000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
</xbrl>
"""


def test_empty_peers_keep_product_revenue_net():
    cand = {
        "period": "2025Q1",
        "period_type": "quarterly",
        "value_reported": 47.4,
        "revenue_scope": "Product family",
        "source_quote": "Product revenue, net $47.4 million",
    }
    kept, dropped = filter_revenue_candidates(
        [cand], product="Calderon", peer_names=[]
    )
    assert len(kept) == 1
    assert not dropped


def test_nonempty_peers_still_drop_product_revenue_net():
    cand = {
        "period": "2025Q1",
        "period_type": "quarterly",
        "value_reported": 47.4,
        "revenue_scope": "Product family",
        "source_quote": "Product revenue, net $47.4 million",
    }
    kept, dropped = filter_revenue_candidates(
        [cand], product="Calderon", peer_names=["NuVessa"]
    )
    assert not kept
    assert dropped[0]["_drop_reason"] == "product_scope_without_product_in_quote"


def test_sole_product_xbrl_product_member_publishes():
    found, notes = candidates_from_instance(
        SOLE_PRODUCT_INSTANCE,
        product="Calderon",
        issuer="Acme Pharma",
        register={},
        verdicts=REVENUE,
        peer_names=[],
    )
    assert [c["value_normalized_usd_millions"] for c in found] == [212.8]
    assert found[0]["member_resolved_by"] == "sole_product"
    assert not any("none resolving" in n for n in notes)


def test_nuvessa_beside_calderon_still_requires_brand_in_quote():
    """Peers on the page refuse a bare ProductMember as Calderon's alone."""
    found, _ = candidates_from_instance(
        SOLE_PRODUCT_INSTANCE,
        product="Calderon",
        issuer="Acme Pharma",
        register={},
        verdicts=REVENUE,
        peer_names=["NuVessa"],
    )
    assert found == []


def test_hard_veto_skips_product_missing_when_member_is_on_the_candidate():
    """The judge payload must carry xbrl_member; the citation string does not."""
    candidate = {
        "period": "2026Q1",
        "period_type": "quarterly",
        "value_reported": 129.881,
        "revenue_scope": "Product family",
        "extraction_method": "xbrl_fact",
        "xbrl_member": "us-gaap:ProductMember",
        "product_mentioned_in_quote": True,
    }
    quote = (
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax "
        "[2026-01-01..2026-03-31] ProductOrServiceAxis=ProductMember = 129881000"
    )
    out = apply_judge_hard_vetoes(
        product="Calderon",
        candidate=candidate,
        quote=quote,
        judgment={
            "support_classification": "supported",
            "validation_status": "auto_pass",
            "issues": [],
        },
        peer_names=[],
    )
    assert "hard_veto:product_missing_from_quote" not in out["issues"]
    assert out["validation_status"] == "auto_pass"


def test_deterministic_judge_keeps_tagged_fact_without_brand_in_citation():
    verdict = try_deterministic_judgment(
        product="Calderon",
        generic=None,
        candidate={
            "period": "2026Q1",
            "period_type": "quarterly",
            "value_reported": 129881000,
            "revenue_scope": "Product family",
            "extraction_method": "xbrl_fact",
            "xbrl_member": "us-gaap:ProductMember",
            "product_mentioned_in_quote": True,
        },
        quote=(
            "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax "
            "[2026-01-01..2026-03-31] ProductOrServiceAxis=ProductMember = 129881000"
        ),
        peer_names=[],
    )
    assert verdict is not None
    assert verdict["validation_status"] == "auto_pass"


def test_deterministic_judge_keeps_sole_product_generic_quote():
    verdict = try_deterministic_judgment(
        product="Calderon",
        generic=None,
        candidate={
            "period": "2025Q1",
            "period_type": "quarterly",
            "value_reported": 47.4,
            "revenue_scope": "Product family",
        },
        quote="Product revenue, net $47.4 million",
        peer_names=[],
    )
    assert verdict is not None
    assert verdict["validation_status"] == "auto_pass"
    assert "deterministic:product_quote_value_ok" in verdict["issues"]
