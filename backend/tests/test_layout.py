"""The layout reader, against invented documents and a scripted model."""

from __future__ import annotations

import base64
import datetime as dt

import pytest
from sec_fixtures import Response

from app.retrieval import layout
from app.retrieval.earnings import names_quarter
from app.retrieval.gemini_search import QuotaExceeded
from app.retrieval.layout import read_layout, render_html

SEC = "https://www.sec.gov/Archives/edgar/data/1/000000000125000001/"

# A results exhibit whose columns are headed by a numeric date, with a
# full-year table below it printing the same row.
DATED = (b"<html><body><p>The following is a summary of ACME PHARMA's sales for selected products.</p>"
         b"<table><tr><td>Quarter Ended 12/31/24</td><td></td><td></td><td></td></tr>"
         b"<tr><td>(dollars in millions)</td><td>U.S. Sales</td><td>Rest of World</td><td>Global Sales</td></tr>"
         b"<tr><td>Calderon</td><td>$ 527</td><td>$ 427</td><td>$ 954</td></tr>"
         b"<tr><td>NuVessa</td><td>$ 435</td><td>$ 26</td><td>$ 461</td></tr></table>"
         b"<p>The following is a summary of sales for the full year.</p>"
         b"<table><tr><td>Twelve Months Ended 12/31/24</td><td></td><td></td><td></td></tr>"
         b"<tr><td>(dollars in millions)</td><td>U.S. Sales</td><td>Rest of World</td><td>Global Sales</td></tr>"
         b"<tr><td>Calderon</td><td>$ 1,651</td><td>$ 1,413</td><td>$ 3,064</td></tr></table></body></html>")
DATED_ANSWER = {"found": True, "value": 954, "unit": "millions", "currency": "USD", "kind": "product_sales",
                "row_label": "Calderon", "column_headers": ["Quarter Ended 12/31/24", "Global Sales"],
                "filer_total_product_revenue": False}

# A single-product filer's annual report: the quarterly table's row does not
# name the product.
ANNUAL = (b"<html><body><p>Calderon is our only approved product.</p>"
          b"<p>15. Quarterly Results (Unaudited)</p>"
          b"<table><tr><td></td><td>Three Months Ended</td><td></td><td></td><td></td></tr>"
          b"<tr><td>(in thousands)</td><td>March 31, 2024</td><td>June 30, 2024</td><td>September 30, 2024</td>"
          b"<td>December 31, 2024</td></tr>"
          b"<tr><td>Product revenue, net</td><td>$ 341</td><td>$ 796</td><td>$ 978</td><td>$ 1,460</td></tr>"
          b"<tr><td>License and collaboration revenue</td><td>-</td><td>-</td><td>2,000</td><td>-</td></tr>"
          b"</table></body></html>")
ANNUAL_ANSWER = {"found": True, "value": 1460, "unit": "thousands", "currency": "USD", "kind": "product_sales",
                 "row_label": "Product revenue, net", "column_headers": ["Three Months Ended", "December 31, 2024"],
                 "filer_total_product_revenue": True}

# A fixed-width text filing, as filed before HTML.
TEXT = (b"ACME PHARMA INC\nCONSOLIDATED STATEMENTS OF INCOME\n(in thousands)\n\n"
        b"                                    Three Months Ended\n"
        b"                                         March 31,\n"
        b"                                     2024          2023\n"
        b"                                   --------      --------\n"
        b"Revenues:\n"
        b"  Product                          $219,997      $174,596\n"
        b"  Royalties                          32,100        28,400\n")
TEXT_ANSWER = {"found": True, "value": 219997, "unit": "thousands", "currency": "USD", "kind": "product_sales",
               "row_label": "Product", "column_headers": ["Three Months Ended", "March 31,", "2024"],
               "filer_total_product_revenue": True}


def _get(documents):
    def get(url, **params):
        for name, content in documents.items():
            if url.endswith(name):
                return Response(content)
        raise AssertionError(url)
    return get


def _ask(answer, seen=None):
    def ask(contents):
        if seen is not None:
            seen.append(contents)
        return answer
    return ask


def _read(document, answer, product="Calderon", period="2024Q4", name="doc.htm", **kw):
    return read_layout(SEC + name, product, period, get=_get({name: document}), ask=_ask(answer), **kw)


def test_names_quarter_reads_a_numeric_end_date():
    q4 = dt.date(2024, 12, 31)
    assert names_quarter("Quarter Ended 12/31/24", q4)
    assert names_quarter("Quarter ended 12/31/2024", q4) and names_quarter("12-31-24", q4)
    assert names_quarter("Quarter Ended 9/30/24", dt.date(2024, 9, 30))
    assert not names_quarter("Quarter Ended 12/31/23", q4)
    assert not names_quarter("Twelve Months Ended 12/31/24", q4)
    assert not names_quarter("Quarter Ended 12/30/24", q4)
    assert not names_quarter("12/31-24", q4)


