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

    The rows are read through `answer_keys.cases_in`, which knows the wrappers
    a case file uses - a bare list, or an object carrying the rows under
    ``cases`` beside a note about what the set is for. Unwrapping here by hand
    made the note impossible to write without the file becoming invisible.
    """
    from tests.answer_keys import cases_in

    cases_dir = REPO / "seed" / "cases"
    files = sorted(cases_dir.glob("*.json"))
    assert files, "no case files"
    for path in files:
        rows = cases_in(path)
        assert rows, f"{path.name}: no cases"
        for case in rows:
            assert case.get("source"), f"{path.name}: {case['drug_name']} cites nothing"
            assert case.get("expect"), f"{path.name}: {case['drug_name']} expects nothing"


def test_every_case_file_represents_both_answers():
    """A set that only publishes is passed by a system that publishes anything.

    Rule 4 states it for refusals; the mirror matters just as much here,
    because a case file that never expects silence cannot see a run inventing
    a figure for a quarter the issuer never reported. `docs/evaluation.md`
    claims this of every file in `seed/cases/`, and the files are found by
    globbing that directory rather than named here, so a file added later is
    held to the claim the document already makes about it.
    """
    from tests.answer_keys import cases_in

    files = sorted((REPO / "seed" / "cases").glob("*.json"))
    assert files, "no case files"
    for path in files:
        expectations = [e for case in cases_in(path) for e in case["expect"]]
        empty = [e for e in expectations if e["value_normalized_usd_millions"] is None]
        assert empty, (
            f"{path.name} expects a figure everywhere, so a run that publishes "
            f"something for every quarter scores full marks on it"
        )
        assert len(empty) < len(expectations), (
            f"{path.name} expects nothing anywhere, so a run that refuses "
            f"everything scores full marks on it"
        )
        for expectation in empty:
            assert expectation.get("why"), (
                f"{path.name}: an empty expectation that does not say why cannot "
                f"be told from a quarter nobody got round to filling in"
            )


def test_every_case_asks_for_the_configuration_that_ships():
    """A case that quietly turns an option off scores a product nobody runs.

    Which options a case has to state is read off the model, not written down:
    a field the model gives no default is one the caller must supply - the
    window the case is drawn around - and every other option a case restates
    has to restate what ships. Anything else is a set whose number belongs to a
    configuration the API would not give a person who asked for nothing.

    The files are globbed, so a case file added later is held to this the day
    it lands rather than when someone remembers to copy a guard into it.
    """
    from app.domain.models import ExtractionOptions
    from tests.answer_keys import cases_in

    shipped = ExtractionOptions()
    declared = ExtractionOptions.model_fields
    caller_states = {n for n, f in declared.items() if f.get_default() is None}

    files = sorted((REPO / "seed" / "cases").glob("*.json"))
    assert files, "no case files"
    assert caller_states, "no option is left to the caller; the window is derived from that"
    for path in files:
        for case in cases_in(path):
            options = case["options"]
            where = (path.name, case["drug_name"])
            unknown = set(options) - set(declared)
            assert not unknown, (where, sorted(unknown))
            for name in set(options) - caller_states:
                assert options[name] == getattr(shipped, name), (
                    f"{path.name}: {case['drug_name']} asks for {name}="
                    f"{options[name]!r} where the API ships "
                    f"{getattr(shipped, name)!r}, so its score is not the "
                    f"product's"
                )
            for name in caller_states & set(options):
                assert options[name] is not None, (where, name)


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
