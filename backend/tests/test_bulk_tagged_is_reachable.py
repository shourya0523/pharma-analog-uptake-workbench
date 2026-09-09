"""The bulk tagged reader must produce datapoints in the pipeline.

`test_capabilities_are_wired.py` checks that `candidates_from_notes` has a
caller under `app/`. Six times in this project something had a caller and had
never produced a row - the derivation had one for eleven runs and emitted
nothing, because of a default nobody read. A grep cannot see that. This drives
the stage and looks at what came out.

The filer here is invented, so nothing passes because a real brand is spelled
somewhere in the code.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.models import Base, DrugJobORM, ExtractionRunORM
from app.domain.models import new_id
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import LocalFileStore

ACC = "0000000007-26-000004"
SUB = "adsh\tcik\tname\tform\tperiod\tfiled\n"
NUM = "adsh\ttag\tversion\tddate\tqtrs\tuom\tdimh\tiprx\tvalue\tfootnote\tfootlen\tdimn\tcoreg\tdurp\tdatp\tdcml\n"
DIM = "dimhash\tsegments\tsegt\n"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _extract(tmp_path: Path) -> Path:
    root = tmp_path / "2026_07"
    root.mkdir()
    (root / "sub.tsv").write_text(
        SUB + f"{ACC}\t4242\tHOLLOWAY BIOSCIENCES\t10-Q\t20260630\t20260801\n"
    )
    (root / "dim.tsv").write_text(DIM + "0xA1\tProductOrService=Selvant;\t0\n")
    (root / "num.tsv").write_text(
        NUM
        + f"{ACC}\tRevenueFromContractWithCustomerExcludingAssessedTax\tus-gaap/2026"
          f"\t20260630\t1\tUSD\t0xA1\t1\t5500000\t\t0\t1\t\t0\t0\t-6\n"
    )
    return root


def _job(tmp_path: Path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    job = DrugJobORM(
        id=new_id(), run_id=run.id, drug_name="Selvant",
        manufacturer="Holloway Biosciences", cik="0000004242",
        status="running", quality_flags=[],
    )
    db.add(job)
    db.commit()
    return db, PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path))), job


def test_the_stage_contributes_nothing_when_no_extract_is_configured(tmp_path, monkeypatch):
    """The extracts are hundreds of megabytes; a run never fetches one itself."""
    monkeypatch.delenv("NOTES_DATASET_DIRS", raising=False)
    get_settings.cache_clear()
    _db, orch, job = _job(tmp_path)
    rows, totals = orch._bulk_tagged_revenue(job)
    assert (rows, totals) == ([], [])


def test_a_configured_extract_produces_a_datapoint(tmp_path, monkeypatch):
    root = _extract(tmp_path)
    monkeypatch.setenv("NOTES_DATASET_DIRS", str(root))
    get_settings.cache_clear()
    _db, orch, job = _job(tmp_path)

    rows, _totals = orch._bulk_tagged_revenue(job)

    assert len(rows) == 1, "the stage has a caller but produced no row"
    row = rows[0]
    assert row.period == "2026Q2"
    assert row.value_normalized_usd_millions == 5.5
    assert row.extraction_method == "xbrl_fact"
    # The citation resolves to the filing the fact was tagged in.
    assert row.citation_json["accession"] == ACC
    assert ACC.replace("-", "") in row.source_url


def test_several_extracts_are_read_in_turn(tmp_path, monkeypatch):
    """A year of quarters is a year of monthly files, not one."""
    first = _extract(tmp_path)
    second = tmp_path / "2026_08"
    second.mkdir()
    other = "0000000007-26-000009"
    (second / "sub.tsv").write_text(
        SUB + f"{other}\t4242\tHOLLOWAY BIOSCIENCES\t10-Q\t20260930\t20261101\n"
    )
    (second / "dim.tsv").write_text(DIM + "0xA1\tProductOrService=Selvant;\t0\n")
    (second / "num.tsv").write_text(
        NUM
        + f"{other}\tRevenueFromContractWithCustomerExcludingAssessedTax\tus-gaap/2026"
          f"\t20260930\t1\tUSD\t0xA1\t1\t6100000\t\t0\t1\t\t0\t0\t-6\n"
    )
    monkeypatch.setenv("NOTES_DATASET_DIRS", os.pathsep.join([str(first), str(second)]))
    get_settings.cache_clear()
    _db, orch, job = _job(tmp_path)

    rows, _totals = orch._bulk_tagged_revenue(job)

    assert sorted(r.period for r in rows) == ["2026Q2", "2026Q3"]


def test_a_directory_that_is_not_an_extract_is_not_a_job_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTES_DATASET_DIRS", str(tmp_path / "nowhere"))
    get_settings.cache_clear()
    _db, orch, job = _job(tmp_path)
    assert orch._bulk_tagged_revenue(job) == ([], [])
