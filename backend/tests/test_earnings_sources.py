from app.connectors.sources import (
    SECConnector,
    exhibit_number,
    form_family,
    states_item,
)
from app.domain.models import (
    ParsedDocument,
    ParsingStatus,
    RetrievalStatus,
    RetrievedSource,
    SourceType,
)
from app.parsing.evidence import prioritize_sources_for_revenue


def test_an_exhibit_is_the_family_its_declared_type_names():
    """One exhibit family, three spellings, and a neighbour that is not it.

    A filer writes the type ``EX-99``, ``EX-99.1`` or ``EX-99.01`` for the
    same exhibit, so the family is what is read and the numbering after it is
    the filer's own. Exhibit 13 is the annual report filed with a 10-K, which
    also carries product revenue and is not an earnings release; exhibit 101
    is the XBRL taxonomy. Neither is in the 99 family.
    """
    assert {exhibit_number(t) for t in ("EX-99", "EX-99.1", "EX-99.01", "ex-99.2")} == {
        SECConnector.EARNINGS_EXHIBIT
    }
    assert exhibit_number("EX-13") == "13"
    assert exhibit_number("EX-101.INS") == "101"
    # The filing's own primary document, its graphics and its viewer pages are
    # declared under no exhibit at all.
    assert all(
        exhibit_number(kind) is None
        for kind in ("8-K", "10-Q", "GRAPHIC", "XML", "JSON", "ZIP", "", None)
    )


def test_earnings_item_is_results_of_operations():
    """The constant is the SEC's own code for "Results of Operations and
    Financial Condition", and the gate is what it is for.

    Pinning the string says nothing about which filings are read: the gate
    also has to find the code in a filing's item list and accept the filing
    it sits on. `2.02` is furnished on the 8-K family, an amendment included,
    and it is a whole entry in a comma-separated list rather than a substring
    of one - `12.02` is not this item.
    """
    assert SECConnector.EARNINGS_ITEM == "2.02"
    assert SECConnector.EARNINGS_FORM == "8-K"
    assert states_item("2.02,9.01", SECConnector.EARNINGS_ITEM)
    assert not states_item("12.02", SECConnector.EARNINGS_ITEM)
    assert form_family("8-K/A") == form_family(SECConnector.EARNINGS_FORM)


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

    async def _declared(self, client, cik_int, accession):
        return [
            ("8-K", f"{accession}.htm"),
            ("EX-99.1", f"{accession}-release.htm"),
            ("EX-99.2", f"{accession}-schedules.htm"),
        ]

    async def _fetch(self, client, *, url, accession, doc, run_id, job_id, source_id):
        return b"<html></html>", False, f"key/{doc}"

    monkeypatch.setattr(SECConnector, "_declared_documents", _declared)
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

    # A refusal that never lets up ends as the error it was, once the budget
    # for that one document is spent. The budget is time, not a count of
    # attempts: four attempts with a doubling delay gave up after seven
    # seconds, which is nothing to a rate limit, and the filing was then
    # recorded as one the issuer had never made.
    client = Flaky([httpx.ConnectError("")] * 50)
    with pytest.raises(httpx.ConnectError):
        asyncio.run(connector._get_with_retry(client, "https://data.sec.gov/x", budget_s=0))
    assert client.calls == 1


def test_every_read_of_a_page_in_the_module_goes_through_the_backoff():
    """Derived from the module, not from a list of the reads we remember.

    A list of method names looked up in `vars(SECConnector)` cannot see a
    function that is not a method, and `fetch_page` is not a method - so an
    unthrottled read living there is invisible to a guard written that way,
    however carefully the list is kept. Both sides of the assertion here are
    read out of the module's own syntax tree, so a read added anywhere in the
    file - method, module-level function or nested - has to be the throttled
    one or this fails.
    """
    import ast
    import inspect

    from app.connectors import sources as module

    text = inspect.getsource(module)
    tree = ast.parse(text)
    functions = {
        node.name: ast.get_source_segment(text, node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    reads = {name for name, body in functions.items() if "client.get(" in body}
    paced = {name for name, body in functions.items() if "await _sec_throttle(" in body}
    assert reads, "no function in the module reads a page; the check would pass vacuously"
    assert reads == paced, (sorted(reads), sorted(paced))


async def test_the_window_reaches_one_reporting_lag_back_and_no_further(monkeypatch):
    """A filing reports a period that ended before it, so the window is widened
    by one reporting lag at each end - not by a year at one end.

    Reaching 400 days back fetched filings that can only report periods well
    before anything the window asks for, which was two fifths of every job's
    retrieval against a rate-limited endpoint.
    """
    from datetime import date, timedelta

    from app.connectors import sources as module
    from app.connectors.sources import SECConnector
    from app.storage.filestore import LocalFileStore

    connector = SECConnector(LocalFileStore("/tmp"))
    fetched: list[str] = []

    async def _fetch(self, client, *, url, accession, doc, run_id, job_id, source_id):
        fetched.append(doc)
        return b"<html></html>", False, f"key/{doc}"

    since, until = date(2005, 4, 5), date(2005, 8, 30)
    lag = module.REPORTING_LAG.days
    # One day inside each bound and one day outside it, so the test moves with
    # the constant instead of restating the dates it happens to produce.
    inside_back = since - timedelta(days=lag - 1)
    outside_back = since - timedelta(days=lag + 1)
    inside_fwd = until + timedelta(days=lag - 1)
    outside_fwd = until + timedelta(days=lag + 1)

    async def _covering(self, client, payload, cik, _since, _until):
        return {
            "form": ["10-K", "10-K", "10-Q", "10-Q"],
            "accessionNumber": [f"000108255{n}-05-00000{n}" for n in range(4)],
            "filingDate": [d.isoformat() for d in
                           (inside_back, outside_back, inside_fwd, outside_fwd)],
            "primaryDocument": ["in_back.htm", "out_back.htm", "in_fwd.htm", "out_fwd.htm"],
            "items": ["", "", "", ""],
        }

    monkeypatch.setattr(SECConnector, "_filings_covering", _covering)
    monkeypatch.setattr(SECConnector, "_fetch_document", _fetch)

    await connector.retrieve(
        run_id="r", job_id="j", cik="0001082554", ticker=None, company_name=None,
        include_primary=True, include_earnings=False, include_xbrl=False,
        earnings_since=since, earnings_until=until,
    )
    assert "in_back.htm" in fetched, "a report filed just before the window still reports into it"
    assert "in_fwd.htm" in fetched, "a period ending inside the window is reported after it closes"
    assert "out_back.htm" not in fetched, "a year back reports periods nothing asked for"
    assert "out_fwd.htm" not in fetched
