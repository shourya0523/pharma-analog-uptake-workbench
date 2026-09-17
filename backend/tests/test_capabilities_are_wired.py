"""Every capability this repo builds is used by the pipeline that ships.

An eval that calls a function directly reports a number the product cannot
produce, so this test discovers the capabilities rather than naming them: any
public function or class in ``app/`` that nothing in ``app/`` references is
either a capability nobody wired, or dead code. Both are worth failing over.

``SCRIPT_ONLY`` is the exception list. Every entry carries the kind of
exception it claims to be, and ``test_every_exception_is_what_it_says``
checks that claim against where the name is actually referenced, so a reason
cannot go quietly false the way a comment can.
"""

from __future__ import annotations

import ast
import collections
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
TESTS = pathlib.Path(__file__).resolve().parent
SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"

# Invoked by a framework, not by our code: routes, settings, ORM models.
FRAMEWORK = ("main.py", "observability.py", "api", "db/models.py", "config.py")

# A script runs it, and the pipeline correctly does not.
SCRIPT = "script"
# Nothing runs it: no caller in `app/`, none in `scripts/`, no route. It is
# built and covered by tests and reaches no user. That is open work, named as
# such so the suite does not read as though the product were whole.
NOT_WIRED = "not wired"

SCRIPT_ONLY = {
    # A builder writes the register; the pipeline only ever reads it.
    "save_register": SCRIPT,
    # Remediation, run against a job that has already finished.
    "backfill_job": SCRIPT,
    # The analytics layer, which the API does not expose. Wiring or deleting
    # it is open work, not an accident to be silently tolerated.
    "rank_analogs": NOT_WIRED,
    "calculate_revenue_uptake": NOT_WIRED,
    "time_to_ninety_percent_peak": NOT_WIRED,
    "select_peak_estimate": NOT_WIRED,
    "aggregate_comparable_sales": NOT_WIRED,
    "calculate_competitive_snapshot": NOT_WIRED,
    "build_launch_peers": NOT_WIRED,
    "categorize_snapshots": NOT_WIRED,
    "assess_intensity": NOT_WIRED,
    "peers_at_launch": NOT_WIRED,
    "compare_to_rule": NOT_WIRED,
    # Adjudication verdicts. The arithmetic they wrap is used; the verdicts
    # are not, because nothing yet asks them. See the plan.
    "adjudicate_split_ownership_quarter": NOT_WIRED,
    "adjudicate_total_against_parts": NOT_WIRED,
    "adjudicate_reported_value": NOT_WIRED,
    "adjudicate_positional_solutions": NOT_WIRED,
    # Alternative readers. `app/` reads through another path and these are
    # exercised only by tests. An eval cannot reach them either:
    # `test_the_eval_runs_the_pipeline` fails any `scripts/eval*.py` that
    # imports `app`, so "kept for the evals" was never a thing they could be.
    "html_tables": NOT_WIRED,
    "extract_revenue_rows": NOT_WIRED,
    "read_positional_block": NOT_WIRED,
    # An importer for a file an analyst prepares by hand - with no caller and
    # no route, so there is nothing to hand the file to.
    "read_peak_sales_csv": NOT_WIRED,
    # Writes a document's coverage verdict onto the retrieved source it
    # belongs to. The only place a source and its parsed document are both in
    # hand is the orchestrator, which this does not reach into; until it is
    # called there, nothing records the verdict on a real run.
    "record_coverage": NOT_WIRED,
    # Domain types and predicates constructed only by tests. The pipeline
    # builds its equivalents inline, so these are a second spelling of a
    # capability rather than the one that ships.
    "CompetitiveIntensity": NOT_WIRED,
    "PeakEstimateType": NOT_WIRED,
    "PharmaAssertion": NOT_WIRED,
    "RevenueCandidate": NOT_WIRED,
    "is_aggregate_formulation": NOT_WIRED,
    "has_label_section_header": NOT_WIRED,
    "select_assertion": NOT_WIRED,
    "deduplicate_phase3_programs": NOT_WIRED,
}


def _definitions() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(APP.rglob("*.py")):
        relative = str(path.relative_to(APP))
        if relative.startswith(FRAMEWORK):
            continue
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    found[node.name] = relative
    return found


def _references(root: pathlib.Path) -> collections.defaultdict:
    seen: collections.defaultdict = collections.defaultdict(set)
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                seen[node.id].add(path)
            elif isinstance(node, ast.Attribute):
                seen[node.attr].add(path)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    seen[alias.name].add(path)
    return seen


def test_no_capability_is_reachable_only_from_an_eval():
    in_app = _references(APP)
    outside = _references(TESTS)
    outside_scripts = _references(SCRIPTS) if SCRIPTS.exists() else {}

    unwired = []
    for name, home in sorted(_definitions().items()):
        if in_app.get(name) or name in SCRIPT_ONLY:
            continue
        reached = "tests/scripts" if (outside.get(name) or outside_scripts.get(name)) else "nothing"
        unwired.append(f"{name} ({home}) is called by {reached}, never by app/")

    assert not unwired, (
        "\n".join(unwired)
        + "\n\nWire it into the pipeline, delete it, or add it to SCRIPT_ONLY "
        "with the reason it is correctly one-directional."
    )


def test_every_exception_is_what_it_says():
    """An exception's reason is checked against where the name is referenced.

    The list is hand-maintained, which is fine - what is not fine is a reason
    nobody re-checks. The three ways an entry can be wrong are each a failure
    here: it is referenced in `app/` after all, so the entry does nothing; it
    claims a script and none calls it; or it claims nothing calls it and a
    script does.
    """
    in_app = _references(APP)
    in_scripts = _references(SCRIPTS) if SCRIPTS.exists() else {}
    definitions = _definitions()

    wrong = []
    for name, kind in sorted(SCRIPT_ONLY.items()):
        if name not in definitions:
            wrong.append(f"{name}: on the list and defined nowhere in app/")
            continue
        if in_app.get(name):
            where = sorted(str(p.relative_to(APP)) for p in in_app[name])
            wrong.append(
                f"{name}: referenced inside app/ ({', '.join(where)}), so the "
                f"exception is a no-op - drop it from the list"
            )
            continue
        called_by_script = bool(in_scripts.get(name))
        if kind == SCRIPT and not called_by_script:
            wrong.append(f"{name}: marked {SCRIPT!r} and no script references it")
        if kind == NOT_WIRED and called_by_script:
            where = sorted(str(p.relative_to(SCRIPTS)) for p in in_scripts[name])
            wrong.append(
                f"{name}: marked {NOT_WIRED!r} but {', '.join(where)} calls it"
            )

    assert not wrong, "\n".join(wrong)


def test_the_list_says_which_kind_each_entry_is():
    """A bare name carries no claim, and a claim nobody states cannot be
    checked. Every entry has to be one of the kinds above."""
    kinds = {SCRIPT, NOT_WIRED}
    bad = {name: kind for name, kind in SCRIPT_ONLY.items() if kind not in kinds}
    assert not bad, f"unknown exception kinds: {bad}"
