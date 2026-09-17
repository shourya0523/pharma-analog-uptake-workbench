"""A corroborator is a second reading of the figure, not a footnote on it.

Corroborators were computed from whichever row won its group and the group was
never looked at again, which loses a quarter in two directions:

- the winner is held and the only publishable reading of the quarter is turned
  into a citation under a row nobody will publish, so the cell is empty while
  the answer sits in the database;
- the winner publishes and a corroborator carrying something the winner does
  not - the filer's note, the pair a combined line was reported as, a flag the
  note raised - has all of it discarded, so a stub covering part of a quarter
  publishes as the quarter.

Invented names: Calderon, NuVessa, acme:.
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
from app.domain.models import ValidationStatus, new_id
from app.parsing.labels import FLAG_PARTIAL, cite_footnote
from app.pipeline.orchestrator import PipelineOrchestrator


def _job():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon",
                     manufacturer="Acme Pharma", status="running", quality_flags=[])
    db.add_all([run, job]); db.commit()
    return db, job


def _source(db, job, source_type, url):
    src = SourceDocumentORM(id=new_id(), job_id=job.id, source_type=source_type,
                            source_url=url, source_date="2024-05-02",
                            retrieval_status="success",
                            accession_number=f"0000000-24-{len(url):06d}")
    db.add(src); db.commit()
    return src


def _point(job, value, *, method, source, status, flags=None, quote=None,
           reported_as=None, source_type="quarterly_report"):
    return DatapointORM(
        id=new_id(), job_id=job.id, source_id=source.id, period="2024Q1",
        period_type="quarterly", value_reported=value,
        value_normalized_usd_millions=value, currency="USD", unit="millions",
        revenue_scope="Product family", extraction_method=method,
        confidence_score=0.9, source_url=source.source_url,
        validation_status=status, issue_flags=list(flags or []),
        reported_as=reported_as,
        source_quote=quote if quote is not None else f"Calderon {value}",
        citation_json={"source_type": source_type,
                       "accession_number": source.accession_number},
    )


def _reconcile(monkeypatch, db, job, rows, settled_on=None):
    """Reconcile the rows, optionally with the model naming a winner.

    `settled_on` is the row the model's verdict picks. Without it the model
    says nothing and the ranking decides, which is the other half of how a
    group gets a winner.
    """
    orch = PipelineOrchestrator(db, file_store=None)

    async def verdict(**_):
        if settled_on is None:
            return {"resolved": [], "conflicts": []}
        return {"resolved": [{"winner_id": settled_on.id}], "conflicts": []}

    monkeypatch.setattr(orch.llm, "reconcile", verdict)
    db.add_all(rows); db.commit()
    asyncio.run(orch._reconcile_with_llm(job, rows))
    return {row.extraction_method: row for row in db.query(DatapointORM).all()}


def test_a_clean_corroborator_publishes_when_the_winner_is_held(monkeypatch):
    """The tagged fact is the quarter's answer when the sentence is vetoed.

    The model settles the group on the sentence; the judge had already vetoed
    it. Nothing then publishes the quarter, and the fact the filer tagged is
    filed as a citation under a row nobody will show.
    """
    db, job = _job()
    instance = _source(db, job, "quarterly_report", "https://example.invalid/acme-20240331.xml")
    readable = _source(db, job, "sec_filing", "https://example.invalid/acme-10q.htm")
    rows = [
        _point(job, 23.2, method="llm", source=readable,
               status=ValidationStatus.NEEDS_REVIEW.value,
               flags=["hard_veto:quote_states_a_different_period"],
               source_type="sec_filing"),
        _point(job, 23.217, method="xbrl_fact", source=instance,
               status=ValidationStatus.AUTO_PASS.value),
    ]
    got = _reconcile(monkeypatch, db, job, rows, settled_on=rows[0])
    assert got["xbrl_fact"].validation_status == ValidationStatus.AUTO_PASS.value
    # The held row keeps the verdict it was given. It is not recorded as
    # corroborating a figure it lost to, and it is not turned into a loser
    # either - nothing about it changed.
    assert got["llm"].validation_status == ValidationStatus.NEEDS_REVIEW.value
    assert "corroborates_published_figure" not in (got["llm"].issue_flags or [])
    assert not (got["llm"].citation_json or {}).get("corroborated_by")


def test_the_strongest_clean_corroborator_is_the_one_that_publishes(monkeypatch):
    """Two readings could take a held winner's place; the claim decides."""
    db, job = _job()
    instance = _source(db, job, "quarterly_report", "https://example.invalid/acme-20240331.xml")
    release = _source(db, job, "earnings_release", "https://example.invalid/acme-ex99.htm")
    readable = _source(db, job, "sec_filing", "https://example.invalid/acme-10q.htm")
    rows = [
        _point(job, 118.5, method="llm", source=readable,
               status=ValidationStatus.NEEDS_REVIEW.value,
               flags=["hard_veto:value_and_product_in_different_sentences"],
               source_type="sec_filing"),
        _point(job, 118.5, method="prose", source=release,
               status=ValidationStatus.AUTO_PASS.value, source_type="earnings_release"),
        _point(job, 118.462, method="xbrl_fact", source=instance,
               status=ValidationStatus.AUTO_PASS.value),
    ]
    got = _reconcile(monkeypatch, db, job, rows, settled_on=rows[0])
    assert got["xbrl_fact"].validation_status == ValidationStatus.AUTO_PASS.value
    assert got["prose"].validation_status == ValidationStatus.CORROBORATES.value


