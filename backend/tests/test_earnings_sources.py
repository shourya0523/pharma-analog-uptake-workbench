import json
from pathlib import Path

from app.connectors.sources import SECConnector, is_earnings_exhibit
from app.domain.models import (
    ParsedDocument,
    ParsingStatus,
    RetrievalStatus,
    RetrievedSource,
    SourceType,
)
from app.parsing.evidence import prioritize_sources_for_revenue

REPO_ROOT = Path(__file__).resolve().parents[2]


def _gold_source_filenames() -> set[str]:
    rows = [
        json.loads(line)
        for line in (REPO_ROOT / "seed" / "gold" / "quarterly_revenue.jsonl").read_text().splitlines()
        if line.strip()
    ]
    return {row["source_url"].rsplit("/", 1)[-1] for row in rows}


def test_earnings_exhibit_matches_every_gold_exhibit_filename():
    """Gold rows cite exhibit 99.x documents under several issuer naming conventions."""
    exhibits = {name for name in _gold_source_filenames() if "ex" in name.lower()}
    assert exhibits, "expected gold rows to cite exhibit documents"
    assert all(is_earnings_exhibit(name) for name in exhibits), sorted(
        name for name in exhibits if not is_earnings_exhibit(name)
    )


def test_earnings_exhibit_rejects_filing_boilerplate():
    # Primary 8-K document, XBRL viewer pages, and filing metadata are not earnings exhibits
    assert not is_earnings_exhibit("uthr-20240501.htm")
    assert not is_earnings_exhibit("R39.htm")
    assert not is_earnings_exhibit("FilingSummary.xml")
    assert not is_earnings_exhibit("0001082554-24-000027-index.html")
    assert not is_earnings_exhibit("ut_lungiconxredxlogo.jpg")
    assert not is_earnings_exhibit("")


def test_earnings_item_is_results_of_operations():
    assert SECConnector.EARNINGS_ITEM == "2.02"


def _source(source_id: str, source_type: SourceType, **kwargs) -> RetrievedSource:
    return RetrievedSource(
        source_id=source_id,
        source_type=source_type,
        url=f"https://www.sec.gov/{source_id}",
        retrieval_status=RetrievalStatus.SUCCESS,
        **kwargs,
    )


def _parsed(*source_ids: str) -> dict[str, ParsedDocument]:
    return {
        sid: ParsedDocument(
            source_id=sid,
            text_blocks=["Total Tyvaso net product sales $372.5 million"],
            parsing_status=ParsingStatus.SUCCESS,
        )
        for sid in source_ids
    }


def test_earnings_releases_outrank_annual_filings_for_revenue():
    tenk = _source("tenk", SourceType.SEC_FILING, filing_type="10-K")
    tenq = _source("tenq", SourceType.SEC_FILING, filing_type="10-Q")
    exhibit = _source("ex991", SourceType.EARNINGS_RELEASE, filing_type="8-K")
    ordered = prioritize_sources_for_revenue(
        [tenk, tenq, exhibit], _parsed("tenk", "tenq", "ex991"), max_sources=3
    )
    assert ordered[0].source_id == "ex991"


def test_earnings_releases_survive_the_primary_source_cut():
    """Two or more 10-K/10-Q filings previously discarded every earnings exhibit."""
    filings = [
        _source("tenk1", SourceType.SEC_FILING, filing_type="10-K"),
        _source("tenk2", SourceType.SEC_FILING, filing_type="10-K"),
        _source("tenq1", SourceType.SEC_FILING, filing_type="10-Q"),
    ]
    exhibits = [
        _source("ex1", SourceType.EARNINGS_RELEASE, filing_type="8-K"),
        _source("ex2", SourceType.EARNINGS_RELEASE, filing_type="8-K"),
    ]
    parsed = _parsed("tenk1", "tenk2", "tenq1", "ex1", "ex2")
    ordered = prioritize_sources_for_revenue(filings + exhibits, parsed, max_sources=4)
    selected = {s.source_id for s in ordered}
    assert {"ex1", "ex2"} <= selected


def test_openfda_still_excluded_from_revenue_sources():
    fda = _source("fda", SourceType.OPENFDA)
    exhibit = _source("ex991", SourceType.EARNINGS_RELEASE, filing_type="8-K")
    ordered = prioritize_sources_for_revenue([fda, exhibit], _parsed("fda", "ex991"), max_sources=4)
    assert [s.source_id for s in ordered] == ["ex991"]


def test_a_company_name_never_resolves_to_the_nearest_registrant():
    """Ambiguity must produce no answer, not the first alphabetical match.

    An unanchored substring search resolved "United" to an unrelated registrant.
    Every figure read from that company's filings would then have been filed
    under United Therapeutics, and nothing downstream could have noticed.
    """
    from app.connectors.sources import normalize_registrant

    assert normalize_registrant("Gilead Sciences, Inc.") == "gilead sciences"
    assert normalize_registrant("Gilead Sciences Inc") == "gilead sciences"
    # The suffix and the ampersand carry no identity either.
    assert normalize_registrant("MERCK & CO., INC.") == "merck"
    assert normalize_registrant("Merck & Co Inc") == "merck"
    # A bare first word is not the company, so it cannot match one.
    assert normalize_registrant("United") != normalize_registrant(
        "United Therapeutics Corp"
    )


