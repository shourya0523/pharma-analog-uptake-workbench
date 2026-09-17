"""What makes a document an earnings exhibit is the filing, not the filename.

The filing's header page states the type the filer submitted each document
under. A filing agent's naming convention states nothing: the same EX-99.1 is
`ex_100200.htm` from one agent, `q4xearningsrelease.htm` from another and
`acme-20260211xex991.htm` from a third, and only the last of those says what it
is in its name.

Both answers are exercised here: a filing whose EX-99 is named without the
digits is found, a filing that declares no EX-99 yields nothing, and an EX-99
furnished under another item is not an earnings exhibit.
"""

from datetime import date

import pytest

from app.connectors.sources import REPORTING_LAG, SECConnector
from app.storage.filestore import LocalFileStore


def _header_page(accession: str, documents: list[tuple[str, str]]) -> str:
    """A filing header page as EDGAR serves one: the submission's own SGML,
    angle brackets escaped, one DOCUMENT block per document."""
    blocks = "".join(
        "&lt;DOCUMENT&gt;\n"
        f"&lt;TYPE&gt;{kind}\n"
        f"&lt;SEQUENCE&gt;{n}\n"
        f"&lt;FILENAME&gt;{name}\n"
        f"&lt;DESCRIPTION&gt;{kind}\n"
        "&lt;TEXT&gt;\n"
        f'<a href="{name}">Document {n} - file: {name}</a><br>\n'
        "&lt;/DOCUMENT&gt;\n"
        for n, (kind, name) in enumerate(documents, start=1)
    )
    return (
        f"<HTML><HEAD><TITLE>SEC EDGAR Submission {accession}</TITLE>\n"
        "<!--\n<SEC-HEADER>\n<TYPE>8-K\n<PERIOD>20260211\n</SEC-HEADER>\n-->\n"
        f"</HEAD><BODY>\n<PRE>&lt;SEC-HEADER&gt;{accession}\n{blocks}</PRE></BODY></HTML>"
    )


class _Page:
    def __init__(self, text: str) -> None:
        self.text = text


def _connector(monkeypatch, filings: dict[str, list[tuple[str, str]]]) -> SECConnector:
    """A connector whose only reachable pages are the header pages of
    ``filings``, keyed by accession, and whose documents fetch to nothing."""
    connector = SECConnector(LocalFileStore("/tmp"))

    async def _get(self, client, url, *, budget_s=None):
        for accession, documents in filings.items():
            if url.endswith(f"{accession}-index-headers.html"):
                return _Page(_header_page(accession, documents))
        raise AssertionError(f"unexpected request: {url}")

    async def _fetch(self, client, *, url, accession, doc, run_id, job_id, source_id):
        return b"<html></html>", False, f"key/{doc}"

    monkeypatch.setattr(SECConnector, "_get_with_retry", _get)
    monkeypatch.setattr(SECConnector, "_fetch_document", _fetch)
    return connector


def _index(*rows: tuple[str, str, str, str]) -> dict[str, list[str]]:
    return {
        "form": [form for form, _acc, _filed, _items in rows],
        "accessionNumber": [acc for _form, acc, _filed, _items in rows],
        "filingDate": [filed for _form, _acc, filed, _items in rows],
        "items": [items for _form, _acc, _filed, items in rows],
    }


async def _exhibits(connector, recent, **kwargs):
    return await connector._retrieve_earnings_exhibits(
        None, run_id="r", job_id="j", cik="0000000001", recent=recent,
        max_exhibits=6, **kwargs,
    )


ACME_RELEASE = "0000000001-26-000001"
ACME_NO_EXHIBIT = "0000000001-26-000002"
ACME_OTHER_ITEM = "0000000001-26-000003"


@pytest.mark.parametrize(
    "filename",
    # The two conventions the filename rule could not read, and the one it
    # could. What the filing declares is the same EX-99.1 in all three.
    ["ex_100200.htm", "q4-2025xearningsrelease.htm", "acme-20260211xex991.htm"],
)
async def test_an_exhibit_named_without_its_number_is_still_found(monkeypatch, filename):
    connector = _connector(
        monkeypatch,
        {ACME_RELEASE: [("8-K", "acme-20260211.htm"), ("EX-99.1", filename)]},
    )
    sources = await _exhibits(
        connector,
        _index(("8-K", ACME_RELEASE, "2026-02-11", "2.02,9.01")),
        since=date(2026, 1, 1),
        until=date(2026, 3, 31),
    )
    assert [s.metadata["exhibit_document"] for s in sources] == [filename]


