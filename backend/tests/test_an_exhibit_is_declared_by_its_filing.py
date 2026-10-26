"""What makes a document an earnings exhibit is the filing, not the filename.

The filing's header page states the type the filer submitted each document
under. A filing agent's naming convention states nothing: the same EX-99.1 is
`ex_100200.htm` from one agent, `q4-earnings-release.htm` from another
and `acme-20260211xex991.htm` from a third, and only the last of those says
what it is in its name.

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


def _complete_submission(accession: str, documents: list[tuple[str, str]]) -> str:
    """A complete-submission .txt as EDGAR serves one: raw SGML DOCUMENT blocks."""
    blocks = "".join(
        f"<DOCUMENT>\n<TYPE>{kind}\n<SEQUENCE>{n}\n<FILENAME>{name}\n"
        f"<DESCRIPTION>{kind}\n<TEXT>\nbody\n</TEXT>\n</DOCUMENT>\n"
        for n, (kind, name) in enumerate(documents, start=1)
    )
    return f"<SEC-DOCUMENT>{accession}.txt\n{blocks}</SEC-DOCUMENT>\n"


class _Page:
    def __init__(self, text: str) -> None:
        self.text = text


def _connector(
    monkeypatch,
    filings: dict[str, list[tuple[str, str]]],
    *,
    headers_missing: frozenset[str] | None = None,
) -> SECConnector:
    """A connector whose only reachable pages are the header pages of
    ``filings``, keyed by accession, and whose documents fetch to nothing.

    ``headers_missing`` accessions 404 the header page so the complete
    submission ``.txt`` is what declaration falls back to.
    """
    connector = SECConnector(LocalFileStore("/tmp"))
    missing = headers_missing or frozenset()

    async def _get(self, client, url, *, budget_s=None):
        for accession, documents in filings.items():
            if url.endswith(f"{accession}-index-headers.html"):
                if accession in missing:
                    raise RuntimeError("404 Not Found")
                return _Page(_header_page(accession, documents))
            if url.endswith(f"{accession}.txt"):
                return _Page(_complete_submission(accession, documents))
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
    ["ex_100200.htm", "q4-earnings-release.htm", "acme-20260211xex991.htm"],
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


async def test_a_missing_header_page_falls_back_to_the_complete_submission(monkeypatch):
    """Older filings 404 the index-headers page; the complete submission .txt
    still carries TYPE and FILENAME, so declaration does not need a year gate."""
    connector = _connector(
        monkeypatch,
        {ACME_RELEASE: [("8-K", "acme.htm"), ("EX-99.1", "ex_100200.htm")]},
        headers_missing=frozenset({ACME_RELEASE}),
    )
    sources = await _exhibits(
        connector,
        _index(("8-K", ACME_RELEASE, "2026-02-11", "2.02,9.01")),
        since=date(2026, 1, 1),
        until=date(2026, 3, 31),
    )
    assert [s.metadata["exhibit_document"] for s in sources] == ["ex_100200.htm"]


async def test_a_six_k_after_quarter_end_is_taken_with_its_primary_and_exhibits(
    monkeypatch,
):
    """Foreign issuers furnish results on Form 6-K with no item 2.02. The
    release is the primary document and/or EX-99; selection is by the
    filing's reportDate matching an asked quarter end."""
    accession = "0000000001-26-000010"
    connector = _connector(
        monkeypatch,
        {
            accession: [
                ("6-K", "acme-results.htm"),
                ("EX-99.1", "acme-schedules.htm"),
            ]
        },
    )
    # Q1 2026 ends 2026-03-31; reportDate is that period, filed after it.
    filed = date(2026, 5, 1)
    recent = {
        "form": ["6-K"],
        "accessionNumber": [accession],
        "filingDate": [filed.isoformat()],
        "reportDate": ["2026-03-31"],
        "items": [""],
        "primaryDocument": ["acme-results.htm"],
    }
    sources = await connector._retrieve_earnings_exhibits(
        None, run_id="r", job_id="j", cik="0000000001", recent=recent,
        max_exhibits=6, since=date(2026, 1, 1), until=date(2026, 8, 1),
        asked=["2026Q1"],
    )
    docs = sorted(
        s.metadata.get("exhibit_document") or s.metadata.get("primary_document")
        for s in sources
    )
    assert docs == ["acme-results.htm", "acme-schedules.htm"]