def test_a_dated_column_is_read_and_its_cell_carries_the_period():
    reading = _read(DATED, DATED_ANSWER)
    assert reading.status == "accepted", reading.reason
    assert reading.value == 954e6 and reading.currency == "USD" and reading.method == "html"
    assert reading.cell and "Global Sales" in reading.cell and not reading.generic_row


def test_the_full_year_column_is_refused():
    answer = {**DATED_ANSWER, "value": 3064, "column_headers": ["Twelve Months Ended 12/31/24", "Global Sales"]}
    reading = _read(DATED, answer)
    assert reading.status == "wrong_period", reading.reason


def test_a_value_from_another_row_or_an_unprinted_header_is_refused():
    assert _read(DATED, {**DATED_ANSWER, "value": 461}).status == "value_not_in_row"
    assert _read(DATED, {**DATED_ANSWER, "row_label": "Calderon XR"}).status == "row_not_in_document"
    assert _read(DATED, {**DATED_ANSWER, "column_headers": ["Three Months Ended December 31, 2024"]}).status \
        == "header_not_in_document"
    assert _read(DATED, {**DATED_ANSWER, "unit": "thousands"}).status == "unit_conflict"
    assert _read(DATED, {"found": False}).status == "not_found"


def test_a_generic_row_stands_for_the_product_only_when_the_caller_says_so():
    refused = _read(ANNUAL, ANNUAL_ANSWER)
    assert refused.status == "generic_row" and refused.generic_row
    accepted = _read(ANNUAL, ANNUAL_ANSWER, single_product_ok=True)
    assert accepted.status == "accepted", accepted.reason
    assert accepted.value == 1_460_000 and "December 31, 2024" in accepted.cell
    not_total = _read(ANNUAL, {**ANNUAL_ANSWER, "filer_total_product_revenue": False}, single_product_ok=True)
    assert not_total.status == "generic_row"
    other_quarter = _read(ANNUAL, {**ANNUAL_ANSWER, "value": 978}, single_product_ok=True)
    assert other_quarter.status == "wrong_period"


def test_a_royalty_row_is_not_own_sales():
    answer = {**ANNUAL_ANSWER, "value": 2000, "row_label": "License and collaboration revenue",
              "column_headers": ["Three Months Ended", "September 30, 2024"]}
    reading = _read(ANNUAL, answer, period="2024Q3", single_product_ok=True)
    assert reading.status == "not_own_sales"


def test_a_fixed_width_text_filing_is_read_from_its_own_lines():
    seen = []
    reading = read_layout(SEC + "q.txt", "Calderon", "2024Q1", single_product_ok=True,
                          get=_get({"q.txt": TEXT}), ask=_ask(TEXT_ANSWER, seen))
    assert reading.status == "accepted", reading.reason
    assert reading.method == "text" and reading.value == 219_997_000
    assert "  Product                          $219,997" in seen[0][0]["text"]
    wrong_year = {**TEXT_ANSWER, "value": 174596, "column_headers": ["Three Months Ended", "March 31,", "2023"]}
    assert read_layout(SEC + "q.txt", "Calderon", "2024Q1", single_product_ok=True,
                       get=_get({"q.txt": TEXT}), ask=_ask(wrong_year)).status == "wrong_period"


def test_a_sentence_states_its_own_period_and_scale():
    doc = b"<html><body><p>(in thousands)</p><p>Net sales of Calderon were $84.0 million for the fourth quarter " \
          b"of 2024.</p></body></html>"
    answer = {"found": True, "value": 84.0, "unit": "millions", "currency": "USD", "kind": "product_sales",
              "row_label": "Net sales of Calderon were $84.0 million for the fourth quarter of 2024.",
              "column_headers": []}
    assert _read(doc, answer).status == "accepted"
    assert _read(doc, {**answer, "unit": "billions"}).status == "unit_conflict"


@pytest.fixture
def pdf_host(monkeypatch):
    calls = []

    def fake_get(url, headers=None, **kw):
        calls.append(headers)

        class R:
            def __init__(self):
                self.content, self.headers = b"%PDF-1.4 invented", {"content-type": "application/pdf"}

            def raise_for_status(self):
                return None
        return R()
    monkeypatch.setattr(layout.httpx, "get", fake_get)
    return calls


def test_a_pdf_is_sent_as_a_document(pdf_host, monkeypatch):
    pdf = b"%PDF-1.4 invented"
    assert pdf_host == []
    monkeypatch.setattr(layout, "pdf_text", lambda content: "Sales by product (in millions)\n"
                        "Quarter Ended 12/31/24\nCalderon 527 427 954\n")
    seen = []
    reading = read_layout("https://acme.example/q4.pdf", "Calderon", "2024Q4",
                          ask=_ask({**DATED_ANSWER, "column_headers": ["Quarter Ended 12/31/24"]}, seen),
                          get=_get({}))
    assert reading.status == "accepted", reading.reason
    assert reading.method == "pdf" and reading.value == 954e6
    document = seen[0][1]
    assert document["type"] == "document" and document["mime_type"] == "application/pdf"
    assert base64.b64decode(document["data"]) == pdf


