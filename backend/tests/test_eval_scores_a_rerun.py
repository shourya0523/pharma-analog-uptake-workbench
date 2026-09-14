"""A case whose job did not finish is scored from its later run.

The full sweep folded a failed job's fresh run into the score by hand, with a
scratch merge script. The eval does it now, and says how many it did it for,
so the number reported is produced by the thing that reports it.
"""

from __future__ import annotations

import importlib.util
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]


def _load_eval():
    spec = importlib.util.spec_from_file_location("eval_script", REPO / "scripts" / "eval.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


WINDOW = {"earnings_since": "2025-04-05", "earnings_until": "2026-05-05"}


def _server(monkeypatch, ev, runs: list[dict], jobs: dict[str, list[dict]]):
    def fake_get(base, path, timeout=180):
        if path.startswith("/observability/db/extraction_runs"):
            return {"rows": runs, "total": len(runs)}
        if path.startswith("/runs/"):
            run_id = path.split("/")[2]
            return {"id": run_id, "jobs": jobs.get(run_id, [])}
        raise AssertionError(path)

    monkeypatch.setattr(ev, "get", fake_get)


def test_the_newest_finished_later_run_is_taken_and_an_older_one_is_not(monkeypatch):
    ev = _load_eval()
    runs = [
        {"id": "old", "created_at": "2026-09-01T10:00:00", "options_json": WINDOW},
        {"id": "scored", "created_at": "2026-09-02T10:00:00", "options_json": WINDOW},
        {"id": "retry-1", "created_at": "2026-09-02T12:00:00", "options_json": WINDOW},
        {"id": "retry-2", "created_at": "2026-09-02T14:00:00", "options_json": WINDOW},
        {"id": "other-window", "created_at": "2026-09-03T10:00:00",
         "options_json": {"earnings_since": "2020-01-01", "earnings_until": "2021-01-01"}},
    ]
    jobs = {
        "old": [{"id": "j-old", "drug_name": "Calderon", "status": "completed"}],
        "retry-1": [{"id": "j-1", "drug_name": "Calderon", "status": "failed"}],
        "retry-2": [{"id": "j-2", "drug_name": "calderon", "status": "ready_for_review"},
                    {"id": "j-x", "drug_name": "NuVessa", "status": "completed"}],
        "other-window": [{"id": "j-w", "drug_name": "Calderon", "status": "completed"}],
    }
    _server(monkeypatch, ev, runs, jobs)
    by_window = ev.runs_by_window("http://x")
    key = ev.window_key(WINDOW)

    assert [row["id"] for row in by_window[key]] == ["retry-2", "retry-1", "scored", "old"], (
        "newest first, and only this window"
    )
    found = ev.later_finished_job(
        "http://x", drug="Calderon", key=key, scored_run="scored", runs=by_window
    )
    assert found == ("retry-2", jobs["retry-2"][0]), (
        "the finished retry, matched on the name whatever its case; not the "
        "retry that failed again, not the older run, not another window"
    )


def test_no_later_run_leaves_the_case_as_it_was(monkeypatch):
    ev = _load_eval()
    runs = [{"id": "scored", "created_at": "2026-09-02T10:00:00", "options_json": WINDOW},
            {"id": "old", "created_at": "2026-09-01T10:00:00", "options_json": WINDOW}]
    _server(monkeypatch, ev, runs, {"old": [{"id": "j", "drug_name": "Calderon", "status": "completed"}]})
    by_window = ev.runs_by_window("http://x")
    assert ev.later_finished_job(
        "http://x", drug="Calderon", key=ev.window_key(WINDOW), scored_run="scored", runs=by_window
    ) is None


def test_the_summary_says_how_many_were_rescored():
    source = (REPO / "scripts" / "eval.py").read_text()
    assert "scored from a later run" in source
    assert "scored_from_later_run" in source, "the detail file records where each score came from"
