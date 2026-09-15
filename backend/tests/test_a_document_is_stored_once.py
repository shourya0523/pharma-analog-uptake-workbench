"""One document, one stored object.

Every filing was written twice with identical bytes: once under the accession
it belongs to, and once more per job under the source's own id. A sweep
therefore stored a filing as many times as there were products citing it. On
a 24-product run that was 615 MB where roughly half was the duplicate, and a
full sweep of the answer key would have been several gigabytes of it.

The accession and the document name identify a filing. Two jobs reading the
same filing are reading the same bytes, and the file store is where that is
already true - `_read_cache` looks there first, so the second copy was never
the one being read.
"""

from __future__ import annotations

import asyncio

import httpx

from app.connectors.sources import SECConnector, _content_type
from app.storage.filestore import LocalFileStore


class _Store(LocalFileStore):
    def __init__(self, root) -> None:
        super().__init__(str(root))
        self.writes: list[tuple[str, str | None]] = []

    async def put(self, key, data, content_type=None):
        self.writes.append((key, content_type))
        return await super().put(key, data, content_type)


def _connector(tmp_path):
    store = _Store(tmp_path)
    connector = SECConnector(store)
    return connector, store


def _fetch(connector, *, doc="acme-20260630.htm", job="job-1"):
    class Client:
        async def get(self, url):
            return httpx.Response(200, request=httpx.Request("GET", url), content=b"<html>x</html>")

    return asyncio.run(connector._fetch_document(
        Client(), url=f"https://www.sec.gov/Archives/edgar/data/1/000/{doc}",
        accession="0000000001-26-000001", doc=doc, run_id="run-1", job_id=job, source_id="s-1",
    ))


def test_a_document_is_written_once_and_the_key_is_where_it_lives(tmp_path):
    connector, store = _connector(tmp_path)
    raw, from_cache, key = _fetch(connector)

    assert raw == b"<html>x</html>" and from_cache is False
    assert [k for k, _ in store.writes] == [key], "written once, under the key that is returned"
    assert key == "cache/sec/000000000126000001/acme-20260630.htm"


def test_a_second_job_reading_the_same_filing_writes_nothing(tmp_path):
    connector, store = _connector(tmp_path)
    _, _, first = _fetch(connector, job="job-1")
    store.writes.clear()

    _, from_cache, second = _fetch(connector, job="job-2")

    assert from_cache is True
    assert second == first
    assert store.writes == [], "the bytes are already stored; a second job adds nothing"


def test_a_stored_document_says_what_it_is(tmp_path):
    """The per-job copy was always named `.html`, so an exhibit filed as a PDF
    was stored as markup and then parsed as markup."""
    connector, store = _connector(tmp_path)
    _, _, key = _fetch(connector, doc="acme-20260630ex991.pdf")

    assert key.endswith(".pdf")
    assert store.writes[0][1] == "application/pdf"
    assert _content_type("acme-20260630.htm") == "text/html"
    assert _content_type("acme-20260630_htm.xml") in {"text/xml", "application/xml"}