def test_a_corroborator_that_is_itself_a_question_does_not_take_the_place(monkeypatch):
    """A row a person has to look at cannot stand in for a reading that was
    held; the cell stays empty and both rows stay in the queue."""
    db, job = _job()
    readable = _source(db, job, "sec_filing", "https://example.invalid/acme-10q.htm")
    rows = [
        _point(job, 21.0, method="llm", source=readable,
               status=ValidationStatus.NEEDS_REVIEW.value,
               flags=["hard_veto:quote_states_a_different_period"],
               source_type="sec_filing"),
        _point(job, 21.005, method="table", source=readable,
               status=ValidationStatus.AUTO_PASS.value,
               flags=["combined_line"], reported_as="Calderon + NuVessa",
               source_type="sec_filing"),
    ]
    got = _reconcile(monkeypatch, db, job, rows, settled_on=rows[0])
    assert got["llm"].validation_status == ValidationStatus.NEEDS_REVIEW.value
    assert got["table"].validation_status == ValidationStatus.CORROBORATES.value
    assert ValidationStatus.AUTO_PASS.value not in {
        r.validation_status for r in got.values()
    }


def test_a_stub_quarter_does_not_publish_as_the_quarter(monkeypatch):
    """The winner publishes and the corroborator carries the filer's note.

    One is a tagged fact with no note attached; the other is the printed table
    row, whose footnote says the figure covers part of the quarter. They are
    one figure read twice, so what the note says is true of the published row,
    and the flag it raised holds the figure for a person.
    """
    db, job = _job()
    instance = _source(db, job, "quarterly_report", "https://example.invalid/acme-20240331.xml")
    readable = _source(db, job, "sec_filing", "https://example.invalid/acme-10q.htm")
    note = ("Calderon net product revenue for the three months ended March 31, "
            "2024 is for the period between March 13, 2024 (date of commercial "
            "launch) and March 31, 2024.")
    rows = [
        _point(job, 1.174, method="xbrl_fact", source=instance,
               status=ValidationStatus.AUTO_PASS.value),
        _point(job, 1.174, method="table", source=readable,
               status=ValidationStatus.NEEDS_REVIEW.value, flags=[FLAG_PARTIAL],
               quote="Calderon* 22,042 1,174" + cite_footnote("*", note),
               source_type="sec_filing"),
    ]
    got = _reconcile(monkeypatch, db, job, rows)
    winner = got["xbrl_fact"]
    assert winner.validation_status == ValidationStatus.NEEDS_REVIEW.value
    assert FLAG_PARTIAL in (winner.issue_flags or [])
    assert note in (winner.citation_json or {}).get("corroborator_footnote", "")
    assert got["table"].validation_status == ValidationStatus.CORROBORATES.value


def test_the_pair_a_combined_line_names_travels_to_the_published_row(monkeypatch):
    """The tagged member line says nothing about what it was reported with;
    the printed line it agrees with does, and that is true of both."""
    db, job = _job()
    instance = _source(db, job, "quarterly_report", "https://example.invalid/acme-20240331.xml")
    readable = _source(db, job, "sec_filing", "https://example.invalid/acme-10q.htm")
    rows = [
        _point(job, 19.255, method="xbrl_fact", source=instance,
               status=ValidationStatus.AUTO_PASS.value),
        _point(job, 19.255, method="table", source=readable,
               status=ValidationStatus.AUTO_PASS.value,
               reported_as="Calderon and NuVessa", quote="Calderon and NuVessa 19,255",
               source_type="sec_filing"),
    ]
    rows[1].citation_json = {**rows[1].citation_json, "combined_with": ["NuVessa"]}
    got = _reconcile(monkeypatch, db, job, rows)
    winner = got["xbrl_fact"]
    assert winner.reported_as == "Calderon and NuVessa"
    assert (winner.citation_json or {}).get("combined_with") == ["NuVessa"]
