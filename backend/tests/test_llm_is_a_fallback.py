"""The model is asked only about quarters nothing else could answer.

The pipeline used to call the model on every in-budget filing and read the
tables beside it, then merge. That ordering could not pay off, because
`CLAIM_STRENGTH` ranks `llm` below every deterministic producer and
`_resolve_conflicts` sorts two rows for one period by `claim_rank`: a model row
covering a quarter a table already answered either agreed - a request spent
confirming what was known - or disagreed and became a `needs_review` row
flagged `conflict_with_higher_priority_source`, adjudicated against before a
human ever saw it.

The ordering is now the other way round. These tests drive the stage with a spy
in place of the model, because the guarantee is about which calls happen, and
no amount of reading the source proves that.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DrugJobORM, ExtractionRunORM
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

# The quarter and the year so far, as a 10-Q states them.
TABLE = [
    [
        "",
        "Three Months Ended June 30, 2005",
        "Three Months Ended September 30, 2005",
        "Nine Months Ended September 30, 2005",
    ],
    ["Remodulin", "28,455", "31,352", "81,272"],
]
CAPTION = "Revenues (in thousands)"


class SpyLLM:
    """Records every extraction request and answers each one the same way."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def extract_revenue(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "candidates": [
                {
                    "period": "2005Q3",
                    "value_reported": 31.352,
                    "unit": "USD millions",
                    "period_type": "quarterly",
                    "revenue_scope": "Worldwide product",
                    "source_quote": "Remodulin revenue was $31.4 million",
                }
            ],
            "spans": [{"span_text": "Remodulin revenue was $31.4 million"}],
        }


def _job(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    job = DrugJobORM(
        id=new_id(),
        run_id=run.id,
        drug_name="Remodulin",
        manufacturer="United Therapeutics",
        status="running",
        quality_flags=[],
    )
    db.add(job)
    db.commit()
    orch = PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path)))
    spy = SpyLLM()
    orch.llm = spy
    return orch, job, spy


def _source_and_doc(tables, blocks):
    source = RetrievedSource(
        source_id="s1",
        source_type=SourceType.SEC_FILING,
        url="https://example.invalid/10q.htm",
        filing_type="10-Q",
        retrieval_status=RetrievalStatus.SUCCESS,
    )
    parsed = {
        "s1": ParsedDocument(
            source_id="s1",
            text_blocks=blocks,
            tables=tables,
            table_captions=[CAPTION] if tables else [],
            parsing_status=ParsingStatus.SUCCESS,
        )
    }
    return source, parsed


@pytest.mark.asyncio
async def test_a_filing_the_table_reader_answers_costs_no_model_call(tmp_path):
    orch, job, spy = _job(tmp_path)
    source, parsed = _source_and_doc([TABLE], [CAPTION])

    rows = await orch._extract_revenue(job, [source], parsed, {"quarterly_revenue": True})

    assert spy.calls == [], (
        "the table states every quarter in this filing, so there was nothing to "
        "ask the model about"
    )
    # And the rows are all there: the two quarters stated, plus the one derived.
    assert {row.period for row in rows} == {"2005Q1", "2005Q2", "2005Q3"}
    assert all(not row.extraction_method.startswith("llm") for row in rows)


@pytest.mark.asyncio
async def test_the_model_still_runs_where_nothing_deterministic_reads(tmp_path):
    """Suppression is per-filing, not a way of switching the model off."""
    orch, job, spy = _job(tmp_path)
    source, parsed = _source_and_doc(
        [],
        ["Remodulin generated meaningful revenue growth. Total revenues were $81.3 million."],
    )

    rows = await orch._extract_revenue(job, [source], parsed, {"quarterly_revenue": True})

    assert len(spy.calls) == 1, "no table and no readable sentence: this is the fallback case"
    assert [row.period for row in rows] == ["2005Q3"]
    assert [row.extraction_method for row in rows] == ["llm"]


