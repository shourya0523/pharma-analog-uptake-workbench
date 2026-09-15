"""An eval speaks to the pipeline the way a person does, or it is not an eval.

Nineteen scripts once scored this project. Two called ``run_job``; the other
seventeen called the readers directly, each carrying its own idea of how the
stages compose. Every one of them was right about the function it called and
silent about the product - which is how the tagged reader could be measured as
working for a whole branch while the pipeline published nothing it produced,
and how a coverage figure that excluded the LLM extractor, the evidence judge,
reconciliation and the search fallback got quoted as the pipeline's accuracy.

The defence is not a comment, and it is not "some eval drives the
orchestrator". It is that an eval has no way to reach past the API: it imports
nothing from ``app``, so there is no second implementation of the pipeline for
it to drift from.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
EVALS = sorted(SCRIPTS.glob("eval*.py"))


def _imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_there_is_an_eval():
    assert EVALS, "nothing scores the pipeline"


def test_no_eval_imports_the_thing_it_is_scoring():
    """The property the old rule only approximated.

    A script that imports a reader measures that reader. A script that imports
    the orchestrator measures a composition it chose itself. Neither is the
    product, and both read as though they were.
    """
    for path in EVALS:
        modules = _imports(ast.parse(path.read_text()))
        reached = {m for m in modules if m == "app" or m.startswith("app.")}
        assert not reached, (
            f"{path.name} imports {sorted(reached)}. An eval reaches the pipeline "
            f"through the API, so that what it scores is what a caller gets."
        )


def test_the_eval_goes_in_and_out_through_the_api():
    """In through POST /runs, out through GET, scored on what was published."""
    for path in EVALS:
        source = path.read_text()
        assert "/runs" in source, f"{path.name} never starts a run"
        assert "/jobs/" in source or "datapoints" in source, (
            f"{path.name} never reads the answers back"
        )
        assert "auto_pass" in source, (
            f"{path.name} does not say what published means, so it credits the "
            f"pipeline for figures it declined to stand behind"
        )


def test_every_case_file_says_where_its_answers_came_from():
    """A case carries its own provenance, so the number can be re-checked.

    A case says what it expects, or it says where its expectation lives. The
    revenue cases carry a figure per quarter; a case scored on a product's
    attributes is scored against a whole answer key, passed to the eval as
    `--labels`, and copying that key's rows into the case file would be the
    same answer stored twice and free to drift. What both shapes must do is
    cite something a reader can go and check.
    """
    import json

    cases_dir = REPO / "seed" / "cases"
    files = sorted(cases_dir.glob("*.json"))
    assert files, "no case files"
    for path in files:
        for case in json.loads(path.read_text()):
            assert case.get("source"), f"{path.name}: {case['drug_name']} cites nothing"
            if case.get("expect"):
                continue
            # No inline expectation: the source has to be the answer key
            # itself, named well enough to fetch.
            assert ".jsonl" in case["source"] or ".json" in case["source"], (
                f"{path.name}: {case['drug_name']} expects nothing and does not "
                f"name the answer key it is scored against"
            )


def test_every_run_is_started_before_any_is_waited_for():
    """The windows are independent; the server decides its own concurrency.

    Waiting for one batch before submitting the next left the pool running
    one or two jobs for most of a sweep and paid the slowest job of every
    batch in turn. Sampled per minute, one sweep of twenty-four jobs ran at
    six or seven jobs for four minutes and at one or two for nine.
    """
    for path in EVALS:
        source = path.read_text()
        if "/runs" not in source or "wait(" not in source:
            continue
        tree = ast.parse(source)
        posts, waits = [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "post":
                    posts.append(node.lineno)
                elif node.func.id == "wait":
                    waits.append(node.lineno)
        if not posts or not waits:
            continue
        # The loop that starts runs must close before the loop that waits.
        starts = [n for n in ast.walk(tree) if isinstance(n, ast.For)
                  and any(p in range(n.lineno, (n.end_lineno or n.lineno) + 1) for p in posts)]
        assert starts, f"{path.name}: no loop creates runs"
        for loop in starts:
            inside = range(loop.lineno, (loop.end_lineno or loop.lineno) + 1)
            assert not any(w in inside for w in waits), (
                f"{path.name} waits for a run inside the loop that starts them, so the "
                f"next window is not submitted until this one finishes"
            )


def test_no_case_file_sets_an_option_the_run_does_not_define():
    """Every case file, not just the holdouts, is held to the option model.

    `product_metadata: false` sat in all five case files and removed the whole
    profile stage from every scored run; nothing failed, because an option a
    case sets is just a key in a JSON blob. Reading the model means a switch
    that has been removed cannot come back quietly, and a misspelt option -
    which silently does nothing, and so reads as a run that behaved oddly -
    fails here instead.
    """
    import json

    from app.domain.models import ExtractionOptions

    allowed = set(ExtractionOptions.model_fields)
    offenders = []
    for path in sorted((REPO / "seed").glob("*/*.json")):
        payload = json.loads(path.read_text())
        cases = payload.get("cases") if isinstance(payload, dict) else payload
        if not isinstance(cases, list):
            continue
        for case in cases:
            if not isinstance(case, dict) or not isinstance(case.get("options"), dict):
                continue
            for name in sorted(set(case["options"]) - allowed):
                offenders.append(f"{path.name}: {case.get('drug_name')} sets {name}")
    assert not offenders, "\n  ".join(["options no run defines:", *offenders])
