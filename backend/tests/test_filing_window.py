"""Filing window derivation: caller bounds, else approval, else lookback."""

from datetime import date

from app.connectors.filing_window import resolve_filing_window


def test_caller_bounds_win():
    window = resolve_filing_window(
        since=date(2024, 1, 1),
        until=date(2025, 6, 30),
        approval=date(2013, 10, 18),
        today=date(2026, 9, 22),
    )
    assert window.since == date(2024, 1, 1)
    assert window.until == date(2025, 6, 30)
    assert window.source == "caller"


def test_approval_fills_an_empty_window():
    window = resolve_filing_window(
        since=None,
        until=None,
        approval=date(2013, 10, 18),
        today=date(2026, 9, 22),
    )
    assert window.since == date(2013, 10, 18)
    assert window.until == date(2026, 9, 22)
    assert window.source == "approval"


def test_caller_since_keeps_today_as_until():
    window = resolve_filing_window(
        since=date(2020, 1, 1),
        until=None,
        approval=date(2013, 10, 18),
        today=date(2026, 9, 22),
    )
    assert window.since == date(2020, 1, 1)
    assert window.until == date(2026, 9, 22)
    assert window.source == "caller"


def test_lookback_when_nothing_is_known():
    window = resolve_filing_window(
        since=None,
        until=None,
        approval=None,
        today=date(2026, 9, 22),
        lookback_years=5,
    )
    assert window.since == date(2021, 9, 22)
    assert window.until == date(2026, 9, 22)
    assert window.source == "lookback"


def test_swapped_bounds_are_ordered():
    window = resolve_filing_window(
        since=date(2025, 1, 1),
        until=date(2024, 1, 1),
        approval=None,
        today=date(2026, 9, 22),
    )
    assert window.since == date(2024, 1, 1)
    assert window.until == date(2025, 1, 1)


def test_orchestrator_writes_an_approval_window_onto_the_run():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db.migrations import upgrade_database
    from app.db.models import DrugJobORM, DrugProfileFieldORM, ExtractionRunORM
    from app.domain.models import new_id
    from app.pipeline.orchestrator import PipelineOrchestrator

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    upgrade_database(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()
    run = ExtractionRunORM(id="run-1", status="queued", options_json={})
    job = DrugJobORM(id="job-1", run_id="run-1", drug_name="Calderon", status="queued")
    db.add_all([run, job])
    db.add(
        DrugProfileFieldORM(
            id=new_id(),
            job_id="job-1",
            field="fda_approval_date",
            value="2013-10-18",
            citation_json={},
            validation_status="needs_review",
        )
    )
    db.commit()

    options = PipelineOrchestrator(db)._ensure_filing_window(job, run, {})
    assert options["earnings_since"] == "2013-10-18"
    assert options["earnings_until"] == date.today().isoformat()
    db.refresh(run)
    assert run.options_json["earnings_since"] == "2013-10-18"
    assert any(flag.startswith("filing_window:approval") for flag in (job.quality_flags or []))
    db.close()


def test_sec_covering_path_uses_the_filing_fuse_not_as_selector():
    import inspect

    from app.connectors.sources import SECConnector

    source = inspect.getsource(SECConnector.retrieve)
    assert "sec_filing_fuse" in source
    assert "choose_filings(candidates, asked, ceiling=max_filings)" in source
