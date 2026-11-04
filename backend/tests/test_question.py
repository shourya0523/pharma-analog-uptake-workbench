"""Questions: their fields, their validation and how free text maps onto them."""

from __future__ import annotations

import pytest

from app.retrieval.question import Question


def test_defaults_are_the_brand_line_worldwide_as_the_issuer_books_it():
    q = Question("Calderon", "Q2 2023", "Acme Pharma")
    assert (q.scope, q.line, q.booked_by, q.combined_ok, q.unresolved) == ("worldwide", "brand", None, False, ())
    assert q.pipeline_kwargs() == {"issuer": "Acme Pharma", "scope": "worldwide", "family": False}
    assert q.review_reason() is None


def test_unknown_scope_or_line_is_refused():
    with pytest.raises(ValueError):
        Question("Calderon", "Q2 2023", scope="europe")
    with pytest.raises(ValueError):
        Question("Calderon", "Q2 2023", line="franchise")


def test_booked_by_names_the_filer_to_read_and_family_sets_the_policy():
    q = Question("Calderon", "Q2 2023", "Acme Pharma", line="family", booked_by="Beta Bio")
    assert q.filer == "Beta Bio" and q.family
    assert q.pipeline_kwargs() == {"issuer": "Beta Bio", "scope": "worldwide", "family": True}


def test_accepts_follows_combined_ok_and_line():
    brand = Question("Calderon", "Q2 2023")
    assert brand.accepts("exact") and brand.accepts("single_product") and brand.accepts(None)
    assert not brand.accepts("combined") and not brand.accepts("family")
    assert brand.with_(combined_ok=True).accepts("combined")
    assert brand.with_(line="family").accepts("family")


@pytest.mark.parametrize("text, scope, line, booked_by, combined_ok", [
    ("Worldwide", "worldwide", "brand", None, False),
    ("U.S. net product sales", "us", "brand", None, False),
    ("United States", "us", "brand", None, False),
    ("Product family", "worldwide", "family", None, False),
    ("Brand total", "worldwide", "family", None, False),
    ("Worldwide, all formulations", "worldwide", "family", None, False),
    ("Formulation-specific", "worldwide", "form", None, False),
    ("Beta-booked", "worldwide", "brand", "Beta", False),
    ("Worldwide as booked by Beta Bio", "worldwide", "brand", "Beta Bio", False),
    ("Company-booked", "worldwide", "brand", None, False),
    ("Worldwide, combined line", "worldwide", "brand", None, True),
])
def test_from_text_sets_the_fields_it_recognises(text, scope, line, booked_by, combined_ok):
    q = Question.from_text("Calderon", "Q2 2023", "Acme Pharma", text)
    assert (q.scope, q.line, q.booked_by, q.combined_ok) == (scope, line, booked_by, combined_ok), q.unresolved
    assert q.review_reason() is None, q.unresolved


@pytest.mark.parametrize("text", ["Ex-U.S.", "U.S. and Canada", "Non-US", "Europe", "Collaboration revenue",
                                  "Predominantly U.S."])
def test_from_text_keeps_what_it_cannot_express_for_review(text):
    q = Question.from_text("Calderon", "Q2 2023", "Acme Pharma", text)
    assert q.unresolved and q.review_reason()
    if "U.S. and" in text or text.startswith(("Ex", "Non")):
        assert q.scope == "worldwide"


def test_from_text_drops_the_product_and_issuer_names():
    q = Question.from_text("Calderon", "Q2 2023", "Acme Pharma", "Acme Pharma net sales of Calderon in the U.S.")
    assert (q.scope, q.unresolved) == ("us", ())
