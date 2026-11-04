"""The earnings-release reader, against invented exhibits and a scripted model."""

from __future__ import annotations

import datetime as dt

from app.retrieval.earnings import check, find_exhibits, names_quarter, read_earnings
from app.retrieval.single_product import filing_text
from sec_fixtures import Response

END = dt.date(2025, 6, 30)
EXHIBIT = (b"<html><body><p>ACME PHARMA reports second quarter 2025 results</p>"
           b"<table><tr><td>(in millions)</td><td>Three Months Ended June 30, 2025</td><td>2024</td></tr>"
           b"<tr><td>Calderon</td><td>412.5</td><td>380.1</td></tr>"
           b"<tr><td>NuVessa</td><td>90.0</td><td>70.0</td></tr></table></body></html>")
ROYALTY = (b"<html><body><table><tr><td>(in millions)</td><td>Three Months Ended June 30, 2025</td></tr>"
           b"<tr><td>Royalty revenue on Calderon</td><td>41.2</td></tr></table></body></html>")
GOOD = {"found": True, "value": 412.5, "unit": "millions", "currency": "USD", "kind": "product_sales",
        "row_quote": "Calderon 412.5 380.1", "period_labels": ["Three Months Ended June 30, 2025"]}


def _hit(adsh, cik, name, doc, form="8-K", items=("2.02", "9.01"), file_type="EX-99.1", filed="2025-07-30"):
    return {"_id": f"{adsh}:{doc}", "_source": {"adsh": adsh, "ciks": [f"000000{cik}"], "display_names": [name],
                                                  "form": form, "items": list(items), "file_type": file_type,
                                                  "file_date": filed}}


def _sec(hits, documents):
    def get(url, **params):
        if "search-index" in url:
            return Response(body={"hits": {"hits": hits}})
        for name, content in documents.items():
            if url.endswith(name):
                return Response(content)
        raise AssertionError(url)
    return get


def test_find_exhibits_keeps_results_exhibits_only():
    hits = [_hit("0001-25-1", 1, "ACME PHARMA (CIK 1)", "ex99.htm"),
            _hit("0001-25-2", 1, "ACME PHARMA (CIK 1)", "ex99b.htm", items=("8.01",)),
            _hit("0001-25-3", 1, "ACME PHARMA (CIK 1)", "main.htm", file_type="8-K"),
            _hit("0003-25-1", 3, "BETA AG (CIK 3)", "beta-99_1.htm", form="6-K", items=(), file_type="EX-99"),
            _hit("0003-25-2", 3, "BETA AG (CIK 3)", "beta-6k.htm", form="6-K", items=(), file_type="6-K"),
            _hit("0001-25-4", 1, "ACME PHARMA (CIK 1)", "main8k.htm", form="8-K", file_type="8-K")]
    found = find_exhibits("Calderon", END, get=_sec(hits, {}))
    assert [f["url"].rsplit("/", 1)[-1] for f in found] == ["ex99.htm", "beta-99_1.htm", "beta-6k.htm"]


def test_names_quarter():
    assert names_quarter("Three Months Ended June 30, 2025", END)
    assert names_quarter("Q2 2025", END) and names_quarter("2Q25", END) and names_quarter("Second Quarter 2025", END)
    assert not names_quarter("Six Months Ended June 30, 2025", END)
    assert not names_quarter("Three Months Ended June 30, 2024", END)


def test_check_accepts_a_verified_row_and_refuses_each_failure():
    text = filing_text(EXHIBIT)
    assert check(GOOD, text, "Calderon", END)[0] == "accepted"
    assert check({**GOOD, "row_quote": "Calderon $ 412.5 $ 380.1"}, text, "Calderon", END)[0] == "accepted"
    assert check({**GOOD, "row_quote": "Calderon 999"}, text, "Calderon", END)[0] == "quote_not_in_exhibit"
    assert check({**GOOD, "value": 400}, text, "Calderon", END)[0] == "value_not_in_quote"
    assert check({**GOOD, "period_labels": ["2024"]}, text, "Calderon", END)[0] == "wrong_period"
    assert check({**GOOD, "period_labels": ["Nine Months Ended"]}, text, "Calderon", END)[0] == "wrong_period"
    assert check({**GOOD, "unit": "thousands"}, text, "Calderon", END)[0] == "unit_conflict"
    assert check({"found": False}, text, "Calderon", END)[0] == "not_found"