async def test_every_exhibit_of_the_family_is_taken_not_the_first(monkeypatch):
    """A filer that separates the prose from the schedules puts the release in
    EX-99.1 and the product-level sales in EX-99.2; nothing in the numbering
    says which is which, so both are read."""
    connector = _connector(
        monkeypatch,
        {
            ACME_RELEASE: [
                ("8-K", "acme-20260211.htm"),
                ("EX-99", "acme-release.htm"),
                ("EX-99.01", "acme-schedules.htm"),
                ("EX-101.INS", "acme-20260211_htm.xml"),
                ("GRAPHIC", "acme-logo.jpg"),
            ]
        },
    )
    sources = await _exhibits(
        connector,
        _index(("8-K", ACME_RELEASE, "2026-02-11", "2.02,9.01")),
        since=date(2026, 1, 1),
        until=date(2026, 3, 31),
    )
    assert sorted(s.metadata["exhibit_document"] for s in sources) == [
        "acme-release.htm",
        "acme-schedules.htm",
    ]


async def test_a_filing_that_declares_no_exhibit_99_yields_nothing(monkeypatch):
    """The other answer: the primary document, the taxonomy and an exhibit 13
    annual report are none of them an earnings release."""
    connector = _connector(
        monkeypatch,
        {
            ACME_NO_EXHIBIT: [
                ("8-K", "acme-20260211.htm"),
                ("EX-13", "acme-annual-report.htm"),
                ("EX-101.INS", "acme-20260211_htm.xml"),
            ]
        },
    )
    sources = await _exhibits(
        connector,
        _index(("8-K", ACME_NO_EXHIBIT, "2026-02-11", "2.02,9.01")),
        since=date(2026, 1, 1),
        until=date(2026, 3, 31),
    )
    assert sources == []


async def test_an_exhibit_99_furnished_under_another_item_is_not_an_earnings_exhibit(
    monkeypatch,
):
    """An EX-99 is filed with a press release about a financing, a licence or a
    board appointment too. The item the filing is furnished under is what says
    it reports results, and the filing that states another item costs no
    request at all."""
    connector = _connector(
        monkeypatch,
        {ACME_OTHER_ITEM: [("8-K", "acme-20260301.htm"), ("EX-99.1", "acme-news.htm")]},
    )
    sources = await _exhibits(
        connector,
        _index(("8-K", ACME_OTHER_ITEM, "2026-03-01", "5.02,9.01")),
        since=date(2026, 1, 1),
        until=date(2026, 3, 31),
    )
    assert sources == []


async def test_the_exhibit_pass_reads_the_same_widened_window_the_primary_pass_does(
    monkeypatch,
):
    """A release reports a quarter that ended before it, so the release
    stating the window's last quarter is filed after the window closes. The
    primary pass widens its bounds by one reporting lag at each end for that
    reason; handed the raw window, the exhibit pass disagreed with it about
    which filings report a period the run asked for.
    """
    accession = "0000000001-26-000004"
    connector = _connector(
        monkeypatch,
        {accession: [("8-K", "acme-x.htm"), ("EX-99.1", "acme-release.htm")]},
    )
    since, until = date(2026, 1, 1), date(2026, 3, 31)
    filed = until + REPORTING_LAG / 2
    recent = _index(("8-K", accession, filed.isoformat(), "2.02,9.01"))

    class _Submissions:
        text = ""

        @staticmethod
        def json():
            return {"filings": {"recent": recent}}

    inner = SECConnector._get_with_retry

    async def _get(self, client, url, *, budget_s=None):
        if "data.sec.gov/submissions" in url:
            return _Submissions()
        return await inner(self, client, url, budget_s=budget_s)

    monkeypatch.setattr(SECConnector, "_get_with_retry", _get)

    # The window the run asked about ends before the release reporting its
    # last quarter is filed, so only a pass that widens finds the release.
    sources = await connector.retrieve(
        run_id="r", job_id="j", cik="0000000001", ticker=None, company_name=None,
        include_primary=False, include_xbrl=False, include_earnings=True,
        earnings_since=since, earnings_until=until,
    )
    assert [s.metadata.get("exhibit_document") for s in sources] == ["acme-release.htm"]
    assert await _exhibits(connector, recent, since=since, until=until) == []
