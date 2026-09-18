"""Drive `scripts/eval.py` the way a server would answer it.

The eval speaks HTTP and nothing else, so what it does is visible by answering
its requests. Reading its source for a substring is not the same test: a string
in a comment passes one, a renamed local fails one, and neither says anything
about what the eval scored.

The server here is the least one an eval can be driven against - a run, its
jobs, and the datapoints of each job - and it keeps the calls it was asked, so
a test can say which route the answer came back through.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]


def load_eval():
    """The eval as a module, without installing it or importing `app`."""
    spec = importlib.util.spec_from_file_location("eval_script", REPO / "scripts" / "eval.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Server:
    """What a server answers, and a log of what it was asked."""

    def __init__(self, jobs: list[dict], datapoints: dict[str, list[dict]],
                 options: dict | None = None) -> None:
        self.jobs = jobs
        self.datapoints = datapoints
        self.options = options or {}
        self.calls: list[tuple[str, str]] = []
        self.posted: dict = {}

    def get(self, base: str, path: str, timeout: int = 180) -> dict:
        self.calls.append(("GET", path))
        if path.startswith("/jobs/"):
            return {"datapoints": self.datapoints.get(path.split("/")[2], [])}
        if path.startswith("/runs/"):
            return {"options": self.options, "jobs": self.jobs}
        if path.startswith("/observability/"):
            return {"rows": [], "total": 0}
        return {}

    def post(self, base: str, path: str, body: dict) -> dict:
        self.calls.append(("POST", path))
        self.posted = body
        return {"run_id": "run-1"}

    def wait(self, base: str, run_id: str, *, timeout_s: int, quiet: bool) -> dict:
        self.calls.append(("WAIT", run_id))
        return {"jobs": self.jobs}

    def install(self, module, monkeypatch) -> None:
        monkeypatch.setattr(module, "get", self.get)
        monkeypatch.setattr(module, "post", self.post)
        monkeypatch.setattr(module, "wait", self.wait)
        # No /config on this server: the header says so rather than inventing
        # settings, and the tests here are about what was scored.
        monkeypatch.setattr(module, "_try_get", lambda base, path: None)


def score_cases(module, server: Server, cases: list[dict], tmp_path, monkeypatch,
                *flags: str) -> tuple[int, list[dict]]:
    """Run the eval's own `main()` over `cases`; return its exit code and detail."""
    case_file = tmp_path / "cases.json"
    case_file.write_text(json.dumps(cases))
    out = tmp_path / "eval.json"
    server.install(module, monkeypatch)
    monkeypatch.setattr(
        "sys.argv",
        ["eval.py", "--cases", str(case_file), "--out", str(out), "--quiet", *flags],
    )
    code = module.main()
    return code, json.loads(out.read_text())
