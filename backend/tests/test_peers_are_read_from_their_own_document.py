"""The peer list a candidate is filtered against is its own document's.

The filer's schedule is its own product list, so "which other brand could this
quote be about" has a different answer in every filing. The list was computed
per document in the prepare loop and read back in the second loop from a name
that loop never rebinds, so every source's model candidates were filtered by
whichever document happened to be prepared last.

Both answers are represented: the filing that gives NuVessa a row of its own
must offer NuVessa as a peer, and the filing that never names it must not -
whichever order the two are prepared in.

Invented names: Calderon XR (ours), NuVessa, Tavoral; Acme Pharma.
"""

from __future__ import annotations

from typing import Any

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
from app.pipeline import orchestrator as orchestrator_module
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import LocalFileStore

# Two schedules. Each carries a period heading, year columns and a row for our
# product, so each is a filer's own product list - and the brand beside ours is
# a different one in each. The figures are left unstated so that the table
# reader answers nothing and the model is asked about both filings.
SCHEDULE_WITH_NUVESSA = [
    ["", "Three Months Ended June 30,", ""],
    ["", "2024", "2023"],
    ["Calderon XR", "-", "-"],
    ["NuVessa", "-", "-"],
]
SCHEDULE_WITH_TAVORAL = [
    ["", "Three Months Ended June 30,", ""],
    ["", "2024", "2023"],
    ["Calderon XR", "-", "-"],
    ["Tavoral", "-", "-"],
]
SCHEDULES = {
    "lists_nuvessa": SCHEDULE_WITH_NUVESSA,
    "lists_tavoral": SCHEDULE_WITH_TAVORAL,
}

PROSE = "Calderon XR net product revenue was $34.9 million for the quarter."
PEER_QUOTE = "NuVessa net product revenue was $7.0 million for the quarter."


class SpyLLM:
    """Answers every extraction request with one candidate."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def extract_revenue(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "candidates": [
                {
                    "period": "2024Q2",
                    "value_reported": 7.0,
                    "unit": "USD millions",
                    "period_type": "quarterly",
                    "revenue_scope": "Worldwide",
                    "source_quote": PEER_QUOTE,
                }
            ],
            "spans": [{"span_text": PEER_QUOTE}],
        }


def _orchestrator(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    job = DrugJobORM(
        id=new_id(),
        run_id=run.id,
        drug_name="Calderon XR",
        manufacturer="Acme Pharma",
        status="running",
        quality_flags=[],
    )
    db.add(job)
    db.commit()
    orch = PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path)))
    orch.llm = SpyLLM()
    return db, orch, job


def _source(db, job, source_id: str):
    source = RetrievedSource(
        source_id=source_id,
        source_type=SourceType.SEC_FILING,
        url=f"https://example.invalid/{source_id}.htm",
        filing_type="10-Q",
        retrieval_status=RetrievalStatus.SUCCESS,
    )
    db.add(
        SourceDocumentORM(
            id=source_id,
            job_id=job.id,
            source_type=SourceType.SEC_FILING.value,
            source_url=source.url,
            retrieval_status=RetrievalStatus.SUCCESS.value,
        )
    )
    doc = ParsedDocument(
        source_id=source_id,
        text_blocks=[PROSE, PEER_QUOTE],
        tables=[SCHEDULES[source_id]],
        parsing_status=ParsingStatus.SUCCESS,
    )
    return source, doc


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "order",
    [("lists_nuvessa", "lists_tavoral"), ("lists_tavoral", "lists_nuvessa")],
)
async def test_each_filings_model_rows_are_filtered_by_its_own_product_list(
    tmp_path, monkeypatch, order
):
    db, orch, job = _orchestrator(tmp_path)
    sources, parsed = [], {}
    for source_id in order:
        source, doc = _source(db, job, source_id)
        sources.append(source)
        parsed[source_id] = doc
    db.commit()

    # The peer list reaches the filter, so the filter is where it is observed.
    # The model's candidates are the call that carries `source_text`; the table
    # reader's own call in the prepare loop does not.
    seen: dict[str, list[str]] = {}
    real = orchestrator_module.filter_revenue_candidates
    quote_to_source = {PEER_QUOTE: None}

    def recording(candidates, **kwargs):
        if kwargs.get("source_text") is not None:
            seen[quote_to_source["current"]] = sorted(kwargs.get("peer_names") or [])
        return real(candidates, **kwargs)

    original_extract = orch.llm.extract_revenue

    async def tracking(**kwargs):
        quote_to_source["current"] = (kwargs.get("source_meta") or {}).get("url", "")
        return await original_extract(**kwargs)

    orch.llm.extract_revenue = tracking
    monkeypatch.setattr(orchestrator_module, "filter_revenue_candidates", recording)

    await orch._extract_revenue(job, sources, parsed, {"quarterly_revenue": True})

    by_source = {
        url.rsplit("/", 1)[-1].removesuffix(".htm"): peers for url, peers in seen.items()
    }
    assert set(by_source) == set(order), "both filings must reach the model"
    assert "nuvessa" in by_source["lists_nuvessa"], (
        "this filing gives NuVessa a row of its own, so NuVessa is one of the "
        "other brands its quotes could be about"
    )
    assert "nuvessa" not in by_source["lists_tavoral"], (
        "this filing never names NuVessa, so another document's product list "
        "must not decide what its quotes are about"
    )