async def test_a_six_k_report_date_the_day_after_quarter_end_is_still_taken(
    monkeypatch,
):
    """A 52/53-week close stated as April 1 is still Q1's results filing."""
    accession = "0000000001-26-000015"
    connector = _connector(
        monkeypatch,
        {
            accession: [
                ("6-K", "acme-results.htm"),
                ("EX-99.1", "acme-schedules.htm"),
            ]
        },
    )
    recent = {
        "form": ["6-K"],
        "accessionNumber": [accession],
        "filingDate": ["2026-05-01"],
        "reportDate": ["2026-04-01"],
        "items": [""],
        "primaryDocument": ["acme-results.htm"],
    }
    sources = await connector._retrieve_earnings_exhibits(
        None, run_id="r", job_id="j", cik="0000000001", recent=recent,
        max_exhibits=6, since=date(2026, 1, 1), until=date(2026, 8, 1),
        asked=["2026Q1"],
    )
    docs = sorted(
        s.metadata.get("exhibit_document") or s.metadata.get("primary_document")
        for s in sources
    )
    assert docs == ["acme-results.htm", "acme-schedules.htm"]


async def test_a_six_k_far_from_any_asked_quarter_is_skipped(monkeypatch):
    accession = "0000000001-26-000011"
    connector = _connector(
        monkeypatch,
        {accession: [("6-K", "acme-other.htm"), ("EX-99.1", "acme-news.htm")]},
    )
    recent = {
        "form": ["6-K"],
        "accessionNumber": [accession],
        "filingDate": ["2026-09-21"],
        # Mid-quarter event date: not a period of report for Q3.
        "reportDate": ["2026-09-21"],
        "items": [""],
        "primaryDocument": ["acme-other.htm"],
    }
    sources = await connector._retrieve_earnings_exhibits(
        None, run_id="r", job_id="j", cik="0000000001", recent=recent,
        max_exhibits=6, since=date(2026, 1, 1), until=date(2026, 12, 31),
        asked=["2026Q3"],
    )
    assert sources == []


async def test_a_declared_annual_report_exhibit_is_fetched(monkeypatch):
    """Reg S-K 601(b)(13): when an annual declares exhibit family 13, that
    document is fetched with the primary - not every 10-K has one."""
    accession = "0000000001-26-000012"
    connector = _connector(
        monkeypatch,
        {
            accession: [
                ("10-K", "acme-10k.htm"),
                ("EX-13", "acme-annual-report.htm"),
            ]
        },
    )
    sources = await connector._annual_report_exhibits(
        None,
        cik="0000000001",
        accession=accession,
        form="10-K",
        filed="2026-02-20",
        run_id="r",
        job_id="j",
    )
    assert [s.metadata["exhibit_document"] for s in sources] == [
        "acme-annual-report.htm"
    ]
    assert all(s.metadata["exhibit_family"] == "13" for s in sources)


async def test_an_annual_without_exhibit_13_yields_no_annual_report_exhibit(
    monkeypatch,
):
    accession = "0000000001-26-000013"
    connector = _connector(
        monkeypatch,
        {accession: [("10-K", "acme-10k.htm"), ("EX-101.INS", "acme_htm.xml")]},
    )
    sources = await connector._annual_report_exhibits(
        None,
        cik="0000000001",
        accession=accession,
        form="10-K",
        filed="2026-02-20",
        run_id="r",
        job_id="j",
    )
    assert sources == []


async def test_exhibit_99_on_an_annual_is_not_an_annual_report_exhibit(monkeypatch):
    """EX-99 on a 10-K is a different disclosure class; family 13 is the gate."""
    accession = "0000000001-26-000014"
    connector = _connector(
        monkeypatch,
        {
            accession: [
                ("10-K", "acme-10k.htm"),
                ("EX-99.1", "acme-other.htm"),
            ]
        },
    )
    sources = await connector._annual_report_exhibits(
        None,
        cik="0000000001",
        accession=accession,
        form="10-K",
        filed="2026-02-20",
        run_id="r",
        job_id="j",
    )
    assert sources == []


