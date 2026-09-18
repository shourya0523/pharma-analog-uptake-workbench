"""Every document a run parses says what it turned out to hold, on its row.

The coverage predicate existed and nothing called it, so a run that fetched
twenty-eight documents and a run that fetched twenty-eight useful ones still
looked the same. `_parse` is the one place a retrieved source and its parsed
document are both in hand, so the verdict is computed there, once per
document, and written on the source the pipeline already stores.

Both answers are here: the schedule that prints the quarter is recorded as
answering it, and the note that names the product beside no figure anyone can
reach is recorded as naming it only.

Invented names: Calderon, Calderon XR, NuVessa, Acme Pharma.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DrugJobORM, ExtractionRunORM, SourceDocumentORM
from app.domain.models import (
    ParsedDocument,
    ParsingStatus,
    RetrievalStatus,
    RetrievedSource,
    SourceType,
    new_id,
)
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import LocalFileStore

SCHEDULE = [
    [None, "Three Months Ended March 31,", "Three Months Ended March 31,"],
    [None, "2025", "2024"],
    ["Calderon", "55,881", "19,834"],
    ["NuVessa", "9,001", "8,600"],
]

WINDOW = {"earnings_since": "2025-04-01", "earnings_until": "2025-06-30"}


class _Parser:
    """Hands back the document prepared for each source, without touching disk."""

    def __init__(self, documents: dict[str, ParsedDocument]) -> None:
        self.documents = documents

    async def parse(self, source: RetrievedSource) -> ParsedDocument:
        return self.documents[source.source_id]


def _source() -> RetrievedSource:
    return RetrievedSource(
        source_id=new_id(),
        source_type=SourceType.QUARTERLY_REPORT,
        url="https://example.invalid/10q.htm",
        retrieval_status=RetrievalStatus.SUCCESS,
    )


def _document(source_id: str, *, grids, text) -> ParsedDocument:
    return ParsedDocument(
        source_id=source_id,
        text_blocks=[text],
        tables=[[[cell or "" for cell in row] for row in grid] for grid in grids],
        table_grids=grids,
        parsing_status=ParsingStatus.SUCCESS,
    )


async def _parsed(tmp_path, documents: dict[str, ParsedDocument], sources):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json=WINDOW)
    job = DrugJobORM(
        id=new_id(), run_id=run.id, drug_name="Calderon",
        manufacturer="Acme Pharma", status="running", quality_flags=[],
    )
    db.add_all([run, job])
    orch = PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path)))
    orch.parser = _Parser(documents)
    orch._job_aliases = ["Calderon", "Calderon XR"]
    orch._persist_sources(job, sources)
    db.commit()
    await orch._parse(job, sources)
    return db


@pytest.mark.asyncio
async def test_a_schedule_that_prints_the_quarter_is_recorded_as_answering_it(tmp_path):
    source = _source()
    document = _document(
        source.source_id, grids=[SCHEDULE],
        text="For the three months ended March 31, 2025",
    )
    db = await _parsed(tmp_path, {source.source_id: document}, [source])

    stored = db.get(SourceDocumentORM, source.source_id)
    recorded = (stored.metadata_json or {}).get("coverage")
    assert recorded["verdict"] == "answers"
    assert recorded["carries"] == ["2025Q1"]
    assert recorded["figures"]["2025Q1"] == 55881.0
    assert recorded["names_product"] is True
    # The verdict rides on the source object too, for anything downstream
    # holding it rather than its row.
    assert source.metadata["coverage"]["verdict"] == "answers"


@pytest.mark.asyncio
async def test_a_note_that_only_names_the_product_says_so(tmp_path):
    """A name in a document is not a figure in a document, and the row says which.

    The table here is a collaboration note: it names the product and states no
    period and no figure, so there is a table for the figure test to run on and
    the figure test finds nothing.
    """
    source = _source()
    document = _document(
        source.source_id,
        grids=[[["Collaboration", "Territory"], ["Calderon", "United States"]]],
        text="Calderon remains subject to the collaboration described above. "
             "For the three months ended March 31, 2025",
    )
    db = await _parsed(tmp_path, {source.source_id: document}, [source])

    recorded = (db.get(SourceDocumentORM, source.source_id).metadata_json or {})["coverage"]
    assert recorded["verdict"] == "names_only"
    assert recorded["carries"] == []
    assert recorded["names_product"] is True
