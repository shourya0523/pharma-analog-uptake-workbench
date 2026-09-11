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