async def test_six_k_budget_follows_asked_quarters_not_the_floor(monkeypatch):
    """Seven asked quarters keep the seventh period even when max_exhibits is 6."""
    filings = {
        f"0000000001-26-0000{n:02d}": [
            ("6-K", f"acme-q{n}.htm"),
            ("EX-99.1", f"acme-q{n}-ex.htm"),
        ]
        for n in range(1, 8)
    }
    connector = _connector(monkeypatch, filings)
    ends = [
        "2024-06-30", "2024-09-30", "2024-12-31",
        "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
    ]
    asked = [
        "2024Q2", "2024Q3", "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4",
    ]
    recent = {
        "form": ["6-K"] * 7,
        "accessionNumber": list(filings),
        "filingDate": [
            "2024-08-01", "2024-11-01", "2025-02-01",
            "2025-05-01", "2025-08-01", "2025-11-01", "2026-02-01",
        ],
        "reportDate": ends,
        "items": [""] * 7,
        "primaryDocument": [f"acme-q{n}.htm" for n in range(1, 8)],
    }
    sources = await connector._retrieve_earnings_exhibits(
        None, run_id="r", job_id="j", cik="0000000001", recent=recent,
        max_exhibits=6, since=date(2024, 1, 1), until=date(2026, 6, 1),
        asked=asked,
    )
    primaries = {
        s.metadata.get("primary_document")
        for s in sources
        if s.metadata.get("primary_document")
    }
    assert "acme-q7.htm" in primaries
    assert len(primaries) == 7


async def test_six_k_html_exhibit_twin_is_kept_beside_pdf(monkeypatch):
    """Same exact reportDate: HTML EX-99 and PDF-only twin both kept."""
    html_acc = "0000000001-26-000020"
    pdf_acc = "0000000001-26-000021"
    connector = _connector(
        monkeypatch,
        {
            html_acc: [("6-K", "acme.htm"), ("EX-99.1", "acme-99.htm")],
            pdf_acc: [("6-K", "acme.pdf"), ("EX-99.1", "acme-99.pdf")],
        },
    )
    recent = {
        "form": ["6-K", "6-K"],
        "accessionNumber": [pdf_acc, html_acc],
        "filingDate": ["2026-02-10", "2026-02-11"],
        "reportDate": ["2025-12-31", "2025-12-31"],
        "items": ["", ""],
        "primaryDocument": ["acme.pdf", "acme.htm"],
    }
    sources = await connector._retrieve_earnings_exhibits(
        None, run_id="r", job_id="j", cik="0000000001", recent=recent,
        max_exhibits=6, since=date(2025, 1, 1), until=date(2026, 6, 1),
        asked=["2025Q4"],
    )
    exhibits = {s.metadata.get("exhibit_document") for s in sources}
    assert "acme-99.htm" in exhibits
    assert "acme-99.pdf" in exhibits


async def test_six_k_exact_report_date_beats_a_snap_match(monkeypatch):
    """An event date that snaps to the quarter end loses to the exact end."""
    exact = "0000000001-26-000030"
    snap = "0000000001-26-000031"
    connector = _connector(
        monkeypatch,
        {
            exact: [("6-K", "acme-exact.htm"), ("EX-99.1", "exact-ex.htm")],
            snap: [("6-K", "acme-snap.htm"), ("EX-99.1", "snap-ex.htm")],
        },
    )
    recent = {
        "form": ["6-K", "6-K"],
        "accessionNumber": [snap, exact],
        "filingDate": ["2026-05-05", "2026-05-10"],
        "reportDate": ["2026-04-01", "2026-03-31"],
        "items": ["", ""],
        "primaryDocument": ["acme-snap.htm", "acme-exact.htm"],
    }
    sources = await connector._retrieve_earnings_exhibits(
        None, run_id="r", job_id="j", cik="0000000001", recent=recent,
        max_exhibits=6, since=date(2026, 1, 1), until=date(2026, 8, 1),
        asked=["2026Q1"],
    )
    primaries = {
        s.metadata.get("primary_document")
        for s in sources
        if s.metadata.get("primary_document")
    }
    assert primaries == {"acme-exact.htm"}


async def test_six_k_keeps_earliest_exact_html_releases(monkeypatch):
    """Several exact HTML EX-99 filings share a quarter: keep the earliest few."""
    early = "0000000001-26-000060"
    mid = "0000000001-26-000061"
    late = "0000000001-26-000062"
    connector = _connector(
        monkeypatch,
        {
            early: [("6-K", "acme-early.htm"), ("EX-99.1", "early-ex.htm")],
            mid: [("6-K", "acme-mid.htm"), ("EX-99.1", "mid-ex.htm")],
            late: [("6-K", "acme-late.htm"), ("EX-99.1", "late-ex.htm")],
        },
    )
    recent = {
        "form": ["6-K", "6-K", "6-K"],
        "accessionNumber": [late, mid, early],
        "filingDate": ["2026-10-27", "2026-07-31", "2026-07-30"],
        "reportDate": ["2026-06-30", "2026-06-30", "2026-06-30"],
        "items": ["", "", ""],
        "primaryDocument": ["acme-late.htm", "acme-mid.htm", "acme-early.htm"],
    }
    sources = await connector._retrieve_earnings_exhibits(
        None, run_id="r", job_id="j", cik="0000000001", recent=recent,
        max_exhibits=6, since=date(2026, 1, 1), until=date(2026, 12, 31),
        asked=["2026Q2"],
    )
    exhibits = {
        s.metadata.get("exhibit_document")
        for s in sources
        if s.metadata.get("exhibit_document")
    }
    assert exhibits == {"early-ex.htm", "mid-ex.htm", "late-ex.htm"}