async def test_the_budget_counts_filings_so_a_filing_is_never_split(monkeypatch):
    """A quarter's exhibits are one disclosure; the cap must not cut between them.

    A filer attaches two EX-99 documents to each earnings 8-K, the press
    release and the product-sales schedule. While the budget counted exhibits,
    it could spend its last on a press release and leave behind the schedule
    that belongs with it, so the quarter was retrieved and still unreadable.
    """
    from app.connectors.sources import SECConnector
    from app.storage.filestore import LocalFileStore

    connector = SECConnector(LocalFileStore("/tmp"))

    async def _documents(self, client, cik_int, acc_nodash):
        return [f"{acc_nodash}exhibit991.htm", f"{acc_nodash}exhibit992.htm"]

    async def _fetch(self, client, *, url, accession, doc, run_id, job_id, source_id):
        return b"<html></html>", False, f"key/{doc}"

    monkeypatch.setattr(SECConnector, "_list_filing_documents", _documents)
    monkeypatch.setattr(SECConnector, "_fetch_document", _fetch)

    quarters = ["2019-10-15", "2019-07-16", "2019-04-16", "2019-01-22"]
    recent = {
        "form": ["8-K"] * len(quarters),
        "accessionNumber": [f"000020040619-{n:06d}" for n in range(len(quarters))],
        "filingDate": quarters,
        "items": ["2.02"] * len(quarters),
    }

    sources = await connector._retrieve_earnings_exhibits(
        None, run_id="r", job_id="j", cik="0000200406", recent=recent, max_exhibits=3
    )

    by_accession: dict[str, int] = {}
    for source in sources:
        by_accession[source.accession_number] = by_accession.get(source.accession_number, 0) + 1

    assert len(by_accession) == 3, "the budget is three filings, not three documents"
    assert set(by_accession.values()) == {2}, (
        "every filing taken must bring both of its exhibits - taking the press "
        "release without the schedule retrieves the quarter and cannot read it"
    )


async def test_primary_filings_respect_the_window_they_were_fetched_for(monkeypatch):
    """A job for 2005 must not be handed the 2026 annual report.

    `_filings_covering` merges EDGAR's archive shards precisely so an older
    quarter can be reached - its docstring says a 2005 series otherwise
    "retrieves nothing at all and reports it as no relevant filings". The
    window was then applied to the earnings exhibits and to the XBRL instances
    and skipped here, so this loop took the newest 10-K and 10-Q on the merged
    list every time.

    What it cost: a 10-Q from 2005 carries a table row the existing table
    reader parses correctly. Those quarters were not unreachable, they were
    unqueried, and the prose reader filled the gap with total company revenues
    from the 8-K instead.
    """
    from datetime import date

    from app.connectors.sources import SECConnector
    from app.storage.filestore import LocalFileStore

    connector = SECConnector(LocalFileStore("/tmp"))
    fetched: list[str] = []

    async def _fetch(self, client, *, url, accession, doc, run_id, job_id, source_id):
        fetched.append(doc)
        return b"<html></html>", False, f"key/{doc}"

    async def _covering(self, client, payload, cik, since, until):
        # Newest first, exactly as EDGAR returns and as the merge leaves it.
        return {
            "form": ["10-K", "10-Q", "10-K", "10-Q"],
            "accessionNumber": [f"000108255{n}-05-00000{n}" for n in range(4)],
            "filingDate": ["2026-02-25", "2026-05-01", "2005-02-25", "2005-08-03"],
            "primaryDocument": ["fy2026.htm", "q2026.htm", "fy2005.htm", "q2005.htm"],
            "items": ["", "", "", ""],
        }

    monkeypatch.setattr(SECConnector, "_filings_covering", _covering)
    monkeypatch.setattr(SECConnector, "_fetch_document", _fetch)

    sources = await connector.retrieve(
        run_id="r", job_id="j", cik="0001082554", ticker=None, company_name=None,
        include_primary=True, include_earnings=False, include_xbrl=False,
        earnings_since=date(2005, 4, 5), earnings_until=date(2005, 8, 30),
    )
    assert sources  # the call itself must still work

    assert "q2005.htm" in fetched, "the 10-Q covering the window is the point"
    assert "fy2005.htm" in fetched, "a 10-K filed in February reports the year before"
    assert "fy2026.htm" not in fetched, (
        "a job for 2005 was handed the 2026 annual report, which says nothing "
        "about 2005"
    )
    assert "q2026.htm" not in fetched



def test_every_edgar_read_survives_a_dropped_connection_or_a_refusal(monkeypatch):
    """EDGAR refuses with 503 under load and sometimes drops the connection
    instead; either, unretried, reads downstream as an issuer with no filings.
    The submissions index - the whole of what EDGAR knows about an issuer -
    went through no retry at all."""
    import asyncio

    import httpx
    import pytest

    from app.connectors import sources as module
    from app.connectors.sources import SECConnector

    async def _now(*_a, **_k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _now)
    monkeypatch.setattr(module, "_sec_throttle", _now)

    class Flaky:
        def __init__(self, script):
            self.script = list(script)
            self.calls = 0

        async def get(self, url):
            self.calls += 1
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return httpx.Response(step, request=httpx.Request("GET", url), json={"ok": True})

    connector = SECConnector.__new__(SECConnector)
    client = Flaky([httpx.ConnectError(""), 503, 200])
    response = asyncio.run(connector._get_with_retry(client, "https://data.sec.gov/submissions/CIK0000000001.json"))
    assert response.status_code == 200 and client.calls == 3

    client = Flaky([httpx.ConnectError("")] * 4)
    with pytest.raises(httpx.ConnectError):
        asyncio.run(connector._get_with_retry(client, "https://data.sec.gov/x", attempts=4))
    assert client.calls == 4

    # And the three reads that used to call the client directly now go
    # through it: the ticker map, the submissions index, and its archive shards.
    import inspect

    for name in ("resolve_cik", "_filings_covering", "_retrieve_xbrl_instances", "retrieve"):
        fn = getattr(SECConnector, name, None)
        if fn is not None:
            assert "client.get(" not in inspect.getsource(fn), name
