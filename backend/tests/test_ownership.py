"""The ownership timeline, handovers and quarter sellers, against invented filings."""

from __future__ import annotations

import datetime as dt

from sec_fixtures import Response

from app.retrieval.ownership import (
    dates_in,
    handover,
    item_201,
    nearest_quarter,
    seller_for,
    short_name,
    timeline,
)

REPORTS = {
    2016: [("ACME PHARMA (CIK 1)", 1, "acme-2016.htm", b"<p>Net product sales of Calderon were $120 million.</p>")],
    2017: [("BETA BIO (CIK 2)", 2, "beta-2017.htm", b"<p>Following the merger we recorded Calderon sales of $90 million.</p>"),
           ("GAMMA LABS (CIK 3)", 3, "gamma-2017.htm", b"<p>We compete with Calderon.</p>")],
}


def get(url, **params):
    if "search-index" in url:
        year = int(params["startdt"][:4]) - 1
        hits = [{"_id": f"000{cik}-{year}:{doc}", "_source": {"ciks": [f"000{cik}"], "display_names": [name], "file_type": "10-K"}}
                for name, cik, doc, _ in REPORTS.get(year, [])]
        return Response(body={"hits": {"hits": hits}})
    for rows in REPORTS.values():
        for _, _, doc, content in rows:
            if url.endswith(doc):
                return Response(content)
    raise AssertionError(url)


def ask(prompt):
    if "Net product sales of Calderon" in prompt:
        return {"role": "seller", "quote": "Net product sales of Calderon were $120 million."}
    if "Following the merger" in prompt:
        return {"role": "seller", "quote": "Following the merger we recorded Calderon sales of $90 million."}
    return {"role": "seller", "quote": "an invented sentence"}


def test_sellers_by_year_follow_the_product_to_its_new_owner():
    result = timeline("Calderon", range(2016, 2019), get=get, ask=ask)
    assert result.sellers(2016) == ["ACME PHARMA (CIK 1)"]
    assert result.sellers(2017) == ["BETA BIO (CIK 2)"]
    assert {r.filer: r.role for r in result.years[2017]}["GAMMA LABS (CIK 3)"] == "unverified"
    assert result.gaps() == [2018]


# ---------------------------------------------------------------- an invented EDGAR


class Edgar:
    """Annual reports, 8-Ks and 10-Qs served from memory, searched the way
    full-text search answers: `forms=8-K` returns amendments too, and a form
    list naming `8-K/A` returns nothing."""

    def __init__(self, annual, current=(), quarterly=()):
        self.annual = annual  # {year: [(filer, cik, doc, html)]}
        self.current = list(current)  # [(filer, cik, accession, form, filed, items, {doc: (type, html)})]
        self.quarterly = list(quarterly)  # [(filer, cik, accession, doc, period_end, html)]
        self.searches = []

    def __call__(self, url, **params):
        if "search-index" in url:
            self.searches.append(params)
            return Response(body={"hits": {"hits": self._hits(params)}})
        for rows in self.annual.values():
            for _, _, doc, html in rows:
                if url.endswith("/" + doc):
                    return Response(html)
        for filer, cik, accession, form, filed, items, docs in self.current:
            folder = f"/{cik}/{accession.replace('-', '')}/"
            if url.endswith(f"{accession}-index.htm"):
                # The form document is linked through the inline-XBRL viewer, as EDGAR does.
                rows = "".join(f'<tr><td scope="row"><a href="/ix?doc=/Archives/edgar/data{folder}{d}">{d}</a> '
                               f'&nbsp;<span>iXBRL</span></td><td scope="row">{t}</td></tr>' if t.startswith("8-K") else
                               f'<tr><td scope="row"><a href="/Archives/edgar/data{folder}{d}">{d}</a></td>'
                               f'<td scope="row">{t}</td></tr>' for d, (t, _) in docs.items())
                return Response(f"<table>{rows}</table>".encode())
            for doc, (_, html) in docs.items():
                if url.endswith(folder + doc):
                    return Response(html)
        for *_, accession, doc, _, html in self.quarterly:
            if url.endswith(f"{accession.replace('-', '')}/{doc}"):
                return Response(html)
        raise AssertionError(url)

    def _hits(self, params):
        term = params["q"].strip('"').lower()
        forms = params.get("forms")
        if forms == "10-K,20-F,40-F":
            year = int(params["startdt"][:4]) - 1
            return [{"_id": f"{cik}-{year}:{doc}", "_source": {"ciks": [str(cik).zfill(10)], "display_names": [filer],
                                                               "file_type": "10-K"}}
                    for filer, cik, doc, html in self.annual.get(year, []) if term in html.decode().lower()]
        start, end = (dt.date.fromisoformat(params[k]) for k in ("startdt", "enddt"))
        if forms == "8-K":
            return [{"_id": f"{accession}:{doc}",
                     "_source": {"ciks": [str(cik).zfill(10)], "display_names": [filer], "form": form,
                                 "file_type": kind, "file_date": filed, "items": items}}
                    for filer, cik, accession, form, filed, items, docs in self.current
                    for doc, (kind, html) in docs.items()
                    if term in html.decode().lower() and start <= dt.date.fromisoformat(filed) <= end
                    and params.get("items", "2.01") in items]
        if forms == "10-Q":
            return [{"_id": f"{accession}:{doc}",
                     "_source": {"ciks": [str(cik).zfill(10)], "display_names": [filer], "file_type": "10-Q",
                                 "period_ending": period_end}}
                    for filer, cik, accession, doc, period_end, html in self.quarterly
                    if term in html.decode().lower() and str(cik).zfill(10) == params.get("ciks")
                    and start <= dt.date.fromisoformat(period_end) <= end]
        return []


