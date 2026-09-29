"""Extraction given a document — success and parse failure, and nothing else.

Retrieval and identity are out of scope. A correct filing is handed to the
parser and then to the extract stage; a document that cannot be read is
handed the same way. What must hold:

- A schedule that states product and period yields those quarters.
- A parse that fails does not become a candidate source.
- A failed document beside a good one does not poison the good one's reading.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DrugJobORM, ExtractionRunORM
from app.domain.models import (
    ParsingStatus,
    RetrievalStatus,
    RetrievedSource,
    SourceType,
    new_id,
)
from app.extraction.candidates import extract_revenue_candidates
from app.parsing.documents import DocumentParser
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import LocalFileStore

# Invented filer schedule: one product, two comparative quarters, unit declared.
CORRECT_HTML = """
<html><body>
<p>Net product revenues (in thousands)</p>
<table>
  <tr>
    <td></td>
    <td>Three Months Ended March 31, 2025</td>
    <td>Three Months Ended March 31, 2024</td>
  </tr>
  <tr>
    <td>Calderon</td>
    <td>55,881</td>
    <td>19,834</td>
  </tr>
  <tr>
    <td>Total product revenue</td>
    <td>55,881</td>
    <td>19,834</td>
  </tr>
</table>
</body></html>
"""


def _store(tmp_path) -> LocalFileStore:
    return LocalFileStore(str(tmp_path))


def _orch(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    job = DrugJobORM(
        id=new_id(),
        run_id=run.id,
        drug_name="Calderon",
        manufacturer="Acme Therapeutics",
        status="running",
        quality_flags=[],
    )
    db.add(job)
    db.commit()
    return db, PipelineOrchestrator(db, file_store=_store(tmp_path)), job


def _filing(*, source_id: str, storage_key: str | None, status=RetrievalStatus.SUCCESS) -> RetrievedSource:
    return RetrievedSource(
        source_id=source_id,
        source_type=SourceType.SEC_FILING,
        url=f"https://example.invalid/{source_id}.htm",
        filing_type="10-Q",
        retrieval_status=status,
        storage_key=storage_key,
    )


@pytest.mark.asyncio
async def test_correct_document_yields_the_quarters_it_states(tmp_path, caplog):
    store = _store(tmp_path)
    key = "filings/calderon_q1.htm"
    await store.put(key, CORRECT_HTML.encode())
    source = _filing(source_id="good", storage_key=key)
    parser = DocumentParser(store)

    with caplog.at_level(logging.INFO):
        doc = await parser.parse(source)

    assert doc.parsing_status == ParsingStatus.SUCCESS
    assert doc.tables, "the schedule must survive parse"
    candidates, _findings, skips, _pending = extract_revenue_candidates(
        doc.tables,
        product="Calderon",
        context="\n".join(doc.text_blocks),
        captions=doc.table_captions,
        grids=doc.table_grids,
        units=doc.table_units,
    )
    assert not skips
    by_period = {
        c["period"]: c
        for c in candidates
        if c.get("period_type") == "quarterly"
    }
    assert by_period["2025Q1"]["value_reported"] == pytest.approx(55_881.0)
    assert by_period["2025Q1"]["value_normalized_usd_millions"] == pytest.approx(55.881)
    assert by_period["2024Q1"]["value_reported"] == pytest.approx(19_834.0)
    assert by_period["2024Q1"]["unit"] == "thousands"


@pytest.mark.asyncio
async def test_extract_stage_reads_only_what_parse_handed_it(tmp_path):
    """Bypass retrieve/identity: the stage sees the document, nothing else."""
    db, orch, job = _orch(tmp_path)
    key = "filings/calderon_q1.htm"
    await orch.file_store.put(key, CORRECT_HTML.encode())
    source = _filing(source_id="good", storage_key=key)
    doc = await DocumentParser(orch.file_store).parse(source)

    rows = await orch._extract_revenue(
        job, [source], {source.source_id: doc}, {"quarterly_revenue": True}
    )
    periods = {
        r.period: float(r.value_normalized_usd_millions or r.value_reported)
        for r in rows
        if r.period_type == "quarterly"
    }
    assert periods["2025Q1"] == pytest.approx(55.881)
    assert periods["2024Q1"] == pytest.approx(19.834)
    assert job.candidates_extracted == len(rows)
    assert "no_product_revenue_candidates" not in (job.quality_flags or [])


@pytest.mark.asyncio
async def test_retrieval_failure_is_a_failed_parse_not_a_guess(tmp_path, caplog):
    source = _filing(source_id="bad", storage_key=None, status=RetrievalStatus.FAILED)
    with caplog.at_level(logging.INFO, logger="app.parsing.documents"):
        doc = await DocumentParser(_store(tmp_path)).parse(source)
    assert doc.parsing_status == ParsingStatus.FAILED
    assert "retrieval failed" in (doc.notes or "").lower()
    assert any("parse_skip" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_missing_bytes_are_a_failed_parse(tmp_path, caplog):
    source = _filing(source_id="missing", storage_key="filings/does_not_exist.htm")
    with caplog.at_level(logging.WARNING, logger="app.parsing.documents"):
        doc = await DocumentParser(_store(tmp_path)).parse(source)
    assert doc.parsing_status == ParsingStatus.FAILED
    assert any("parse_failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_empty_document_is_a_failed_parse(tmp_path, caplog):
    store = _store(tmp_path)
    key = "filings/empty.htm"
    await store.put(key, b"")
    source = _filing(source_id="empty", storage_key=key)
    # Empty file → decode to "" → no text branch when raw_text also absent.
    source.raw_text = None
    with caplog.at_level(logging.WARNING, logger="app.parsing.documents"):
        doc = await DocumentParser(store).parse(source)
    assert doc.parsing_status == ParsingStatus.FAILED
    assert "no text" in (doc.notes or "").lower()


@pytest.mark.asyncio
async def test_corrupt_openfda_body_is_a_failed_parse(tmp_path, caplog):
    source = RetrievedSource(
        source_id="fda",
        source_type=SourceType.OPENFDA,
        url="https://api.fda.gov/example",
        retrieval_status=RetrievalStatus.SUCCESS,
        raw_text="{not-json",
    )
    with caplog.at_level(logging.WARNING, logger="app.parsing.documents"):
        doc = await DocumentParser(_store(tmp_path)).parse(source)
    assert doc.parsing_status == ParsingStatus.FAILED
    assert any("openfda_json" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_failed_document_does_not_poison_a_good_one(tmp_path):
    db, orch, job = _orch(tmp_path)
    key = "filings/calderon_q1.htm"
    await orch.file_store.put(key, CORRECT_HTML.encode())
    good = _filing(source_id="good", storage_key=key)
    bad = _filing(source_id="bad", storage_key=None, status=RetrievalStatus.FAILED)
    parser = DocumentParser(orch.file_store)
    good_doc = await parser.parse(good)
    bad_doc = await parser.parse(bad)
    assert good_doc.parsing_status == ParsingStatus.SUCCESS
    assert bad_doc.parsing_status == ParsingStatus.FAILED

    rows = await orch._extract_revenue(
        job,
        [bad, good],
        {bad.source_id: bad_doc, good.source_id: good_doc},
        {"quarterly_revenue": True},
    )
    assert {r.source_id for r in rows} == {good.source_id}
    assert any(r.period == "2025Q1" for r in rows)


@pytest.mark.asyncio
async def test_only_failed_documents_flag_no_product_revenue(tmp_path, caplog):
    db, orch, job = _orch(tmp_path)
    bad = _filing(source_id="bad", storage_key=None, status=RetrievalStatus.FAILED)
    bad_doc = await DocumentParser(orch.file_store).parse(bad)

    with caplog.at_level(logging.WARNING, logger="app.pipeline.orchestrator"):
        rows = await orch._extract_revenue(
            job, [bad], {bad.source_id: bad_doc}, {"quarterly_revenue": True}
        )
    assert rows == []
    assert "no_product_revenue_candidates" in (job.quality_flags or [])
    assert any("no_product_revenue" in r.message for r in caplog.records)
