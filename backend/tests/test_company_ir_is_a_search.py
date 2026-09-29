"""IR pages come from a search-and-filter, not a table of URLs.

Invented names: Calderon, Acme Pharma, ticker ACME. The connector is the
procedure: without this module nothing produces an IR URL from a company
name, so the module must actually search and drop unfit hits.
"""

from __future__ import annotations

import inspect

import pytest

from app.connectors.company_ir import CompanyIRConnector, looks_like_issuer_ir
from app.domain.models import RetrievalStatus, SourceType
from app.storage.filestore import LocalFileStore


class _Search:
    def __init__(self, results: list[dict]) -> None:
        self.results = results
        self.asked: dict | None = None

    async def web_search(self, **kwargs) -> dict:
        self.asked = kwargs
        return {"results": self.results}


def _connector(monkeypatch, results, fetches=None):
    search = _Search(results)
    connector = CompanyIRConnector(LocalFileStore("/tmp"), llm=search)
    connector.settings = connector.settings.model_copy(
        update={"enable_llm_search": True, "llm_search_max_urls": 5}
    )
    taken: list[str] = fetches if fetches is not None else []

    async def _fetch(file_store, *, run_id, job_id, source_id, url, user_agent):
        taken.append(url)
        return b"<html>Calderon</html>", "text/html", f"sources/{run_id}/{job_id}/{source_id}.html"

    monkeypatch.setattr("app.connectors.company_ir.fetch_page", _fetch)
    return connector, search, taken


def test_an_issuer_host_is_kept_and_a_news_hit_is_not():
    assert looks_like_issuer_ir(
        "https://ir.acme.example/earnings/q1-2020.htm",
        "Acme Q1 2020 earnings",
    )
    assert looks_like_issuer_ir("https://investors.acme.example/financials/q2.pdf")
    assert not looks_like_issuer_ir(
        "https://news.example/acme-stock-jumps-on-calderon-data",
        "Acme stock jumps",
    )


@pytest.mark.asyncio
async def test_search_hits_are_filtered_then_fetched(monkeypatch):
    ir = "https://ir.acme.example/earnings/q1-2020.htm"
    news = "https://news.example/acme-stock-jumps-on-calderon-data"
    connector, search, taken = _connector(
        monkeypatch,
        [
            {"url": ir, "title": "Acme Q1 2020 earnings"},
            {"url": news, "title": "Acme stock jumps"},
        ],
    )
    sources = await connector.retrieve_for_issuer(
        run_id="run",
        job_id="job",
        company_name="Acme Pharma",
        ticker="ACME",
        product="Calderon",
        aliases=["Calderon"],
    )
    assert search.asked["ticker"] == "ACME"
    assert search.asked["manufacturer"] == "Acme Pharma"
    assert search.asked["product"] == "Calderon"
    assert search.asked["goal"] == "company_ir"
    assert taken == [ir]
    assert [s.url for s in sources] == [ir]
    assert all(s.source_type == SourceType.COMPANY_IR for s in sources)
    assert all(s.retrieval_status == RetrievalStatus.SUCCESS for s in sources)


@pytest.mark.asyncio
async def test_extra_urls_are_fetched_even_when_search_is_empty(monkeypatch):
    extra = "https://ir.acme.example/history/product-sales.pdf"
    connector, search, taken = _connector(monkeypatch, [])
    sources = await connector.retrieve_for_issuer(
        run_id="run",
        job_id="job",
        company_name="Acme Pharma",
        ticker="ACME",
        product="Calderon",
        aliases=["Calderon"],
        extra_urls=[extra],
    )
    assert search.asked is not None
    assert taken == [extra]
    assert [s.url for s in sources] == [extra]


@pytest.mark.asyncio
async def test_a_locator_naming_other_quarters_is_dropped(monkeypatch):
    """Q4 in the filename is not an answer to Q1/Q2, even on an IR host."""
    q4 = "https://ir.acme.example/earningsreleaseq42020.htm"
    q1 = "https://ir.acme.example/earningsreleaseq12020.htm"
    connector, _search, taken = _connector(
        monkeypatch,
        [
            {"url": q4, "title": "Acme Q4 2020 earnings"},
            {"url": q1, "title": "Acme Q1 2020 earnings"},
        ],
    )
    sources = await connector.retrieve_for_issuer(
        run_id="run",
        job_id="job",
        company_name="Acme Pharma",
        ticker="ACME",
        product="Calderon",
        aliases=["Calderon"],
        asked_quarters=["2020Q1", "2020Q2"],
    )
    assert taken == [q1]
    assert [s.url for s in sources] == [q1]


def test_the_module_is_a_procedure_not_a_url_table():
    """A table of document URLs would still 'work' if search were deleted.

    The retrieve path has to ask the search, and fitness is a function of the
    locator, not of a stored URL. A gold URL appearing as a string literal in
    this module would be the answer key wearing a different hat.
    """
    source = inspect.getsource(CompanyIRConnector.retrieve_for_issuer)
    assert "_discover" in source
    assert "looks_like_issuer_ir" in source
    assert "_locator_answers" in source or "asked_quarters" in source
    module = inspect.getsource(inspect.getmodule(CompanyIRConnector))
    assert "sec.gov/Archives" not in module
    assert "http://" not in module.split("def looks_like_issuer_ir")[0]
