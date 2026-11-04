"""The investor-relations archive route, against invented pages, PDFs and a scripted model."""

from __future__ import annotations

import base64

import pytest

from app.retrieval.ir_archive import (
    Archive,
    Document,
    FiscalCalendar,
    Refused,
    Span,
    check_answer,
    detect_calendar,
    document_reader,
    list_reports,
    locate_archive,
    parse_periods,
    quarter_span,
    read_ir_quarter,
)

APRIL = FiscalCalendar(4)
Q2_2023 = Span((2023, 4), (2023, 6))


def spans(text, fiscal=FiscalCalendar()):
    return [s for s, _ in parse_periods(text, fiscal)]


# ---------------------------------------------------------------- periods


def test_quarter_labels_in_their_usual_spellings():
    for label in ("Q2 2023", "2Q23", "2Q 2023", "Q2'23", "2023 Q2", "second quarter 2023", "Q2-2023",
                  "three months ended June 30, 2023", "April-June 2023", "Apr. – Jun. 2023"):
        assert spans(label) == [Q2_2023], label


def test_half_years_nine_months_and_full_years():
    assert spans("H1 2023") == spans("Half-year 2023") == spans("1H23") == [Span((2023, 1), (2023, 6))]
    assert spans("9M 2023") == spans("nine months ended September 30, 2023") == [Span((2023, 1), (2023, 9))]
    assert spans("FY2023") == spans("Full year 2023") == spans("January - December 2023") == [Span((2023, 1), (2023, 12))]


def test_a_bare_year_or_date_is_not_a_period():
    assert spans("Published 2023-07-27") == []
    assert spans("Annual meeting, page 23") == []


def test_fiscal_labels_follow_an_april_year():
    assert spans("FY2023 Q1", APRIL) == [Span((2023, 4), (2023, 6))]
    assert spans("Q2 FY2023", APRIL) == [Span((2023, 7), (2023, 9))]
    assert spans("FY2023 H1", APRIL) == [Span((2023, 4), (2023, 9))]
    assert spans("FY2023", APRIL) == [Span((2023, 4), (2024, 3))]
    assert spans("FY2024/3", APRIL) == [Span((2023, 4), (2024, 3))]
    # Spelled-out months stay calendar months under any fiscal year.
    assert spans("April-June 2023", APRIL) == [Q2_2023]
    assert spans("FY2023", FiscalCalendar(4, named_by_start=False)) == [Span((2022, 4), (2023, 3))]


def test_a_url_and_a_folder_year():
    assert spans("dam acme 2024 q4 2023 results pdf") == [Span((2023, 10), (2023, 12))]


def test_detect_calendar():
    assert detect_calendar("Results for the fiscal year ending March 31, 2024") == FiscalCalendar(4)
    assert detect_calendar("FY2024/3 Q2 financial results") == FiscalCalendar(4)
    assert detect_calendar("FY2023 (April 1, 2023 - March 31, 2024)") == FiscalCalendar(4, True)
    assert detect_calendar("FY2024 (April 1, 2023 - March 31, 2024)") == FiscalCalendar(4, False)
    assert detect_calendar("Q1 (April - June) results") == FiscalCalendar(4)
    assert detect_calendar("Quarterly results archive") is None


def test_a_calendar_unstated_is_read_from_release_dates():
    page = ("Q2/FY2026 Financial results November 4, 2026 ... Q1/FY2026 Financial results August 5, 2026 "
            "Full Year and Q4/FY2025 Financial results April 25, 2026")
    assert detect_calendar(page) == FiscalCalendar(4, True)
    assert detect_calendar("Q2/FY2026 results July 28, 2026") == FiscalCalendar(1)
    assert detect_calendar("Q2/FY2026 results") is None
    stray = page + " Q3/FY2020) (831KB) download Q2/FY2020 Financial Results October 30, 2020"
    assert detect_calendar(stray) == FiscalCalendar(4, True)


def test_a_quarter_year_to_date_label():
    assert spans("Supplementary Documents (Q2 YTD/FY2024)", APRIL) == [Span((2024, 4), (2024, 9))]
    assert spans("Financial Results (3Q YTD FY2024)", APRIL) == [Span((2024, 4), (2024, 12))]


def test_fiscal_label_names_a_span_the_issuer_way():
    assert APRIL.label(Q2_2023) == "Q1 FY2023"
    assert APRIL.label(Span((2024, 1), (2024, 3))) == "Q4 FY2023"
    assert quarter_span("2023Q2") == Q2_2023 and quarter_span("FY2023") is None


# ---------------------------------------------------------------- listing