ANNUAL = {
    2016: [("ACME PHARMA INC (CIK 1)", 1, "acme-2016.htm", b"<p>Net product sales of Calderon were $120 million.</p>")],
    2017: [("BETA BIO CORP (CIK 2)", 2, "beta-2017.htm",
            b"<p>Following our acquisition of Acme Pharma we recorded Calderon sales of $90 million.</p>")],
}

CLOSING = ("BETA BIO CORP (CIK 2)", 2, "0000000002-17-000010", "8-K", "2017-05-16", ["2.01", "9.01"], {
    "beta-8k.htm": ("8-K", (b"<p>Item 2.01. Completion of Acquisition or Disposition of Assets.</p>"
                            b"<p>On May 12, 2017, Beta Bio Corp completed its acquisition of Acme Pharma Inc.</p>"
                            b"<p>Item 9.01. Financial Statements and Exhibits.</p>")),
    "beta-ex99.htm": ("EX-99.1", b"<p>Acme Pharma markets Calderon in the United States.</p>"),
})

UNRELATED = ("DELTA THERAPEUTICS (CIK 4)", 4, "0000000004-17-000001", "8-K", "2017-03-01", ["2.01"], {
    "delta-8k.htm": ("8-K", (b"<p>Item 2.01. Completion of Acquisition. On February 27, 2017, Delta acquired a "
                             b"Calderon competitor.</p>")),
})

OTHER_DEAL = ("BETA BIO CORP (CIK 2)", 2, "0000000002-16-000003", "8-K", "2016-09-02", ["2.01"], {
    "beta-nuvessa.htm": ("8-K", (b"<p>Item 2.01. Completion of Acquisition. On August 30, 2016, Beta Bio Corp "
                                 b"completed its acquisition of the NuVessa programme from Acme Pharma Inc.</p>")),
})


