"""What the pipeline can read is decided by the window, not by a number.

Two written-down budgets decided which filings a job could read. Every 8-K
since 2019 carries an inline-XBRL cover page, so the instance budget of six,
taken newest-first by EDGAR's `isXBRL` flag, was filled by cover pages and
the 10-Qs were never fetched; the exhibit budget of six dropped the oldest
quarter of a thirteen-month window whenever an issuer furnished other item
2.02 filings in between.

Inside a window the window is the bound: one periodic report per period it
covers, every earnings filing it holds. An instance is taken for what it
carries - a tagged number under any namespace but the cover page's - and a
cover page counts for nothing.
"""

from __future__ import annotations

from datetime import date

from app.connectors.sources import (
    ANNUAL_FORMS,
    SECConnector,
    _holds_financial_facts,
    is_annual,
    reports_a_period,
)
from app.storage.filestore import LocalFileStore

COVER_PAGE = (
    b'<xbrl xmlns:dei="http://xbrl.sec.gov/dei/2024">'
    b'<dei:DocumentType contextRef="c">8-K</dei:DocumentType>'
    b'<dei:EntityCommonStockSharesOutstanding contextRef="c">100</dei:EntityCommonStockSharesOutstanding>'
    b"</xbrl>"
)
QUARTERLY_REPORT = (
    b'<xbrl xmlns:dei="http://xbrl.sec.gov/dei/2024" xmlns:us-gaap="http://fasb.org/us-gaap/2024">'
    b'<dei:DocumentType contextRef="c">10-Q</dei:DocumentType>'
    b'<us-gaap:Revenues contextRef="c" unitRef="usd" decimals="-3">100000</us-gaap:Revenues>'
    b"</xbrl>"
)


def test_an_instance_is_taken_for_what_it_states():
    assert not _holds_financial_facts(COVER_PAGE)
    assert _holds_financial_facts(QUARTERLY_REPORT)
    assert _holds_financial_facts(b'<acme:CalderonSales contextRef="c">5</acme:CalderonSales>')


def test_every_form_this_module_calls_annual_reports_a_period():
    """The two vocabularies cannot disagree, because one is built from the other.

    Written out separately they did: the annual set named `10-K405` and
    `10-KT`, the periodic list did not, and a filer's transition-period
    annual report was skipped as though it reported nothing.
    """
    assert all(reports_a_period(form) for form in ANNUAL_FORMS)
    assert all(is_annual(form) for form in ANNUAL_FORMS)


def test_a_form_is_read_as_its_family_so_an_amendment_travels_with_it():
    """`8-K/A` is the shape that started this rule: a list naming `8-K` and
    `10-Q` excluded the amendment carrying a company's financials."""
    assert reports_a_period("10-K/A") and is_annual("10-K/A")
    assert reports_a_period("10-QT") and not is_annual("10-QT")
    assert reports_a_period("10-Q/A") and not is_annual("10-Q/A")


def test_a_form_that_states_no_period_of_its_own_is_not_periodic():
    assert not any(reports_a_period(f) for f in ("8-K", "8-K/A", "DEF 14A", "S-8", "4", None))
    # `11-K` is a benefit plan's own annual report, and it rides with the
    # annual vocabulary rather than being written out of it here. It costs a
    # listing and is then dropped by what its instance states, which is the
    # same test every other filing passes.
    assert reports_a_period("11-K")