def test_a_royalty_line_is_refused_whatever_the_model_calls_it():
    text = filing_text(ROYALTY)
    royalty = {**GOOD, "value": 41.2, "row_quote": "Royalty revenue on Calderon 41.2"}
    assert check(royalty, text, "Calderon", END)[0] == "not_own_sales"
    assert check({**royalty, "kind": "royalty"}, text, "Calderon", END)[0] == "not_own_sales"


def test_read_earnings_takes_the_issuers_reading_and_labels_the_others():
    hits = [_hit("0002-25-1", 2, "BETA BIO (CIK 2)", "beta.htm"), _hit("0001-25-1", 1, "ACME PHARMA (CIK 1)", "acme.htm")]
    get = _sec(hits, {"acme.htm": EXHIBIT, "beta.htm": ROYALTY})

    def ask(prompt):
        if "Royalty revenue" in prompt:
            return {**GOOD, "value": 41.2, "kind": "royalty", "row_quote": "Royalty revenue on Calderon 41.2"}
        return GOOD

    answer = read_earnings("Calderon", "Q2 2025", "Acme Pharma", get=get, ask=ask, later=False)
    assert (answer.status, answer.value, answer.currency) == ("found", 412.5e6, "USD")
    assert answer.scope == "Calderon 412.5 380.1"
    assert [r.status for r in answer.readings] == ["accepted", "not_own_sales"]
    assert read_earnings("Calderon", "Q2 2025", "Gamma Labs", get=get, ask=ask, later=False).status == "other_filer"


def test_disagreeing_sellers_without_an_issuer_are_not_picked_between():
    other = EXHIBIT.replace(b"412.5", b"977.0")
    hits = [_hit("0001-25-1", 1, "ACME PHARMA (CIK 1)", "acme.htm"), _hit("0002-25-1", 2, "BETA BIO (CIK 2)", "beta.htm")]
    get = _sec(hits, {"acme.htm": EXHIBIT, "beta.htm": other})

    def ask(prompt):
        return {**GOOD, "value": 977.0, "row_quote": "Calderon 977.0 380.1"} if "977.0" in prompt else GOOD

    answer = read_earnings("Calderon", "Q2 2025", get=get, ask=ask, later=False)
    assert answer.status == "several_sellers" and len([r for r in answer.readings if r.status == "accepted"]) == 2


def test_split_header_cells_and_self_dating_sentences_name_the_quarter():
    split = filing_text(b"<table><tr><td>Three Months Ended June 30,</td></tr><tr><td>2025</td><td>2024</td></tr>"
                        b"<tr><td>Calderon</td><td>412.5</td><td>380.1</td></tr></table>")
    assert check({**GOOD, "period_labels": ["Three Months Ended June 30,", "2025"]}, split, "Calderon", END)[0] == "accepted"
    prose = filing_text(b"<p>Recorded $41.2 million in Calderon net product revenue in the second quarter of 2025.</p>")
    sentence = {**GOOD, "value": 41.2, "period_labels": [],
                "row_quote": "Recorded $41.2 million in Calderon net product revenue in the second quarter of 2025."}
    assert check(sentence, prose, "Calderon", END)[0] == "accepted"
    assert names_quarter("4th Qtr 2015", dt.date(2015, 12, 31))


