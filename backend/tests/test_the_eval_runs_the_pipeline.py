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
    """A case carries its own provenance, so the number can be re-checked."""
    import json

    cases_dir = REPO / "seed" / "cases"
    files = sorted(cases_dir.glob("*.json"))
    assert files, "no case files"
    for path in files:
        for case in json.loads(path.read_text()):
            assert case.get("source"), f"{path.name}: {case['drug_name']} cites nothing"
            assert case.get("expect"), f"{path.name}: {case['drug_name']} expects nothing"