async def test_six_k_without_exhibit_99_keeps_earliest_exact_primaries(monkeypatch):
    """FPI results often live on the primary with no EX-99; keep earliest exact."""
    first = "0000000001-26-000070"
    second = "0000000001-26-000071"
    connector = _connector(
        monkeypatch,
        {
            first: [("6-K", "acme-results.htm")],
            second: [("6-K", "acme-other.htm")],
        },
    )
    recent = {
        "form": ["6-K", "6-K"],
        "accessionNumber": [second, first],
        "filingDate": ["2026-02-04", "2026-02-03"],
        "reportDate": ["2025-12-31", "2025-12-31"],
        "items": ["", ""],
        "primaryDocument": ["acme-other.htm", "acme-results.htm"],
    }
    sources = await connector._retrieve_earnings_exhibits(
        None, run_id="r", job_id="j", cik="0000000001", recent=recent,
        max_exhibits=6, since=date(2025, 1, 1), until=date(2026, 6, 1),
        asked=["2025Q4"],
    )
    primaries = {
        s.metadata.get("primary_document")
        for s in sources
        if s.metadata.get("primary_document")
    }
    assert "acme-results.htm" in primaries
    assert "acme-other.htm" in primaries


async def test_an_amended_nine_oh_one_with_exhibit_99_is_fetched(monkeypatch):
    """8-K/A item 9.01 that declares EX-99 is an earnings disclosure."""
    accession = "0000000001-26-000040"
    connector = _connector(
        monkeypatch,
        {accession: [("8-K/A", "acme.htm"), ("EX-99.2", "acme-fin.htm")]},
    )
    sources = await _exhibits(
        connector,
        _index(("8-K/A", accession, "2026-02-11", "9.01")),
        since=date(2026, 1, 1),
        until=date(2026, 3, 31),
    )
    assert [s.metadata["exhibit_document"] for s in sources] == ["acme-fin.htm"]


async def test_a_plain_nine_oh_one_without_amendment_is_skipped(monkeypatch):
    """Non-amendment item 9.01 alone is not opened."""
    accession = "0000000001-26-000041"
    connector = _connector(
        monkeypatch,
        {accession: [("8-K", "acme.htm"), ("EX-99.1", "acme-other.htm")]},
    )
    sources = await _exhibits(
        connector,
        _index(("8-K", accession, "2026-02-11", "9.01")),
        since=date(2026, 1, 1),
        until=date(2026, 3, 31),
    )
    assert sources == []


async def test_yoy_comparative_earnings_release_is_in_reach(monkeypatch):
    """Asked Q1 2024 also admits the Q1 2025 2.02 release (prior-year column)."""
    accession = "0000000001-26-000050"
    connector = _connector(
        monkeypatch,
        {accession: [("8-K", "acme.htm"), ("EX-99.1", "acme-2025q1.htm")]},
    )
    # Filed after 2025-03-31 within REPORTING_LAG; outside a 2024-only window.
    sources = await _exhibits(
        connector,
        _index(("8-K", accession, "2025-05-01", "2.02")),
        since=date(2024, 1, 1),
        until=date(2024, 6, 30),
        asked=["2024Q1"],
    )
    assert [s.metadata["exhibit_document"] for s in sources] == ["acme-2025q1.htm"]


def test_yoy_earnings_bounds_follow_the_asked_quarter():
    from app.connectors.sources import REPORTING_LAG, yoy_earnings_filing_bounds

    bounds = yoy_earnings_filing_bounds(["2024Q1"])
    assert bounds == [(date(2025, 3, 31), date(2025, 3, 31) + REPORTING_LAG)]


def test_stated_quarters_of_an_annual_reach_back_three_years():
    from app.connectors.sources import _stated_quarter_indices, quarter_index

    stated = _stated_quarter_indices("10-K", date(2025, 12, 31))
    assert quarter_index(date(2025, 12, 31)) in stated
    assert quarter_index(date(2023, 12, 31)) in stated
    assert quarter_index(date(2022, 12, 31)) not in stated
