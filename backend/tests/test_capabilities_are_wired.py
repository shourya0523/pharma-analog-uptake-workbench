"""Every capability this repo builds is used by the pipeline that ships.

Six times in this project a reader, a derivation or an adjudicator was
written, given tests, measured in an eval, and never called by ``app/``. Each
time the eval was the only caller, which is what made the capability look
wired: an eval that calls a function directly reports a number the product
cannot produce.

The previous version of this test checked a hand-maintained list of four
names, so it caught only what someone already knew to worry about. This one
discovers the capabilities instead: any public function or class in ``app/``
that nothing in ``app/`` references is either a capability nobody wired, or
dead code. Both are worth failing over.

``SCRIPT_ONLY`` is the exception list, and every entry needs a reason. A
builder that writes a file the pipeline reads is correctly one-directional;
so is an importer, and so is a diagnostic the pipeline has no business
running. Anything else on this list is a to-do.
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

SCRIPT_ONLY = {
    # A builder writes the register; the pipeline only ever reads it.
    "save_register",
    # Importers, run by hand against a file someone was sent.
    "read_peak_sales_csv",
    # Remediation, run against a job that has already finished.
    "backfill_job",
    # The uptake and peak-sales half of the analytics layer, which no route
    # exposes yet. Wiring or deleting it is open work, not an accident to be
    # silently tolerated.
    "calculate_revenue_uptake", "time_to_ninety_percent_peak",
    "select_peak_estimate", "aggregate_comparable_sales",
    # The competitive-intensity assessor, which puts a model's judgement beside
    # the rule the pipeline runs. The rule is wired; asking the model on every
    # product is a call per product that nothing yet asks for.
    "build_launch_peers", "assess_intensity", "peers_at_launch", "compare_to_rule",
    # Adjudication verdicts. The arithmetic they wrap is used; the verdicts
    # are not, because nothing yet asks them. See the plan.
    "adjudicate_split_ownership_quarter", "adjudicate_total_against_parts",
    "adjudicate_reported_value", "adjudicate_positional_solutions",
    # Readers kept for the evals that compare against them.
    "html_tables", "extract_revenue_rows", "read_positional_block",
    # Domain types, constructed in tests and by callers outside this package.
    "CompetitiveIntensity", "PeakEstimateType", "PharmaAssertion", "RevenueCandidate",
    "is_aggregate_formulation", "has_label_section_header", "select_assertion",
    "deduplicate_phase3_programs",
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


def test_the_exception_list_has_nothing_wired_on_it():
    """A name on SCRIPT_ONLY that app/ does call excuses nothing, and hides.

    The list is what the check above skips. An entry that was true when it was
    written and has since been wired keeps that name unwatched: the capability
    could be unwired again and no test would say so. `reading_rank` sat here
    while the orchestrator called it.
    """
    in_app = _references(APP)
    defined = _definitions()
    stale = sorted(
        name for name in SCRIPT_ONLY if name in defined and in_app.get(name)
    )
    assert not stale, (
        "these are on SCRIPT_ONLY and are called by app/:\n  " + "\n  ".join(stale)
        + "\nRemove them from the list so the check above watches them again."
    )


def test_every_exception_names_something_that_exists():
    """A name that no longer exists cannot be the reason for an exception."""
    defined = _definitions()
    missing = sorted(name for name in SCRIPT_ONLY if name not in defined)
    assert not missing, f"SCRIPT_ONLY names things that are not defined: {missing}"
