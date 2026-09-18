"""An instance and the page beside it are one filing, not two.

A filer that tags no product member files an instance that answers nothing
while the schedule naming each product sits in the page of the same accession.
Retrieval used to decide the two separately - a capped pass for pages, a
window-bounded pass for instances - so a quarter could be missed by a job that
had already spent a request on the accession holding it.

The rule is about the accession, not about the form or the filer: a filing
whose instance is worth a request has its page taken too, and a filing whose
page another pass already took is not fetched again.
"""

from __future__ import annotations

from datetime import date

from app.connectors.sources import SECConnector
from app.domain.models import SourceType
from app.storage.filestore import LocalFileStore

TAGGED = (
    b'<xbrl xmlns:dei="http://xbrl.sec.gov/dei/2024" xmlns:us-gaap="http://fasb.org/us-gaap/2024">'
    b'<us-gaap:Revenues contextRef="c" unitRef="usd" decimals="-3">100000</us-gaap:Revenues>'
    b"</xbrl>"
)

RECENT = {
    "form": ["10-Q", "10-Q", "10-K"],
    "accessionNumber": ["0000000001-25-000030", "0000000001-25-000020", "0000000001-25-000010"],
    "primaryDocument": ["calderon-20250930.htm", "calderon-20250630.htm", "calderon-20241231.htm"],
    "filingDate": ["2025-11-04", "2025-08-05", "2025-02-20"],
    "isXBRL": [1, 1, 1],
}


def _connector(monkeypatch, fetched: list[str]) -> SECConnector:
    async def _documents(self, client, cik_int, acc_nodash):
        return [f"calderon-{acc_nodash}.xsd", f"calderon-{acc_nodash}_htm.xml"]

    async def _fetch(self, client, *, url, accession, doc, run_id, job_id, source_id):
        fetched.append(doc)
        return TAGGED, False, f"key/{doc}"

    monkeypatch.setattr(SECConnector, "_list_filing_documents", _documents)
    monkeypatch.setattr(SECConnector, "_fetch_document", _fetch)
    return SECConnector(LocalFileStore("/tmp"))


async def test_the_page_of_an_accession_whose_instance_is_taken_is_taken_too(monkeypatch):
    fetched: list[str] = []
    connector = _connector(monkeypatch, fetched)
    sources = await connector._retrieve_xbrl_instances(
        None, run_id="r", job_id="j", cik="0000000001", recent=RECENT,
        max_filings=6, since=date(2025, 1, 1), until=date(2025, 12, 31),
        also_fetch_pages=True, pages_fetched=set(),
    )
    pages = {
        s.url.rsplit("/", 1)[-1] for s in sources if s.source_type == SourceType.SEC_FILING
    }
    assert pages == set(RECENT["primaryDocument"]), (
        "every accession whose instance was fetched contributed its page as well"
    )
    assert len([s for s in sources if s.accession_number == RECENT["accessionNumber"][0]]) == 2


async def test_a_page_another_pass_already_has_is_not_fetched_again(monkeypatch):
    fetched: list[str] = []
    connector = _connector(monkeypatch, fetched)
    await connector._retrieve_xbrl_instances(
        None, run_id="r", job_id="j", cik="0000000001", recent=RECENT,
        max_filings=6, since=date(2025, 1, 1), until=date(2025, 12, 31),
        also_fetch_pages=True, pages_fetched={"0000000001-25-000010"},
    )
    assert "calderon-20241231.htm" not in fetched, "the annual page was already in hand"
    assert "calderon-20250930.htm" in fetched


async def test_a_caller_that_asked_for_no_pages_gets_none(monkeypatch):
    """`include_primary=False` is a caller after tagged facts alone, and this
    pass does not decide for it that it wanted the markup too."""
    fetched: list[str] = []
    connector = _connector(monkeypatch, fetched)
    await connector._retrieve_xbrl_instances(
        None, run_id="r", job_id="j", cik="0000000001", recent=RECENT,
        max_filings=6, since=date(2025, 1, 1), until=date(2025, 12, 31),
    )
    assert not any(name in fetched for name in RECENT["primaryDocument"])
    assert fetched == [f"calderon-{a.replace('-', '')}_htm.xml" for a in RECENT["accessionNumber"]]
