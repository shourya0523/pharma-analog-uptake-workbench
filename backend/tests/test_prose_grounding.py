"""The value and the product in the same sentence.

A 573-character block of a release's headline bullets was passed as the
quote for a quarter: the product in one bullet, "$100 million" - a financing
facility - in another, and the judge holding the block against the figure
found both and passed it. The same shape passed company totals as product
revenue. A quote is a sentence; where it runs to several, the sentence that
carries the value has to carry the product, or the candidate cannot be
published without a person.

Two more things a sentence can say about an amount: that it is a change
("increased by $12.3 million") and that it is a cost ("cost of sales").
Neither is the quarter's revenue.

Invented names: Calderon.
"""

from __future__ import annotations

from app.extraction.prose import read_prose
from app.llm.client import apply_judge_hard_vetoes
from app.parsing.periods import PeriodContext
from app.quality.sentences import (
    sentence_carrying,
    sentences,
    states_a_change_not_a_level,
)


def _judged(quote: str, value: float, scope: str = "Product family") -> dict:
    return apply_judge_hard_vetoes(
        product="Calderon",
        candidate={"value_reported": value, "period_type": "quarterly", "revenue_scope": scope},
        quote=quote,
        judgment={"support_classification": "supported", "validation_status": "auto_pass", "issues": []},
    )


def test_bullets_are_sentences():
    block = ("• Calderon net product revenue grew 40% in the quarter\n"
             "• Entered into a $100 million financing facility\n"
             "• Cash of $312.4 million at quarter end")
    assert len(sentences(block)) == 3
    assert sentence_carrying(block, 100.0).startswith("Entered")


def test_the_value_and_the_product_must_share_a_sentence():
    block = ("• Calderon net product revenue grew 40% in the quarter\n"
             "• Entered into a $100 million financing facility")
    verdict = _judged(block, 100.0)
    assert verdict["validation_status"] == "needs_review"
    assert "hard_veto:value_and_product_in_different_sentences" in verdict["issues"]
    fine = _judged("Calderon net product revenue was $100.0 million for the quarter.", 100.0)
    assert fine["validation_status"] == "auto_pass"


def test_a_change_is_not_a_level():
    assert states_a_change_not_a_level(
        "Calderon revenue increased by $12.3 million compared with the prior year.", 12.3)
    assert not states_a_change_not_a_level(
        "Calderon revenue increased by $12.3 million to $61.2 million.", 61.2)
    assert states_a_change_not_a_level(
        "Calderon revenue increased by $12.3 million to $61.2 million.", 12.3)
    verdict = _judged("Calderon revenue increased by $12.3 million compared with the prior year.", 12.3)
    assert "hard_veto:change_not_level" in verdict["issues"]


def test_a_cost_of_sales_is_not_revenue():
    verdict = _judged("Cost of sales for Calderon was $30.0 million in the quarter.", 30.0)
    assert verdict["validation_status"] == "needs_review"
    assert "hard_veto:milestone_or_license_revenue" in verdict["issues"]


def test_the_prose_reader_does_not_produce_a_period_after_the_documents_own():
    text = ("Calderon net product sales were $20.0 million for the three months ended "
            "March 31, 2025. Calderon net product sales were $50.0 million for the three "
            "months ended March 31, 2027.")
    context = PeriodContext(months=3, month=3, year=2025)
    periods = {v.period for v in read_prose(text, product="Calderon", catalog=["Calderon"],
                                            period_context=context)}
    assert periods == {"2025Q1"}
    # Without a document period nothing is known about what lies ahead.
    assert "2027Q1" in {v.period for v in read_prose(text, product="Calderon", catalog=["Calderon"])}