ARCHIVE = b"""<html><body><h1>Results archive</h1>
<ul>
 <li><a href="/results/q1-2023">Q1 2023 results</a></li>
 <li><a href="https://cdn.acme.example/files/2023/hy-2023-data-book.pdf">Download</a></li>
 <li><span>Nine months 2023</span> <a href="/files/sales-update-oct.pdf">PDF</a></li>
 <li><a href="/about">About us</a></li>
</ul>
<section><h2>2022</h2><p><a href="/files/x1.pdf">Q3</a></p><p><a href="/files/x2.pdf">Q4</a></p></section>
</body></html>"""


def _fetcher(pages):
    def fetch(url):
        page = pages.get(url)
        if page is None:
            raise AssertionError(f"unexpected fetch {url}")
        if isinstance(page, Exception):
            raise page
        return page
    return fetch


def test_list_reports_assigns_periods_from_link_url_nearby_and_heading_year():
    page = Document("https://acme.example/investors/results", ARCHIVE, "text/html")
    found = {r.url.rsplit("/", 1)[-1]: (r.span, r.basis, r.kind)
             for r in list_reports(page.url, fetch=_fetcher({page.url: page}))}
    assert found["q1-2023"] == (Span((2023, 1), (2023, 3)), "link", "html")
    assert found["hy-2023-data-book.pdf"] == (Span((2023, 1), (2023, 6)), "url", "pdf")
    assert found["sales-update-oct.pdf"] == (Span((2023, 1), (2023, 9)), "nearby", "pdf")
    assert found["x1.pdf"] == (Span((2022, 7), (2022, 9)), "year_nearby", "pdf")
    assert found["x2.pdf"] == (Span((2022, 10), (2022, 12)), "year_nearby", "pdf")
    assert "about" not in found


def test_locate_archive_takes_the_first_candidate_that_lists_dated_reports():
    good = Document("https://acme.example/investors/results", ARCHIVE, "text/html")
    release = Document("https://acme.example/news/one", b"<html><a href='/q1-2023.pdf'>Q1 2023</a></html>", "text/html")
    fetch = _fetcher({good.url: good, release.url: release, "https://blocked.example/": Refused("403")})
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return {"archive_url": release.url, "other_urls": ["https://blocked.example/"]}, [good.url]

    archive, refusals = locate_archive("Acme Pharma", ask=ask, fetch=fetch)
    assert len(calls) == 1 and "Acme Pharma" in calls[0]
    assert archive.url == good.url and len(refusals) == 2

    nothing, _ = locate_archive("Acme Pharma", ask=lambda p: ({"archive_url": release.url}, []), fetch=fetch)
    assert nothing is None


# ---------------------------------------------------------------- reading

DATA_BOOK = """Acme Pharma Half-year 2023 data book
CHF millions Q2 2023 Q2 2022 H1 2023 H1 2022
Calderon 412 380 800 735
NuVessa 90 70 170 140
"""
GOOD = {"found": True, "value": 412, "unit": "millions", "currency": "CHF", "kind": "product_sales",
        "row_quote": "Calderon 412 380 800 735", "period_labels": ["Q2 2023"]}


def _pdf(url, text):
    return Document(url, b"%PDF-1.7 invented", "application/pdf", _text=text)


def _archive(url, reports_html, fiscal=FiscalCalendar()):
    page = Document(url, reports_html, "text/html")
    reports = list_reports(url, fetch=_fetcher({url: page}), fiscal=fiscal)
    return lambda issuer: Archive(url, reports, fiscal, True)


def test_check_answer_accepts_a_verified_row_and_refuses_each_failure():
    assert check_answer(GOOD, DATA_BOOK, Q2_2023, FiscalCalendar())[0] == "accepted"
    assert check_answer({**GOOD, "row_quote": "Calderon 999"}, DATA_BOOK, Q2_2023, FiscalCalendar())[0] == \
        "quote_not_in_document"
    assert check_answer({**GOOD, "value": 413}, DATA_BOOK, Q2_2023, FiscalCalendar())[0] == "value_not_in_quote"
    assert check_answer({**GOOD, "period_labels": ["H1 2023"]}, DATA_BOOK, Q2_2023, FiscalCalendar())[0] == \
        "wrong_period"
    assert check_answer({**GOOD, "period_labels": ["Q3 2023"]}, DATA_BOOK, Q2_2023, FiscalCalendar())[0] == \
        "wrong_period"
    assert check_answer({**GOOD, "unit": "billions"}, DATA_BOOK + " in millions", Q2_2023, FiscalCalendar())[0] == \
        "unit_conflict"
    assert check_answer({"found": False}, DATA_BOOK, Q2_2023, FiscalCalendar())[0] == "not_found"