def reader(log=None):
    """A model that answers each prompt from the invented documents it carries."""
    def ask(prompt):
        if log is not None:
            log.append(prompt)
        if "annual report" in prompt:
            if "Net product sales of Calderon" in prompt:
                return {"role": "seller", "quote": "Net product sales of Calderon were $120 million."}
            if "recorded Calderon sales" in prompt:
                return {"role": "seller", "quote": "Following our acquisition of Acme Pharma we recorded Calderon "
                                                   "sales of $90 million."}
            return {"role": "mention", "quote": ""}
        if "Item 2.01" in prompt:
            if "May 12, 2017" in prompt:
                return {"transfers": True, "closed": "2017-05-12",
                        "quote": "On May 12, 2017, Beta Bio Corp completed its acquisition of Acme Pharma Inc."}
            if "NuVessa" in prompt:
                return {"transfers": False, "closed": "2016-08-30", "quote": ""}
            return {"transfers": True, "closed": "2017-02-27", "quote": "Delta acquired a Calderon competitor."}
        if "quarterly report" in prompt:
            if "completed the acquisition of Acme Pharma on August 3, 2017" in prompt:
                return {"sells": True, "quote": "Net product sales of Calderon were $30 million.",
                        "closed": "2017-08-03",
                        "closing_quote": "We completed the acquisition of Acme Pharma on August 3, 2017."}
            if "Net product sales of Calderon" in prompt:
                return {"sells": True, "quote": "Net product sales of Calderon were $30 million.",
                        "closed": None, "closing_quote": None}
            return {"sells": False, "quote": ""}
        raise AssertionError(prompt[:200])
    return ask


def test_a_closing_inside_the_quarter_splits_it_at_the_closing_date():
    edgar = Edgar(ANNUAL, current=[CLOSING, UNRELATED, OTHER_DEAL])
    q2 = seller_for("Calderon", "Q2 2017", get=edgar, ask=reader())
    assert q2.status == "split"
    assert [(s.filer, s.start, s.end) for s in q2.sellers] == [
        ("ACME PHARMA INC (CIK 1)", dt.date(2017, 4, 1), dt.date(2017, 5, 11)),
        ("BETA BIO CORP (CIK 2)", dt.date(2017, 5, 12), dt.date(2017, 6, 30)),
    ]
    assert q2.handover.source == "8-K item 2.01"
    assert "On May 12, 2017, Beta Bio Corp completed its acquisition of Acme Pharma Inc." in q2.evidence
    # the 8-K search names the old seller, so a closing that never names the product is found
    assert any(s["q"] == '"ACME PHARMA"' for s in edgar.searches)
    assert all(s["forms"] in ("8-K", "10-Q", "10-K,20-F,40-F") for s in edgar.searches)


def test_quarters_either_side_of_the_closing_have_one_seller():
    edgar = Edgar(ANNUAL, current=[CLOSING])
    q1 = seller_for("Calderon", "Q1 2017", get=edgar, ask=reader())
    q3 = seller_for("Calderon", "Q3 2017", get=edgar, ask=reader())
    assert q1.status == q3.status == "single"
    assert [s.cik for s in q1.sellers] == ["1"]
    assert [s.cik for s in q3.sellers] == ["2"]


def test_a_completion_by_an_unrelated_filer_or_of_another_asset_is_not_the_handover():
    edgar = Edgar(ANNUAL, current=[UNRELATED, OTHER_DEAL])
    h = handover("Calderon", 2016, 2017, get=edgar, ask=reader())
    assert h.status == "unknown"
    assert h.closed is None


def test_a_quote_that_does_not_carry_the_closing_date_is_refused():
    def ask(prompt):
        if "Item 2.01" in prompt:
            return {"transfers": True, "closed": "2017-05-01",
                    "quote": "On May 12, 2017, Beta Bio Corp completed its acquisition of Acme Pharma Inc."}
        return reader()(prompt)

    h = handover("Calderon", 2016, 2017, get=Edgar(ANNUAL, current=[CLOSING]), ask=ask)
    assert h.status == "unknown"


def test_without_a_completion_report_the_first_10q_dates_the_quarter():
    tenq = ("BETA BIO CORP (CIK 2)", 2, "0000000002-17-000020", "beta-10q-q3.htm", "2017-10-01",
            b"<p>Net product sales of Calderon were $30 million.</p>")
    edgar = Edgar(ANNUAL, quarterly=[tenq])
    h = handover("Calderon", 2016, 2017, get=edgar, ask=reader())
    assert (h.status, h.quarter, h.source) == ("quarter_only", "Q3 2017", "10-Q")
    q3 = seller_for("Calderon", "Q3 2017", get=edgar, ask=reader())
    assert q3.status == "split_undated"
    assert [(s.cik, s.start, s.end) for s in q3.sellers] == [("1", None, None), ("2", None, None)]
    assert [s.cik for s in seller_for("Calderon", "Q2 2017", get=edgar, ask=reader()).sellers] == ["1"]
    assert [s.cik for s in seller_for("Calderon", "Q4 2017", get=edgar, ask=reader()).sellers] == ["2"]


