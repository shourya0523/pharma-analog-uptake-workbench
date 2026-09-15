"""A product's aliases are bought from the model once, not once per job.

Aliases are a property of the product: the model is asked about a brand, its
generic, its manufacturer and its ticker, and nothing else. Every job in a
sweep asked the same question again before it could read a single filing, and
in one run that was the longest stage of the job while retrieval took two
seconds.

This is rule 3's kind of cache and not rule 3's kind of mechanism: delete
every stored answer and the next job asks the model, at one call each. What
it must not do is answer a question it was not asked, which is why the key is
all four inputs rather than the brand alone.
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DrugJobORM, DrugProfileFieldORM, ExtractionRunORM
from app.domain.models import new_id
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import LocalFileStore


class CountingLLM:
    def __init__(self) -> None:
        self.asked: list[dict] = []

    async def expand_aliases(self, **kwargs):
        self.asked.append(kwargs)
        return {"aliases": ["Calderon XR"], "formulations": ["Nebulized Calderon"],
                "parent_companies": ["Acme Holdings"], "search_terms": []}


def _orchestrator(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(ExtractionRunORM(id="run", status="running", options_json={}))
    db.commit()
    orch = PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path)))
    orch.llm = CountingLLM()
    return orch, db


def _job(db, **fields):
    job = DrugJobORM(id=new_id(), run_id="run", status="running", quality_flags=[],
                     drug_name=fields.pop("drug_name", "Calderon"), **fields)
    db.add(job); db.commit()
    return job


def test_the_second_job_asking_the_same_question_reuses_the_answer(tmp_path):
    orch, db = _orchestrator(tmp_path)
    first = _job(db, manufacturer="Acme Pharma", generic_name="calderonib", ticker="ACME")
    second = _job(db, manufacturer="Acme Pharma", generic_name="calderonib", ticker="ACME")

    one = asyncio.run(orch._expand_aliases(first))
    two = asyncio.run(orch._expand_aliases(second))

    assert len(orch.llm.asked) == 1, "the same question was put to the model twice"
    assert one == two and "Calderon XR" in two
    # The answer the second job used is the one the first recorded.
    stored = json.loads(
        db.query(DrugProfileFieldORM).filter_by(job_id=first.id, field="llm_aliases").one().value
    )
    assert stored["merged"] == two


def test_a_different_question_is_asked_again(tmp_path):
    """The same brand from a different filer is a different question, and a
    stored answer is not an answer to it."""
    orch, db = _orchestrator(tmp_path)
    asyncio.run(orch._expand_aliases(
        _job(db, manufacturer="Acme Pharma", generic_name="calderonib", ticker="ACME")))

    for changed in (
        {"manufacturer": "Beta Therapeutics", "generic_name": "calderonib", "ticker": "ACME"},
        {"manufacturer": "Acme Pharma", "generic_name": None, "ticker": "ACME"},
        {"manufacturer": "Acme Pharma", "generic_name": "calderonib", "ticker": None},
        {"drug_name": "NuVessa", "manufacturer": "Acme Pharma", "generic_name": "calderonib",
         "ticker": "ACME"},
    ):
        before = len(orch.llm.asked)
        asyncio.run(orch._expand_aliases(_job(db, **changed)))
        assert len(orch.llm.asked) == before + 1, f"not re-asked for {changed}"


def test_a_stored_answer_that_says_nothing_is_not_used(tmp_path):
    orch, db = _orchestrator(tmp_path)
    job = _job(db, manufacturer="Acme Pharma", generic_name="calderonib", ticker="ACME")
    db.add(DrugProfileFieldORM(id=new_id(), job_id=job.id, field="llm_aliases",
                               value=json.dumps({"merged": []}), validation_status="needs_review"))
    db.commit()

    asyncio.run(orch._expand_aliases(
        _job(db, manufacturer="Acme Pharma", generic_name="calderonib", ticker="ACME")))
    assert len(orch.llm.asked) == 1
