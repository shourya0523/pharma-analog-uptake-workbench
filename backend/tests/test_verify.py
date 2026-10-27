"""The source check, against documents given as text."""

from __future__ import annotations

from app.retrieval.verify import verify_answer

TABLE = """
Prioritized Brands  Calderon  1,234.5  —  1,100.0  Acme Allergy  17  —  17
Established Brands  NuVessa  (45)  126  Total revenues  9,999
"""


def _answer(**overrides):
    answer = {
        "value": 1234.5,
        "source_url": "https://example.com/acme-10q.htm",
        "source_quote": "Prioritized Brands ... Calderon 1,234.5",
    }
    answer.update(overrides)
    return answer


def test_value_printed_after_the_product_is_verified():
    result = verify_answer(_answer(), "Calderon", fetch=lambda url: TABLE)
    assert result.status == "verified"
    assert result.quote_segments_found == 2 and result.quote_segments == 2
    assert "1,234.5" in result.near_text


def test_value_printed_only_elsewhere_is_not_near_the_product():
    result = verify_answer(_answer(value=9999), "Calderon", fetch=lambda url: TABLE)
    assert result.status == "value_not_near_product"
    assert result.value_anywhere


def test_value_missing_from_the_document():
    assert verify_answer(_answer(value=31), "Calderon", fetch=lambda url: TABLE).status == "value_absent"


def test_accounting_parentheses_read_as_the_number():
    assert verify_answer(_answer(value=45), "NuVessa", fetch=lambda url: TABLE).status == "verified"


def test_no_value_and_unfetchable_are_their_own_verdicts():
    assert verify_answer(_answer(value=None), "Calderon").status == "no_value"

    def broken(url):
        raise OSError("connection refused")

    result = verify_answer(_answer(), "Calderon", fetch=broken)
    assert result.status == "unfetchable" and "connection refused" in result.error