def test_a_10q_that_states_the_closing_date_dates_the_day():
    tenq = ("BETA BIO CORP (CIK 2)", 2, "0000000002-17-000020", "beta-10q-q3.htm", "2017-09-30",
            (b"<p>Net product sales of Calderon were $30 million.</p>"
             b"<p>We completed the acquisition of Acme Pharma on August 3, 2017.</p>"))
    q3 = seller_for("Calderon", "Q3 2017", get=Edgar(ANNUAL, quarterly=[tenq]), ask=reader())
    assert q3.status == "split"
    assert [(s.cik, s.end) for s in q3.sellers] == [("1", dt.date(2017, 8, 2)), ("2", dt.date(2017, 9, 30))]


def test_an_owner_outside_the_sec_is_reported_unknown_not_named():
    annual = {2016: [], 2017: ANNUAL[2017]}
    edgar = Edgar(annual, current=[CLOSING])
    q2 = seller_for("Calderon", "Q2 2017", get=edgar, ask=reader())
    assert q2.status == "split"
    before, after = q2.sellers
    assert (before.filer, before.cik, before.sec_filer) == (None, None, False)
    assert "not found in any SEC filing" in before.note
    assert before.end == dt.date(2017, 5, 11)
    assert (after.cik, after.start) == ("2", dt.date(2017, 5, 12))


def test_a_year_no_filer_reports_selling_is_a_gap():
    q = seller_for("Calderon", "Q2 2018", get=Edgar(ANNUAL), ask=reader())
    assert q.status == "unknown"
    assert [(s.filer, s.cik) for s in q.sellers] == [(None, None)]
    assert "2018" in q.sellers[0].note


def test_unchanged_sellers_need_no_handover_reading():
    annual = {2016: ANNUAL[2016], 2017: [("ACME PHARMA INC (CIK 1)", 1, "acme-2017.htm",
                                          b"<p>Net product sales of Calderon were $120 million.</p>")]}
    log = []
    q = seller_for("Calderon", "Q2 2017", get=Edgar(annual), ask=reader(log))
    assert (q.status, [s.cik for s in q.sellers]) == ("single", ["1"])
    assert q.handover is None
    assert all("annual report" in p for p in log)


def test_a_timeline_passed_in_is_not_read_again():
    edgar = Edgar(ANNUAL, current=[CLOSING])
    known = timeline("Calderon", range(2016, 2018), get=edgar, ask=reader())
    log = []
    seller_for("Calderon", "Q2 2017", known=known, get=edgar, ask=reader(log))
    assert not any("annual report" in p for p in log)


def test_shapes():
    assert dates_in("On June 16, 2017 and 16 June 2017, Jun. 16, 2017, 2017-06-16") == {dt.date(2017, 6, 16)}
    assert short_name("CALDERON LABS CORP /DE/  (CLB)  (CIK 0000000001)") == "CALDERON LABS"
    assert nearest_quarter(dt.date(2017, 7, 2)) == "Q2 2017"
    text = "Item 2.01. Completion of Acquisition. Closed. As set out in Item 2.01 of this report. Item 3.02. Other."
    assert item_201(text) == "Item 2.01. Completion of Acquisition. Closed. As set out in Item 2.01 of this report. "


def test_quarters_of_one_year_share_one_handover_reading():
    edgar = Edgar(ANNUAL, current=[CLOSING])
    known, log = timeline("Calderon", range(2016, 2018), get=edgar, ask=reader()), []
    seller_for("Calderon", "Q2 2017", known=known, get=edgar, ask=reader(log))
    first = len(log)
    q3 = seller_for("Calderon", "Q3 2017", known=known, get=edgar, ask=reader(log))
    assert first > 0 and len(log) == first
    assert q3.evidence[0] == "On May 12, 2017, Beta Bio Corp completed its acquisition of Acme Pharma Inc."
    # a quarter one seller held carries that seller's quote, not the other's
    assert not any("Net product sales of Calderon were $120 million." == e for e in q3.evidence)