def test_check_answer_reads_fiscal_labels_through_the_issuers_year():
    text = "Revenue by product (JPY billions) FY2023 Q1 FY2022 Q1\nCalderon 17.4 15.0"
    answer = {**GOOD, "value": 17.4, "unit": "billions", "currency": "JPY", "row_quote": "Calderon 17.4 15.0",
              "period_labels": ["FY2023 Q1"]}
    assert check_answer(answer, text, Q2_2023, APRIL)[0] == "accepted"
    # Under a calendar year the same label is calendar Q1, so it is refused.
    assert check_answer(answer, text, Q2_2023, FiscalCalendar())[0] == "wrong_period"


def test_read_ir_quarter_reads_the_quarters_pdf():
    url = "https://acme.example/results"
    pdf = _pdf("https://acme.example/q2-2023.pdf", DATA_BOOK)
    archive = _archive(url, b"<a href='/q2-2023.pdf'>Q2 2023 sales</a><a href='/q1-2023.pdf'>Q1 2023</a>")
    seen = []

    def ask(prompt, document):
        seen.append((prompt, document))
        return GOOD

    answer = read_ir_quarter("Calderon", "2023Q2", "Acme Pharma", archive=archive,
                             fetch=_fetcher({pdf.url: pdf}), ask=ask)
    assert (answer.status, answer.value, answer.currency, answer.method) == ("found", 412e6, "CHF", "direct")
    assert answer.url == pdf.url and seen[0][1] == pdf.content
    assert "three months ended June 30, 2023" in seen[0][0]


def test_a_report_that_never_names_the_product_costs_no_model_call():
    url = "https://acme.example/results"
    pdf = _pdf("https://acme.example/q2-2023.pdf", "NuVessa 90 70")
    archive = _archive(url, b"<a href='/q2-2023.pdf'>Q2 2023</a><a href='/q1-2023.pdf'>Q1 2023</a>")

    def ask(prompt, document):
        raise AssertionError("the model should not be asked")

    answer = read_ir_quarter("Calderon", "2023Q2", "Acme Pharma", archive=archive, fetch=_fetcher({pdf.url: pdf}),
                             ask=ask)
    assert answer.status == "not_found" and answer.readings[0].status == "product_not_in_report"


def test_an_april_issuers_fiscal_q1_answers_calendar_q2():
    url = "https://nuvessa.example/ir"
    q1 = _pdf("https://nuvessa.example/fy2023q1.pdf",
              "Revenue by product (JPY billions) FY2023 Q1 FY2022 Q1\nCalderon 17.4 15.0")
    archive = _archive(url, b"<a href='/fy2023q1.pdf'>FY2023 Q1</a><a href='/fy2023q2.pdf'>FY2023 Q2</a>", APRIL)

    def ask(prompt, document):
        assert "Q1 FY2023" in prompt
        return {"found": True, "value": 17.4, "unit": "billions", "currency": "JPY", "kind": "product_sales",
                "row_quote": "Calderon 17.4 15.0", "period_labels": ["FY2023 Q1"]}

    answer = read_ir_quarter("Calderon", "Q2 2023", "NuVessa", archive=archive, fetch=_fetcher({q1.url: q1}), ask=ask)
    assert (answer.status, answer.value, answer.currency) == ("found", pytest.approx(17.4e9), "JPY")
    assert answer.fiscal_start_month == 4


def test_a_quarter_from_a_half_year_less_the_first_quarter():
    url = "https://acme.example/results"
    half = _pdf("https://acme.example/hy-2023.pdf", "CHF millions H1 2023 H1 2022\nCalderon 800 735")
    first = _pdf("https://acme.example/q1-2023.pdf", "CHF millions Q1 2023 Q1 2022\nCalderon 388 355")
    archive = _archive(url, b"<a href='/hy-2023.pdf'>Half-year 2023</a><a href='/q1-2023.pdf'>Q1 2023</a>")

    def ask(prompt, document):
        if "three months ended June 30, 2023" in prompt:
            return {"found": False}
        if "six months ended June 30, 2023" in prompt:
            return {"found": True, "value": 800, "unit": "millions", "currency": "CHF", "kind": "product_sales",
                    "row_quote": "Calderon 800 735", "period_labels": ["H1 2023"]}
        assert "three months ended March 31, 2023" in prompt
        return {"found": True, "value": 388, "unit": "millions", "currency": "CHF", "kind": "product_sales",
                "row_quote": "Calderon 388 355", "period_labels": ["Q1 2023"]}

    answer = read_ir_quarter("Calderon", "Q2 2023", "Acme Pharma", archive=archive,
                             fetch=_fetcher({half.url: half, first.url: first}), ask=ask)
    assert (answer.status, answer.method, answer.value) == ("found", "ytd_difference", 412e6)


