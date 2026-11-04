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


def test_percentage_columns_and_subrow_labels_stay_inside_the_row():
    wide = "Calderon 107 176% 135 158% 129 111% 148 90% 156 69% NuVessa 20 30"
    assert verify_answer(_answer(value=148), "Calderon", fetch=lambda url: wide).status == "verified"
    subrows = "Calderon us 1,042 599 73.9% - intl 566 356 58.9% ww 1,608 956 NuVessa 5"
    assert verify_answer(_answer(value=1608), "Calderon", fetch=lambda url: subrows).status == "verified"
    assert verify_answer(_answer(value=20), "Calderon", fetch=lambda url: wide).status == "value_not_near_product"


def test_prose_with_quarter_labels_and_years_reaches_its_figure():
    prose = ("the most important products in q1 were: calderon (allergic rhinitis treatment) us market launch "
             "is in full swing in 2009. sales in q1 reached sek 46 million, after sales of 12 in q4")
    assert verify_answer(_answer(value=46), "Calderon", fetch=lambda url: prose).status == "verified"


def _refusing(status):
    import httpx

    def fetch(url):
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("refused", request=request, response=httpx.Response(status, request=request))
    return fetch


def test_a_refusing_host_is_flagged_for_review_and_can_be_checked_on_an_archive_copy():
    refused = verify_answer(_answer(), "Calderon", fetch=_refusing(403))
    assert (refused.status, refused.needs_review) == ("refused_by_host", True)
    missing = verify_answer(_answer(), "Calderon", fetch=_refusing(404))
    assert (missing.status, missing.needs_review) == ("unfetchable", False)
    archived = verify_answer(_answer(), "Calderon", fetch=_refusing(403),
                             archive=lambda url: (TABLE, "https://web.archive.org/web/2020/" + url))
    assert archived.status == "verified_from_archive" and archived.archive_url.startswith("https://web.archive.org")
    nothing = verify_answer(_answer(), "Calderon", fetch=_refusing(403), archive=lambda url: None)
    assert nothing.status == "refused_by_host"
