"""A page a search points at is fetched here, and the passage checked against it.

The fallback stored the model's own excerpt as the source's text. The readers
then read that text, and the rule that a quote must appear verbatim in the
cited document was satisfied by the quote appearing in the model's account of
the document. Nothing in the pipeline had ever seen the page. A fabricated
passage would have passed every check, and only the by-hand check, which
fetches the URL, could have caught it.

The document is now ours: the page is fetched, stored, and read, and whether
the passage the model reported is actually on it is recorded beside the
source rather than assumed.
"""

from __future__ import annotations

import asyncio

import pytest

from app.connectors.llm_search import LLMSearchConnector, excerpt_is_on_the_page
from app.connectors.sources import _is_fetchable
from app.domain.models import RetrievalStatus
from app.storage.filestore import LocalFileStore

PAGE = b"""<html><body>
<p>Acme Pharma reports first quarter results.</p>
<table><tr><td>Calderon</td><td>19,255</td></tr></table>
<p>Calderon net product revenue was
$19.3 million for the quarter ended March 31, 2026.</p>
</body></html>"""


def test_a_passage_split_across_lines_is_still_on_the_page():
    assert excerpt_is_on_the_page(
        "Calderon net product revenue was $19.3 million for the quarter ended March 31, 2026.",
        PAGE, "text/html")


def test_a_passage_nobody_wrote_is_not_on_the_page():
    assert not excerpt_is_on_the_page(
        "Calderon net product revenue was $altered million for the quarter.", PAGE, "text/html")
    assert not excerpt_is_on_the_page("", PAGE, "text/html")


def test_a_long_report_is_accepted_on_the_sentence_that_carries_the_figure():
    reported = ("Acme reported strongly this quarter. Calderon net product revenue was "
                "$19.3 million for the quarter ended March 31, 2026. Management was pleased.")
    assert excerpt_is_on_the_page(reported, PAGE, "text/html")


def test_a_url_the_model_returns_is_not_asked_for_blindly():
    assert _is_fetchable("https://example.test/report.htm")
    for refused in ("file:///etc/passwd", "http://localhost:8000/runs",
                    "http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data",
                    "http://db.internal/dump", "not a url"):
        assert not _is_fetchable(refused), refused


def _connector(tmp_path, monkeypatch, *, page=PAGE, excerpt="Calderon net product revenue was $19.3 million for the quarter ended March 31, 2026.", fail=False):
    connector = LLMSearchConnector(LocalFileStore(str(tmp_path)))

    async def _search(**kwargs):
        return {"sources": [{"url": "https://example.test/q1.htm", "title": "Q1", "excerpt": excerpt}]}

    async def _fetch(file_store, *, run_id, job_id, source_id, url, user_agent):
        if fail:
            raise RuntimeError("403 Forbidden")
        key = f"sources/{run_id}/{job_id}/{source_id}.html"
        await file_store.put(key, page, "text/html")
        return page, "text/html", key

    monkeypatch.setattr(connector.llm, "web_search_and_fetch", _search)
    monkeypatch.setattr("app.connectors.llm_search.fetch_page", _fetch)
    return connector


def _retrieve(connector):
    return asyncio.run(connector.fallback_retrieve(
        run_id="r", job_id="j", goal="revenue", product="Calderon", aliases=["Calderon"],
        manufacturer="Acme Pharma", ticker="ACME",
    ))


def test_the_source_carries_the_page_we_fetched(tmp_path, monkeypatch):
    source, = _retrieve(_connector(tmp_path, monkeypatch))
    assert source.retrieval_status == RetrievalStatus.SUCCESS
    assert source.storage_key and source.storage_key.endswith(".html")
    assert source.metadata["excerpt_found_in_page"] is True
    assert source.raw_text is None, "the readers read the page, not the model's account of it"


def test_a_passage_that_is_not_there_is_recorded_as_not_there(tmp_path, monkeypatch):
    source, = _retrieve(_connector(tmp_path, monkeypatch, excerpt="Calderon revenue was $99.9 million."))
    assert source.metadata["excerpt_found_in_page"] is False
    assert "not in the page" in (source.notes or "")


def test_a_page_that_cannot_be_fetched_is_not_a_source(tmp_path, monkeypatch):
    source, = _retrieve(_connector(tmp_path, monkeypatch, fail=True))
    assert source.retrieval_status == RetrievalStatus.FAILED
    assert source.storage_key is None and source.raw_text is None