def test_a_calendar_issuers_html_quarter_is_read_as_a_grid():
    url = "https://acme.example/results"
    page = Document("https://acme.example/q2-2023", b"""<html><body><p>Acme Pharma second quarter 2023</p>
        <table><tr><td>(CHF millions)</td><td>Three Months Ended June 30, 2023</td><td>2022</td></tr>
        <tr><td>Calderon</td><td>412</td><td>380</td></tr></table></body></html>""", "text/html")
    archive = _archive(url, b"<a href='/q2-2023'>Q2 2023</a><a href='/q1-2023'>Q1 2023</a>")

    def ask(prompt, document):
        assert document is None
        return {"cell": "c0", "unit": "millions", "currency": "CHF", "kind": "product_sales"}

    answer = read_ir_quarter("Calderon", "Q2 2023", "Acme Pharma", archive=archive, fetch=_fetcher({page.url: page}),
                             ask=ask)
    assert (answer.status, answer.value, answer.readings[-1].method) == ("found", 412e6, "html_grid")


def test_a_period_page_that_links_its_files_is_followed_to_the_pdf():
    url = "https://acme.example/results"
    page = Document("https://acme.example/q2-2023", b"<html><a href='/slides.pdf'>Slides</a>"
                    b"<a href='/data.pdf'>Data book</a></html>", "text/html")
    slides = _pdf("https://acme.example/slides.pdf", "Strategy update")
    data = _pdf("https://acme.example/data.pdf", DATA_BOOK)
    archive = _archive(url, b"<a href='/q2-2023'>Q2 2023</a><a href='/q1-2023'>Q1 2023</a>")
    answer = read_ir_quarter("Calderon", "Q2 2023", "Acme Pharma", archive=archive,
                             fetch=_fetcher({page.url: page, slides.url: slides, data.url: data}),
                             ask=lambda prompt, document: GOOD)
    assert answer.status == "found" and answer.url == data.url


def test_a_host_that_refuses_the_client_is_reported_not_disguised_for():
    url = "https://acme.example/results"
    archive = _archive(url, b"<a href='/q2-2023.pdf'>Q2 2023</a><a href='/q1-2023.pdf'>Q1 2023</a>")
    answer = read_ir_quarter("Calderon", "Q2 2023", "Acme Pharma", archive=archive,
                             fetch=_fetcher({"https://acme.example/q2-2023.pdf": Refused("403")}),
                             ask=lambda p, d: GOOD)
    assert answer.status == "refused_by_host"


def test_no_archive_and_no_report_and_not_a_quarter():
    assert read_ir_quarter("Calderon", "Q2 2023", "Acme", archive=lambda i: None).status == "no_archive"
    archive = _archive("https://acme.example/r", b"<a href='/q1-2021.pdf'>Q1 2021</a><a href='/q2-2021.pdf'>Q2 2021</a>")
    assert read_ir_quarter("Calderon", "Q2 2023", "Acme", archive=archive).status == "no_report"
    assert read_ir_quarter("Calderon", "FY2023", "Acme", archive=archive).status == "not_a_quarter"


def test_document_reader_sends_the_pdf_as_a_document_block():
    class Interactions:
        def create(self, **kwargs):
            self.kwargs = kwargs
            return {"output_text": '```json\n{"found": false}\n```'}

    class Client:
        interactions = Interactions()

    client = Client()
    assert document_reader("read it", b"%PDF-1.7", client=client, model="m") == {"found": False}
    text, document = client.interactions.kwargs["input"]
    assert text == {"type": "text", "text": "read it"}
    assert document["type"] == "document" and document["mime_type"] == "application/pdf"
    assert base64.b64decode(document["data"]) == b"%PDF-1.7"


def test_an_archive_paginated_by_fiscal_year_is_followed_one_level():
    url = "https://nuvessa.example/ir"
    year_page = Document("https://nuvessa.example/ir/fy2023", b"<a href='/files/FY2023/Q1/data.pdf'>Data</a>"
                         b"<a href='/files/FY2023/Q2/data.pdf'>Data</a>", "text/html")
    q1 = _pdf("https://nuvessa.example/files/FY2023/Q1/data.pdf",
              "Revenue by product (JPY billions) FY2023 Q1 FY2022 Q1\nCalderon 17.4 15.0")
    archive = _archive(url, b"<a href='/ir/fy2023'>FY2023</a><a href='/ir/fy2022'>FY2022</a>", APRIL)
    answer = read_ir_quarter("Calderon", "Q2 2023", "NuVessa", archive=archive,
                             fetch=_fetcher({year_page.url: year_page, q1.url: q1}),
                             ask=lambda prompt, document: {
                                 "found": True, "value": 17.4, "unit": "billions", "currency": "JPY",
                                 "kind": "product_sales", "row_quote": "Calderon 17.4 15.0",
                                 "period_labels": ["FY2023 Q1"]})
    assert (answer.status, answer.url) == ("found", q1.url)