def test_a_sentence_states_its_own_scale_over_the_tables_heading():
    text = filing_text(b"<p>(in thousands)</p><p>Net product revenues of Calderon were $84.0 million for the "
                       b"quarter ended June 30, 2025.</p>")
    prose = {**GOOD, "value": 84.0, "unit": "millions", "period_labels": [],
             "row_quote": "Net product revenues of Calderon were $84.0 million for the quarter ended June 30, 2025."}
    assert check(prose, text, "Calderon", END)[0] == "accepted"
    assert check({**prose, "unit": "billions"}, text, "Calderon", END)[0] == "unit_conflict"
    assert names_quarter("SECOND-QUARTER 2025 and 2024", END)


def test_a_figure_missing_from_its_own_release_is_read_from_a_later_comparative_column():
    later = (b"<table><tr><td>(in millions)</td><td>Three Months Ended September 30,</td></tr>"
             b"<tr><td>2026</td><td>2025</td></tr><tr><td>Calderon</td><td>500.0</td><td>412.5</td></tr></table>")
    own = b"<p>Calderon remains in Phase 3.</p>"
    calls = []

    def get(url, **params):
        if "search-index" in url:
            calls.append(params["startdt"])
            doc = "own.htm" if params["startdt"] == "2025-09-30" else "later.htm"
            return Response(body={"hits": {"hits": [_hit(f"0001-{doc}", 1, "ACME PHARMA (CIK 1)", doc)]}})
        return Response(own if url.endswith("own.htm") else later)

    def ask(prompt):
        if "Phase 3" in prompt:
            return {"found": False}
        return {**GOOD, "row_quote": "Calderon 500.0 412.5", "period_labels": ["Three Months Ended September 30,", "2025"],
                "value": 412.5}

    answer = read_earnings("Calderon", "Q3 2025", "Acme Pharma", get=get, ask=ask)
    assert answer.status == "found" and answer.later and answer.value == 412.5e6
    assert calls[:2] == ["2025-09-30", "2025-12-31"]
    assert answer.readings[-1].released_after == "Q4 2025"


def test_a_restated_comparative_column_is_refused():
    text = filing_text(b"<table><tr><td>(in millions)</td><td>Three Months Ended June 30, 2025 (as restated)</td></tr>"
                       b"<tr><td>Calderon</td><td>412.5</td></tr></table>")
    restated = {**GOOD, "row_quote": "Calderon 412.5", "period_labels": ["Three Months Ended June 30, 2025 (as restated)"]}
    assert check(restated, text, "Calderon", END)[0] == "restated_basis"


def test_a_spent_quota_stops_the_reading_instead_of_declining():
    import pytest

    from app.retrieval.gemini_search import QuotaExceeded

    class Spent(Exception):
        status_code = 429

    def ask(prompt):
        raise Spent("Your project has exceeded its monthly spending cap.")

    get = _sec([_hit("0001-25-1", 1, "ACME PHARMA (CIK 1)", "acme.htm")], {"acme.htm": EXHIBIT})
    with pytest.raises(QuotaExceeded):
        read_earnings("Calderon", "Q2 2025", "Acme Pharma", get=get, ask=ask, later=False)


def test_depleted_prepaid_credits_stop_the_reading_like_a_spent_quota():
    import pytest

    from app.retrieval.gemini_search import QuotaExceeded

    class Depleted(Exception):
        status_code = 402

    def ask(prompt):
        raise Depleted("Your prepayment credits are depleted.")

    get = _sec([_hit("0001-25-1", 1, "ACME PHARMA (CIK 1)", "acme.htm")], {"acme.htm": EXHIBIT})
    with pytest.raises(QuotaExceeded):
        read_earnings("Calderon", "Q2 2025", "Acme Pharma", get=get, ask=ask, later=False)