async def test_cover_pages_do_not_fill_the_instance_budget(monkeypatch):
    connector = SECConnector(LocalFileStore("/tmp"))
    listed: list[str] = []

    async def _documents(self, client, cik_int, acc_nodash):
        listed.append(acc_nodash)
        return [f"acme-{acc_nodash}.xsd", f"acme-{acc_nodash}_htm.xml"]

    async def _fetch(self, client, *, url, accession, doc, run_id, job_id, source_id):
        return (COVER_PAGE if "cover" in accession else QUARTERLY_REPORT), False, f"key/{doc}"

    monkeypatch.setattr(SECConnector, "_list_filing_documents", _documents)
    monkeypatch.setattr(SECConnector, "_fetch_document", _fetch)

    # Newest first, as EDGAR lists them: seven cover-page filings before the
    # first 10-Q, every one of them flagged as carrying XBRL.
    forms = ["8-K", "DEF 14A", "8-K", "S-8", "8-K", "S-1", "8-K", "10-Q", "10-Q", "10-K", "10-Q"]
    recent = {
        "form": forms,
        "accessionNumber": [f"0001-24-cover{n}" if f not in ("10-Q", "10-K") else f"0001-24-report{n}"
                            for n, f in enumerate(forms)],
        "filingDate": ["2024-11-01", "2024-10-20", "2024-10-01", "2024-09-15", "2024-09-01",
                       "2024-08-20", "2024-08-10", "2024-08-05", "2024-05-05", "2024-02-25", "2023-11-05"],
        "isXBRL": [1] * len(forms),
    }
    sources = await connector._retrieve_xbrl_instances(
        None, run_id="r", job_id="j", cik="0000000001", recent=recent,
        max_filings=6, since=date(2023, 10, 1), until=date(2024, 12, 31),
    )
    assert [s.filing_type for s in sources] == ["10-Q", "10-Q", "10-K", "10-Q"], (
        "every periodic report in the window, and no cover page"
    )
    assert not any("cover" in acc for acc in listed), "a cover-page filing costs no listing"


async def test_a_periodic_form_whose_instance_is_only_a_cover_page_counts_for_nothing(monkeypatch):
    connector = SECConnector(LocalFileStore("/tmp"))

    async def _documents(self, client, cik_int, acc_nodash):
        return [f"acme-{acc_nodash}_htm.xml"]

    async def _fetch(self, client, *, url, accession, doc, run_id, job_id, source_id):
        return COVER_PAGE, False, f"key/{doc}"

    monkeypatch.setattr(SECConnector, "_list_filing_documents", _documents)
    monkeypatch.setattr(SECConnector, "_fetch_document", _fetch)
    recent = {"form": ["10-Q"], "accessionNumber": ["0001-24-000001"],
              "filingDate": ["2024-08-05"], "isXBRL": [1]}
    sources = await connector._retrieve_xbrl_instances(
        None, run_id="r", job_id="j", cik="0000000001", recent=recent,
        max_filings=6, since=date(2024, 1, 1), until=date(2024, 12, 31),
    )
    assert sources == []


async def test_every_earnings_filing_in_the_window_is_read(monkeypatch):
    """A thirteen-month window holds five earnings releases; three other item
    2.02 filings in between must not push the oldest quarter out."""
    connector = SECConnector(LocalFileStore("/tmp"))

    async def _declared(self, client, cik_int, accession):
        return [("EX-99.1", f"{accession}-release.htm")]

    async def _fetch(self, client, *, url, accession, doc, run_id, job_id, source_id):
        return b"<html></html>", False, f"key/{doc}"

    monkeypatch.setattr(SECConnector, "_declared_documents", _declared)
    monkeypatch.setattr(SECConnector, "_fetch_document", _fetch)
    dates = ["2016-04-19", "2016-01-26", "2016-01-12", "2015-10-13", "2015-09-30",
             "2015-07-14", "2015-06-15", "2015-04-14"]
    recent = {
        "form": ["8-K"] * len(dates),
        "accessionNumber": [f"0000200406-16-{n:06d}" for n in range(len(dates))],
        "filingDate": dates,
        "items": ["2.02"] * len(dates),
    }
    sources = await connector._retrieve_earnings_exhibits(
        None, run_id="r", job_id="j", cik="0000200406", recent=recent,
        max_exhibits=6, since=date(2015, 4, 5), until=date(2016, 5, 5),
    )
    assert len(sources) == len(dates), "inside a window, the window is the budget"
    assert "2015-04-14" in {s.source_date.isoformat() for s in sources}

    unbounded = await connector._retrieve_earnings_exhibits(
        None, run_id="r", job_id="j", cik="0000200406", recent=recent, max_exhibits=6,
    )
    assert len(unbounded) == 6, "with no window, the cap bounds a request for recent releases"