@pytest.mark.asyncio
async def test_a_table_the_reader_declines_to_read_escalates_to_the_model(tmp_path):
    """A table stating no unit is skipped, and skipping is a reason to escalate.

    This is the fallback case as it actually occurs. The reader refuses the
    table rather than guessing thousands or millions - guessing is what put
    1000x-wrong values in this dataset - so the filing reaches the model with
    nothing answered, which is exactly when the model is worth its cost.
    """
    orch, job, spy = _job(tmp_path)
    unitless = [
        ["", "Three Months Ended September 30, 2005", "Nine Months Ended September 30, 2005"],
        ["Remodulin", "31,352", "81,272"],
    ]
    source, parsed = _source_and_doc(
        [unitless],
        ["Remodulin revenues grew over the period. Total revenues were $81.3 million."],
    )
    # The caption declares no unit, so the reader skips rather than guesses.
    parsed["s1"].table_captions = ["Revenues"]

    rows = await orch._extract_revenue(job, [source], parsed, {"quarterly_revenue": True})

    assert len(spy.calls) == 1, "a skipped table leaves the filing unanswered"
    assert [row.extraction_method for row in rows] == ["llm"]


@pytest.mark.asyncio
async def test_suppression_is_per_filing_not_across_them(tmp_path):
    """Another filing's table is not a reason to stop reading this one.

    `answered` is scoped to one document on purpose. Two filings covering one
    quarter are two independent pieces of evidence, and which of them wins is
    `_resolve_conflicts`' job - it weighs source authority first, so an audited
    10-K outranks a press exhibit whatever read each one. Scoping the
    suppression wider would let whichever filing happened to be processed first
    silence the rest.
    """
    orch, job, spy = _job(tmp_path)
    answered_source, parsed = _source_and_doc([TABLE], [CAPTION])
    # A second filing with nothing readable in it, covering the same quarter.
    other = RetrievedSource(
        source_id="s2",
        source_type=SourceType.EARNINGS_RELEASE,
        url="https://example.invalid/8k.htm",
        filing_type="8-K",
        retrieval_status=RetrievalStatus.SUCCESS,
    )
    parsed["s2"] = ParsedDocument(
        source_id="s2",
        text_blocks=["Remodulin grew over the period. Total revenues were $81.3 million."],
        tables=[],
        table_captions=[],
        parsing_status=ParsingStatus.SUCCESS,
    )

    await orch._extract_revenue(
        job, [answered_source, other], parsed, {"quarterly_revenue": True}
    )

    asked = {call["source_meta"]["url"] for call in spy.calls}
    assert asked == {"https://example.invalid/8k.htm"}, (
        "the model should have been asked about the unreadable filing and only "
        f"that one; it was asked about {sorted(asked)}"
    )


def test_a_model_that_cannot_be_reached_answers_nothing_rather_than_raising(monkeypatch):
    """A failed connection is that one question going unanswered.

    Every caller already treats an empty answer as the model having nothing
    to say - it is what the client returns when no key is set. A job that
    raised instead threw away every figure the other readers had produced,
    for a network blip, in whichever stage happened to ask: extraction,
    identity, reconciliation.
    """
    import asyncio

    import httpx

    from app.llm.client import OpenRouterClient

    calls = {"n": 0}

    class FlakyClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            calls["n"] += 1
            raise httpx.ConnectError("")

    monkeypatch.setattr(httpx, "AsyncClient", FlakyClient)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    client = OpenRouterClient.__new__(OpenRouterClient)
    from app.config import get_settings
    client.settings = get_settings()
    answer = asyncio.run(client.chat_json(model="m", system="s", user="u"))
    assert answer == {}
    assert calls["n"] == OpenRouterClient.TRANSPORT_ATTEMPTS, "it is retried before it is given up on"

    # The judge asks through the web-search variant; it is the same network.
    calls["n"] = 0
    answer = asyncio.run(client.chat_json_with_web(model="m", system="s", user="u"))
    assert answer == {}
    assert calls["n"] == OpenRouterClient.TRANSPORT_ATTEMPTS


async def _no_sleep(_seconds):
    return None
