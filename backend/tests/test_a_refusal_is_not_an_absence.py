"""A filing that could not be fetched is not a filing that was never made.

EDGAR refuses with 429 or 503 under load. Retrieval recorded those documents
as failed and moved on, and the stage below asked one question of what came
back: did any SEC source succeed. A run that had been refused answered that
question the same way as a run for an issuer with no filings at all, so the
search fallback ran and published investor-relations pages for a filer whose
own quarterly reports were sitting behind a rate limit. The fallback exists
for the second case and not the first.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.connectors import sources as module
from app.connectors.sources import (
    SECConnector,
    sec_pace,
    sec_saw_refusal,
    sec_saw_success,
)
from app.db.models import Base, DrugJobORM, ExtractionRunORM
from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import LocalFileStore


def test_a_refusal_slows_every_caller_and_quiet_speeds_them_up(monkeypatch):
    """The pace is observed, not guessed: what the endpoint accepts depends on
    who else is asking from the same address."""
    monkeypatch.setattr(module, "_sec_pace", module._SEC_FLOOR_S)
    monkeypatch.setattr(module, "_last_sec_refusal", 0.0)

    sec_saw_refusal()
    doubled = sec_pace()
    assert doubled > module._SEC_FLOOR_S

    sec_saw_refusal(retry_after=3.0)
    assert sec_pace() >= 3.0, "EDGAR's own number wins when it gives one"

    # Still inside the quiet window: a success this soon proves nothing.
    sec_saw_success()
    assert sec_pace() >= 3.0

    _quiet(monkeypatch)
    sec_saw_success()
    assert sec_pace() < 3.0, "a quiet window brings the pace back down"

    monkeypatch.setattr(module, "_sec_pace", module._SEC_CEILING_S * 4)
    sec_saw_refusal()
    assert sec_pace() <= module._SEC_CEILING_S, "and it never runs away"


def _quiet(monkeypatch):
    """Put the last refusal a full recovery window into the past."""
    monkeypatch.setattr(
        module, "_last_sec_refusal",
        time.monotonic() - module._SEC_RECOVERY_QUIET_S - 1,
    )


def test_a_trickle_of_refusals_does_not_pin_the_pace_at_the_ceiling(monkeypatch):
    """Recovery counted consecutive successes, and every refusal reset the
    count. A shared address gets a refusal every few seconds, so the count
    never reached its target: the pace reached the ceiling early in a run and
    stayed there, at one request every four seconds, for the rest of it.

    Timed recovery cannot be starved that way - the endpoint either has been
    quiet for a window or has not.
    """
    monkeypatch.setattr(module, "_sec_pace", module._SEC_CEILING_S)
    monkeypatch.setattr(module, "_last_sec_refusal", time.monotonic())

    # A long run of successes, interrupted now and then by a refusal, which is
    # what the log of a throttled run actually shows.
    for _ in range(200):
        sec_saw_success()
    assert sec_pace() == module._SEC_CEILING_S, "no quiet window has passed"

    windows = 0
    while sec_pace() > module._SEC_FLOOR_S and windows < 20:
        _quiet(monkeypatch)
        sec_saw_success()
        windows += 1
    assert sec_pace() == module._SEC_FLOOR_S
    # Halving from the ceiling, so the climb down is logarithmic in the range
    # rather than a number to keep in step with the constants.
    import math
    assert windows == math.ceil(math.log2(module._SEC_CEILING_S / module._SEC_FLOOR_S))


def test_a_refused_document_is_waited_out_rather_than_recorded_as_missing(monkeypatch):
    """Four attempts with a doubling delay gave up after seven seconds."""
    async def _now(*_a, **_k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _now)
    monkeypatch.setattr(module, "_sec_throttle", _now)

    class Refusing:
        def __init__(self, refusals: int) -> None:
            self.left, self.calls = refusals, 0

        async def get(self, url):
            self.calls += 1
            status = 429 if self.left > 0 else 200
            self.left -= 1
            return httpx.Response(status, request=httpx.Request("GET", url), json={"ok": True})

    connector = SECConnector.__new__(SECConnector)
    client = Refusing(refusals=12)
    response = asyncio.run(connector._get_with_retry(client, "https://www.sec.gov/x"))
    assert response.status_code == 200 and client.calls == 13


def _job():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon",
                     manufacturer="Acme Pharma", status="running", quality_flags=[])
    db.add_all([run, job]); db.commit()
    return db, job


def _sec_source(status: RetrievalStatus) -> RetrievedSource:
    return RetrievedSource(
        source_id=new_id(), source_type=SourceType.SEC_FILING,
        url="https://example.invalid/10q.htm", filing_type="10-Q", retrieval_status=status,
    )


@pytest.mark.parametrize(
    ("listed", "searched", "flagged"),
    [
        ([RetrievalStatus.FAILED], False, True),   # refused: the filings exist
        ([], True, False),                         # nothing filed: the fallback's case
        ([RetrievalStatus.SUCCESS], False, False),
    ],
)
def test_the_search_runs_for_an_issuer_with_nothing_filed_and_not_for_one_we_could_not_reach(
    monkeypatch, tmp_path, listed, searched, flagged
):
    db, job = _job()
    orch = PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path)))
    asked: list[str] = []

    async def _sec(*_a, **kw):
        return [_sec_source(status) for status in listed]

    async def _search(**kw):
        asked.append(kw.get("goal"))
        return []

    monkeypatch.setattr(orch.sec, "retrieve", _sec)
    monkeypatch.setattr(orch.search, "fallback_retrieve", _search)
    monkeypatch.setattr(orch.fda, "retrieve", lambda **kw: _sec())

    asyncio.run(orch._retrieve(job, {"sec_filings": True, "openfda": False}))

    assert bool(asked) is searched
    assert ("sec_retrieval_failed" in (job.quality_flags or [])) is flagged
