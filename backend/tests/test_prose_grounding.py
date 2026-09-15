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


# A sentence can name our product and still not state its figure. Three shapes,
# all read from one quarter of one release, all published as the product's own.
# Invented names: Calderon, NuVessa, Acme Pharma.

def test_a_sibling_the_catalogue_never_heard_of_still_makes_a_sentence_ambiguous():
    """The one-product rule was right and blind.

    It asked a catalogue which products a sentence names, so a sibling the
    catalogue does not hold was not a second product: the sentence read as
    unambiguous and its figure was published for whichever product was asked.
    The caller's own products answer for the run's drugs, and the filer's
    trademark answers for the rest.
    """
    both = ("In the second quarter of 2024, Acme Pharma delivered $242.0 million in net "
            "product sales, highlighted by growth in Calderon net sales and growth in "
            "NuVessa net sales.")
    blind = read_prose(both, product="Calderon", catalog=())
    assert [v.value_as_reported for v in blind] == [242.0], "the defect, with nothing to see it"

    told = read_prose(both, product="Calderon", catalog=(), products=["Calderon", "NuVessa"])
    assert told == [], "a product the run was asked about is a product"


def test_a_brand_the_filer_marks_is_a_product_whatever_the_catalogue_holds():
    marked = ("Net product sales from Calderon ® and NuVessa ® were $242.0 million "
              "for the three months ended June 30, 2024.")
    assert read_prose(marked, product="Calderon", catalog=()) == []
    # Our own mark is not a second product.
    ours = ("Calderon ® net sales were approximately $34.6 million for the three months "
            "ended June 30, 2023.")
    assert [v.value_as_reported for v in read_prose(ours, product="Calderon", catalog=())] == [34.6]


def test_a_total_that_our_product_is_part_of_is_not_our_products_figure():
    """The hardest of the three: the sentence names one product we track, marks
    only that one, and still states a figure covering two. What it does say is
    the order - the aggregate heads the sentence and the product sits inside
    the thing being aggregated."""
    total = ("Total revenues, comprised of net product sales from Calderon ® and NuVessa, "
             "were $242.0 million for the three months ended June 30, 2024.")
    assert read_prose(total, product="Calderon", catalog=()) == []

    # A total *of* our product is still our product's figure, and the order is
    # what tells them apart.
    ours = ("Calderon total net sales were $34.6 million for the three months ended "
            "June 30, 2023.")
    assert [v.value_as_reported for v in read_prose(ours, product="Calderon", catalog=())] == [34.6]
