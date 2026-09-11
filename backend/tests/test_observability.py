from datetime import UTC, datetime, timedelta

from app.db.models import DrugJobORM
from app.observability import dedupe_jobs_by_analog, normalize_analog_key


def _job(name: str, completeness: float, hours_ago: int = 0, job_id: str = "a") -> DrugJobORM:
    now = datetime.now(UTC)
    return DrugJobORM(
        id=job_id,
        run_id="run",
        drug_name=name,
        completeness_pct=completeness,
        created_at=now - timedelta(hours=hours_ago + 1),
        updated_at=now - timedelta(hours=hours_ago),
    )


def test_normalize_analog_key_collapses_case_and_space():
    assert normalize_analog_key("  Tyvaso  DPI ") == "tyvaso dpi"
    assert normalize_analog_key("TYVASO") == normalize_analog_key("tyvaso")


def test_dedupe_jobs_keeps_best_completeness_per_analog():
    jobs = [
        _job("Tyvaso", 10, hours_ago=0, job_id="old"),
        _job("tyvaso", 80, hours_ago=5, job_id="better"),
        _job("Adcirca", 50, hours_ago=1, job_id="adc"),
        _job("  Adcirca ", 40, hours_ago=0, job_id="adc2"),
    ]
    kept = dedupe_jobs_by_analog(jobs)
    names = sorted(j.drug_name for j in kept)
    assert len(kept) == 2
    by_key = {normalize_analog_key(j.drug_name): j for j in kept}
    assert by_key["tyvaso"].id == "better"
    assert by_key["adcirca"].id == "adc"
    assert "Adcirca" in names or "  Adcirca " in names


def test_a_published_figure_says_which_reader_produced_it():
    """A caller comparing two figures for one quarter compares claims.

    `CLAIM_STRENGTH` ranks a tagged fact above a number read off a page, and
    the pipeline reconciles on that rank - but the rank was invisible from
    outside, so a reader that had stopped answering looked exactly like one
    with nothing to say. That is how the tagged path stayed dead: the only way
    to see which reader answered was to open the database.
    """
    import ast
    import pathlib

    main = (pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    tree = ast.parse(main)
    keys = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "extraction_method" in keys, (
        "the datapoint payload must name the reader that produced the figure"
    )


def test_a_job_is_read_back_one_collection_at_a_time():
    """Joining one-to-many collections onto one row multiplies them together.

    Six of them on a job - datapoints, sources, tasks, checks, unresolved
    quarters, profile fields - made a Cartesian product that SQLAlchemy
    de-duplicated in Python: a fifty-kilobyte response cost two gigabytes and
    thirteen seconds on the event loop, and several callers reading finished
    jobs at once took the whole machine down. Each collection is loaded with
    its own query instead.
    """
    import ast
    import pathlib

    main = (pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    tree = ast.parse(main)
    get_job = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "get_job")
    loaders = {n.func.id for n in ast.walk(get_job)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id.endswith("load")}
    assert "joinedload" not in loaders, "a joined load on a collection multiplies rows"
    assert "selectinload" in loaders


def test_the_log_buffer_keeps_the_traceback_of_a_failed_job():
    """A handler that raised on every exception record kept none of them,
    which is when the buffer is read."""
    import logging

    from app.observability import RingBufferHandler, _logs

    handler = RingBufferHandler()
    log = logging.getLogger("test.ring")
    log.addHandler(handler)
    try:
        try:
            raise ValueError("Calderon has no such quarter")
        except ValueError:
            log.exception("pipeline_failed")
    finally:
        log.removeHandler(handler)
    entry = next(e for e in reversed(_logs) if e["message"] == "pipeline_failed")
    assert "Calderon has no such quarter" in entry["exc_info"]
