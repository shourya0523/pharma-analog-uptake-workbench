"""What produced a reading separates two readings of one filing; where it
sits does not.

An XBRL instance retrieved inside a 10-Q is stored as a quarterly report and
the human-readable document of the same accession as a filing, so ranking the
document before the claim put the figure the filer tagged below a model's
sentence about the page beside it. A tagged fact arrives that way whenever its
instance came out of a periodic report, which is the ordinary case rather than
an edge.

The ranking is also checked for being total over `SourceType`. Two members
state no revenue at all and were in neither list, reaching the ranking as an
unknown rank - the fall-through that says nothing about them.

Invented names: Calderon, acme:.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    DatapointORM,
    DrugJobORM,
    ExtractionRunORM,
    SourceDocumentORM,
)
from app.domain.models import SourceType, ValidationStatus, new_id
from app.pipeline.orchestrator import (
    DOCUMENT_FITNESS,
    SOURCE_PRIORITY,
    PipelineOrchestrator,
)


def _job():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon",
                     manufacturer="Acme Pharma", status="running", quality_flags=[])
    db.add_all([run, job]); db.commit()
    return db, job


def _accession(db, job, source_type, url):
    src = SourceDocumentORM(id=new_id(), job_id=job.id, source_type=source_type,
                            source_url=url, source_date="2024-05-02",
                            retrieval_status="success",
                            accession_number="0000000-24-000001")
    db.add(src); db.commit()
    return src


def _row(job, src, value, *, method, source_type):
    return DatapointORM(
        id=new_id(), job_id=job.id, source_id=src.id, period="2024Q1",
        period_type="quarterly", value_reported=value,
        value_normalized_usd_millions=value, currency="USD", unit="millions",
        revenue_scope="Product family", extraction_method=method,
        confidence_score=0.9, source_url=src.source_url,
        validation_status=ValidationStatus.AUTO_PASS.value, issue_flags=[],
        source_quote=f"Calderon net product sales were ${value} million",
        citation_json={"source_type": source_type,
                       "accession_number": src.accession_number},
    )


def _reconcile(monkeypatch, db, job, rows):
    orch = PipelineOrchestrator(db, file_store=None)

    async def no_opinion(**_):
        return {"resolved": [], "conflicts": []}

    monkeypatch.setattr(orch.llm, "reconcile", no_opinion)
    db.add_all(rows); db.commit()
    asyncio.run(orch._reconcile_with_llm(job, rows))
    return {row.extraction_method: row for row in db.query(DatapointORM).all()}


def test_the_filers_own_tagged_fact_wins_against_a_sentence_from_the_same_filing(monkeypatch):
    db, job = _job()
    instance = _accession(db, job, SourceType.QUARTERLY_REPORT.value,
                          "https://example.invalid/acme-20240331.htm")
    readable = _accession(db, job, SourceType.SEC_FILING.value,
                          "https://example.invalid/acme-10q.htm")
    rows = [
        _row(job, instance, 133.936, method="xbrl_fact",
             source_type=SourceType.QUARTERLY_REPORT.value),
        _row(job, readable, 133.9, method="llm",
             source_type=SourceType.SEC_FILING.value),
    ]
    got = _reconcile(monkeypatch, db, job, rows)
    assert got["xbrl_fact"].validation_status == ValidationStatus.AUTO_PASS.value
    assert got["llm"].validation_status == ValidationStatus.CORROBORATES.value
    cited = got["xbrl_fact"].citation_json["corroborated_by"]
    assert [c["extraction_method"] for c in cited] == ["llm"]


def test_the_document_still_ranks_where_the_claim_is_the_same(monkeypatch):
    """Claim strength first does not stop the document deciding a tie: one
    sentence in the filing, the same figure on a page found by search."""
    db, job = _job()
    filing = _accession(db, job, SourceType.SEC_FILING.value,
                        "https://example.invalid/acme-10q.htm")
    found = _accession(db, job, SourceType.LLM_SEARCH.value,
                       "https://example.invalid/a-page")
    rows = [
        _row(job, found, 133.9, method="llm", source_type=SourceType.LLM_SEARCH.value),
        _row(job, filing, 133.9, method="llm", source_type=SourceType.SEC_FILING.value),
    ]
    _reconcile(monkeypatch, db, job, rows)
    published = {r.source_url: r.validation_status for r in db.query(DatapointORM).all()}
    assert published[filing.source_url] == ValidationStatus.AUTO_PASS.value
    assert published[found.source_url] == ValidationStatus.CORROBORATES.value


def test_every_source_type_is_ranked():
    """Both rankings are total over the enum, so nothing arrives by default."""
    for name, ranking in (("SOURCE_PRIORITY", SOURCE_PRIORITY),
                          ("DOCUMENT_FITNESS", DOCUMENT_FITNESS)):
        assert list(dict.fromkeys(ranking)) == list(ranking), f"{name} repeats a member"
        assert set(ranking) == set(SourceType), (
            f"{name} does not rank {sorted(t.value for t in set(SourceType) - set(ranking))}"
        )
