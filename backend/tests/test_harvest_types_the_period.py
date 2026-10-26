"""Harvest types a forward-looking quote as guidance, and drops unknown loci.

Invented: Calderon. A sentence that expects a figure is not a booked quarter.
A money span with no period is not a candidate.
"""

from __future__ import annotations

from app.domain.models import PeriodType
from app.llm.harvest import harvest_amount_loci
from app.quality.checks import apply_auto_pass_gate


def test_a_forward_looking_sentence_is_guidance_not_a_quarter():
    text = (
        "The company expects Calderon net sales of $24 million "
        "in the fourth quarter."
    )
    loci = harvest_amount_loci(text, product="Calderon")
    assert loci, "the sentence names an amount and the product"
    assert all(loc.get("period_type") == PeriodType.GUIDANCE.value for loc in loci)
    candidate = {
        "id": "g1",
        "period": "2019Q4",
        "period_type": PeriodType.GUIDANCE.value,
        "value_reported": 24,
        "source_url": "https://ir.acme.example/outlook.htm",
        "source_quote": text,
        "revenue_scope": "Product family",
        "confidence_score": 0.95,
        "validation_status": "pending",
    }
    assert apply_auto_pass_gate(candidate, []) == "needs_review"


def test_a_locus_with_no_period_is_not_a_candidate():
    text = "Calderon net sales were $24 million."
    loci = harvest_amount_loci(text, product="Calderon")
    assert loci == []


def test_a_booked_quarter_still_harvests():
    text = "Calderon net sales were $12.3 million in Q1 2025."
    loci = harvest_amount_loci(text, product="Calderon")
    assert loci
    assert loci[0].get("period_type") is None
    assert loci[0]["period_hints"]
