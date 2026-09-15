"""The stages that read fields off each entry of a model's reply.

A reply that is a bare array is kept under `BARE_LIST` and returned by
`listed` for whatever key is asked of it, so a list of strings meant for one
question is also what the next stage's `listed` call hands back. Every caller
that goes on to read a field off each entry then meets a string, and `.get`
ends the job at whatever stage received it.

Two such callers are exercised end to end - the extractor filling candidates
from spans, and the reconciler settling a period - because the unit test on
`mappings` cannot show that the stage around it still produces its answer.

Invented names: Calderon, Acme Pharma.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DatapointORM, DrugJobORM, ExtractionRunORM
from app.domain.models import ValidationStatus, new_id
from app.llm.client import LLMModules
from app.pipeline.orchestrator import PipelineOrchestrator


class _Answers:
    """A client whose reply is whatever the test hands it."""

    def __init__(self, reply):
        self.reply = reply

    async def chat_json(self, *, model, system, user):
        return self.reply


@pytest.mark.asyncio
async def test_the_extractor_survives_a_reply_of_bare_strings(monkeypatch):
    monkeypatch.setattr(
        "app.llm.client.get_settings",
        lambda: type("S", (), {"openrouter_api_key": "test-key",
                               "openrouter_model_extract": "test/model"})(),
    )
    llm = LLMModules(client=_Answers(
        # asked for candidate objects, the model answered with the quotes
        {"_bare_list": ["Calderon net sales were $483.3 million"]}
    ))
    result = await llm.extract_revenue_from_spans(
        product="Calderon", company="Acme Pharma", source_meta={},
        spans=[{"span_id": "s1", "span_text": "Calderon net sales were $483.3 million"}],
    )
    assert result["candidates"] == [], "no entry carried a period, a value and a quote"
    assert [s["span_id"] for s in result["spans"]] == ["s1"], "the source is still read"


def _job():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon",
                     manufacturer="Acme Pharma", status="running", quality_flags=[])
    db.add_all([run, job]); db.commit()
    return db, job


def _point(job, value, method):
    return DatapointORM(
        id=new_id(), job_id=job.id, source_id=None, period="2021Q4",
        period_type="quarterly", value_reported=value,
        value_normalized_usd_millions=value, currency="USD", unit="millions",
        revenue_scope="Product family", extraction_method=method,
        confidence_score=0.9 if method == "xbrl_fact" else 0.4,
        source_url=f"https://example.invalid/{method}",
        validation_status=ValidationStatus.AUTO_PASS.value, issue_flags=[],
        source_quote=f"Calderon {value}",
        citation_json={"source_type": "quarterly_report"},
    )


def test_the_reconciler_survives_a_reply_of_bare_strings():
    db, job = _job()
    rows = [_point(job, 483.3, "xbrl_fact"), _point(job, 470.0, "table")]
    db.add_all(rows); db.commit()

    orch = PipelineOrchestrator(db, file_store=None)

    async def bare_strings(**_):
        # asked for verdict objects, the model answered with the figures
        return {"_bare_list": ["483.3", "470.0"]}

    orch.llm.reconcile = bare_strings
    asyncio.run(orch._reconcile_with_llm(job, rows))

    got = {row.extraction_method: row for row in db.query(DatapointORM).all()}
    assert got["xbrl_fact"].validation_status == "auto_pass", (
        "no entry was a verdict, so the group is settled by the ranking"
    )
    assert got["table"].validation_status == "needs_review"
