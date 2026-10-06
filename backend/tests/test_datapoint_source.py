"""A figure's source document is served from the copy the pipeline stored."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app import main
from app.api import sources as sources_api
from app.db.migrations import upgrade_database
from app.db.models import DatapointORM, DrugJobORM, ExtractionRunORM, SourceDocumentORM
from app.storage.filestore import LocalFileStore

PAGE = b"<html><body><table><tr><td>Calderon</td><td>186.4</td></tr></table></body></html>"
PDF = b"%PDF-1.4 stand-in"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    upgrade_database(engine)
    monkeypatch.setattr(sources_api, "SessionLocal", sessionmaker(bind=engine))
    store = LocalFileStore(str(tmp_path))
    monkeypatch.setattr(sources_api, "get_file_store", lambda: store)
    asyncio.run(store.put("sources/run/job/doc-html.html", PAGE))
    asyncio.run(store.put("sources/run/job/doc-pdf.pdf", PDF))

    with Session(engine) as db:
        db.add(ExtractionRunORM(id="run", status="completed"))
        db.add(DrugJobORM(id="job", run_id="run", drug_name="Calderon", status="completed"))
        db.add(SourceDocumentORM(
            id="doc-html", job_id="job", source_type="sec_filing", retrieval_status="success",
            source_url="https://www.sec.gov/Archives/acme-ex99.htm",
            storage_key="sources/run/job/doc-html.html",
            metadata_json={"content_type": "text/html; charset=utf-8"},
        ))
        db.add(SourceDocumentORM(
            id="doc-pdf", job_id="job", source_type="issuer_ir", retrieval_status="success",
            source_url="https://ir.acme.example/q2.pdf", storage_key="sources/run/job/doc-pdf.pdf",
        ))
        common = dict(job_id="job", period="2024Q2", source_quote="Calderon | 186.4")
        # Read by the pipeline: carries its source row.
        db.add(DatapointORM(id="dp-own", source_id="doc-html",
                            source_url="https://www.sec.gov/Archives/acme-ex99.htm", **common))
        # Entered by a person citing a URL the job already stored: no source row.
        db.add(DatapointORM(id="dp-cited", source_id=None,
                            source_url="https://ir.acme.example/q2.pdf", **common))
        # Citing a URL nobody fetched.
        db.add(DatapointORM(id="dp-unfetched", source_id=None,
                            source_url="https://elsewhere.example/page", **common))
        db.commit()
    return TestClient(main.app)


def test_a_figure_gets_the_bytes_the_pipeline_stored_with_their_origin(client):
    res = client.get("/datapoints/dp-own/source")
    assert res.status_code == 200
    assert res.content == PAGE
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["x-source-url"] == "https://www.sec.gov/Archives/acme-ex99.htm"
    assert res.headers["x-source-content-type"].startswith("text/html")


def test_a_figure_without_a_source_row_uses_the_jobs_copy_of_the_same_url(client):
    res = client.get("/datapoints/dp-cited/source")
    assert res.status_code == 200
    assert res.content == PDF
    # No type was recorded at fetch time; the stored key's extension names it.
    assert res.headers["content-type"] == "application/pdf"


def test_nothing_stored_is_a_404_that_says_so(client):
    res = client.get("/datapoints/dp-unfetched/source")
    assert res.status_code == 404
    assert "no stored copy" in res.json()["detail"]
    assert client.get("/datapoints/nope/source").status_code == 404


def test_the_frontend_can_read_the_origin_header_across_origins(client):
    res = client.get("/datapoints/dp-own/source", headers={"Origin": "http://localhost:5173"})
    exposed = res.headers.get("access-control-expose-headers", "").lower()
    assert "x-source-url" in exposed and "x-source-content-type" in exposed