def test_a_us_question_takes_the_rows_us_column():
    table = (b"<table><tr><td>(in millions)</td><td colspan='3'>Three Months Ended June 30,</td></tr>"
             b"<tr><td></td><td colspan='3'>2025</td></tr><tr><td></td><td>U.S.</td><td>ROW</td><td>Total</td></tr>"
             b"<tr><td>Calderon</td><td>412.2</td><td>300.2</td><td>712.4</td></tr></table>")
    get = _sec([_hit("0001-25-1", 1, "ACME PHARMA (CIK 1)", "acme.htm")], {"acme.htm": table})

    def ask(prompt):
        total = next(line.split(":")[0] for line in prompt.splitlines() if "712.4" in line and line.startswith("c"))
        return {"cell": total, "unit": "millions", "currency": "USD", "kind": "product_sales"}

    worldwide = read_earnings("Calderon", "Q2 2025", "Acme Pharma", get=get, ask=ask, later=False)
    us = read_earnings("Calderon", "Q2 2025", "Acme Pharma", get=get, ask=ask, later=False, scope="us")
    assert (worldwide.value, us.value) == (712.4e6, 412.2e6)

    get = _sec([_hit("0001-25-1", 1, "ACME PHARMA (CIK 1)", "acme.htm")], {"acme.htm": EXHIBIT})
    unsplit = read_earnings("Calderon", "Q2 2025", "Acme Pharma", get=get, ask=lambda p: {"cell": "c0", **GOOD},
                            later=False, scope="us")
    assert (unsplit.status, unsplit.value, unsplit.geography) == ("found", 412.5e6, "no_geographic_split")

    abroad = table.replace(b"U.S.", b"Europe")
    get = _sec([_hit("0001-25-1", 1, "ACME PHARMA (CIK 1)", "acme.htm")], {"acme.htm": abroad})
    split = read_earnings("Calderon", "Q2 2025", "Acme Pharma", get=get, ask=ask, later=False, scope="us")
    assert split.status != "found" and split.readings[0].status == "no_us_column"


def test_a_us_line_label_answers_and_a_rounded_summary_takes_its_precise_twin():
    from app.retrieval.earnings import is_us
    from app.retrieval.grid import Cell

    assert is_us(Cell(0, (), ("Calderon - U.S.",), ("Q2 2025",), "412"))
    assert not is_us(Cell(0, (), ("Calderon - ex-U.S.",), ("Q2 2025",), "300"))
    assert not is_us(Cell(0, (), ("Calderon - non-U.S.",), ("Q2 2025",), "300"))
    release = (b"<p>(in millions)</p><table><tr><td></td><td>Q2 2025</td></tr>"
               b"<tr><td>Calderon - U.S.</td><td>412</td></tr></table>"
               b"<table><tr><td>(in millions)</td><td colspan='2'>Three Months Ended June 30,</td></tr>"
               b"<tr><td></td><td colspan='2'>2025</td></tr><tr><td></td><td>U.S.</td><td>Total</td></tr>"
               b"<tr><td>Calderon</td><td>412.2</td><td>712.4</td></tr></table>")
    get = _sec([_hit("0001-25-1", 1, "ACME PHARMA (CIK 1)", "acme.htm")], {"acme.htm": release})

    def ask(prompt):
        summary = next(line.split(":")[0] for line in prompt.splitlines() if line.endswith("= 412"))
        return {"cell": summary, "unit": "millions", "currency": "USD", "kind": "product_sales"}

    us = read_earnings("Calderon", "Q2 2025", "Acme Pharma", get=get, ask=ask, later=False, scope="us")
    assert us.value == 412.2e6


def test_an_8k_from_before_item_202_is_read_whatever_its_items():
    hits = [_hit("0001-03-1", 1, "ACME PHARMA (CIK 1)", "ex99-2003.htm", items=("7", "9"), filed="2003-07-24"),
            _hit("0001-25-2", 1, "ACME PHARMA (CIK 1)", "ex99-2025.htm", items=("8.01",))]
    found = find_exhibits("Calderon", END, get=_sec(hits, {}))
    assert [f["url"].rsplit("/", 1)[-1] for f in found] == ["ex99-2003.htm"]