def test_other_hosts_are_fetched_with_the_declared_agent(pdf_host, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "acme research contact@acme.example")
    content, kind = layout.fetch("https://acme.example/q4.pdf", get=_get({}))
    assert content.startswith(b"%PDF") and "pdf" in kind
    assert pdf_host == [{"User-Agent": "acme research contact@acme.example"}]


def test_a_large_document_sends_the_tables_that_matter_first():
    filler = "".join(f"<p>Unrelated paragraph {i} about facilities and leases.</p>" for i in range(200))
    other = "".join(f"<table><tr><td>Lease {i}</td><td>Year Ended 2020</td></tr><tr><td>Rent</td><td>{i}</td></tr>"
                    f"</table>" for i in range(200))
    doc = ANNUAL.replace(b"<html><body>", f"<html><body>{filler}{other}".encode())
    rendered = render_html(doc, "Calderon", dt.date(2024, 12, 31), cap=1500)
    assert "Product revenue, net | $ 341 | $ 796 | $ 978 | $ 1,460" in rendered
    assert "Lease 7" not in rendered and len(rendered) < 1500 + 400


def test_failures_decline_and_a_spent_quota_stops():
    class Spent(Exception):
        status_code = 429

    def spent(contents):
        raise Spent("quota")

    with pytest.raises(QuotaExceeded):
        read_layout(SEC + "doc.htm", "Calderon", "2024Q4", get=_get({"doc.htm": DATED}), ask=spent)

    def broken(contents):
        raise ValueError("bad reply")

    assert read_layout(SEC + "doc.htm", "Calderon", "2024Q4", get=_get({"doc.htm": DATED}),
                       ask=broken).status == "error"
    assert read_layout(SEC + "doc.htm", "Calderon", "2024", get=_get({}), ask=broken).status == "not_a_quarter"


def test_a_table_in_year_sections_takes_the_year_printed_above_the_row():
    doc = (b"<html><body><p>Revenue came from three distributors in 2024, four in 2023 and five in 2022.</p>"
           b"<p>Quarterly Financial Data (Unaudited)</p><table>"
           b"<tr><td>(in thousands)</td><td>First</td><td>Second</td><td>Third</td><td>Fourth</td></tr>"
           b"<tr><td></td><td>Quarter</td><td>Quarter</td><td>Quarter</td><td>Quarter</td></tr>"
           b"<tr><td></td><td>2024</td><td></td><td></td><td></td></tr>"
           b"<tr><td>Product revenue</td><td>219,997</td><td>243,217</td><td>249,203</td><td>259,177</td></tr>"
           b"<tr><td></td><td>2023</td><td></td><td></td><td></td></tr>"
           b"<tr><td>Product revenue</td><td>174,596</td><td>190,009</td><td>193,242</td><td>203,232</td></tr>"
           b"</table></body></html>")
    answer = {"found": True, "value": 259177, "unit": "thousands", "currency": "USD", "kind": "product_sales",
              "row_label": "Product revenue", "column_headers": ["Fourth", "Quarter"],
              "filer_total_product_revenue": True}
    assert _read(doc, answer, single_product_ok=True).status == "accepted"
    assert _read(doc, {**answer, "value": 203232}, single_product_ok=True).status == "wrong_period"


def test_a_header_joined_from_rows_printed_apart_is_heard_only_under_a_grid_cell():
    doc = (b"<html><body><p>Quarterly Financial Data</p><table>"
           b"<tr><td>(in thousands)</td><td>Third</td><td>Fourth</td></tr>"
           b"<tr><td></td><td>Quarter</td><td>Quarter</td></tr>"
           b"<tr><td>Calderon</td><td>249,203</td><td>259,177</td></tr></table>"
           b"<p>All figures are for 2024.</p></body></html>")
    answer = {"found": True, "value": 259177, "unit": "thousands", "currency": "USD", "kind": "product_sales",
              "row_label": "Calderon", "column_headers": ["Fourth Quarter 2024"]}
    assert _read(doc, answer).status == "header_not_in_document"  # 2024 is printed below the row
    answer["column_headers"] = ["Fourth Quarter"]
    reading = _read(doc, answer)
    assert reading.status == "wrong_period" and "no year" in reading.reason
    text = b"Quarterly Financial Data 2024\n(in thousands)   Third     Fourth    Total\n" \
           b"                 Quarter   Quarter   Year\nCalderon         249,203   259,177   508,380\n"
    assert read_layout(SEC + "q.txt", "Calderon", "2024Q4", get=_get({"q.txt": text}),
                       ask=_ask(answer)).status == "header_not_in_document"
