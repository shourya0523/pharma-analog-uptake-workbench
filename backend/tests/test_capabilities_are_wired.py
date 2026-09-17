"""Every capability this repo builds is used by the pipeline that ships.

An eval that calls a function directly reports a number the product cannot
produce, so this test discovers the capabilities rather than naming them: a
public function or class in ``app/`` is wired when a chain of references
reaches it from the code a framework starts - the ASGI app, the routes, the
settings, the ORM models, and any module that is itself a process entry point.
A name nothing reaches is either a capability nobody wired, or dead code. Both
are worth failing over.

Reachability rather than one reference anywhere in ``app/``, because a
reference is not a route: if ``read_calderon`` is called only by
``record_calderon`` and nothing calls *that*, the pair reaches no user, and a
whole ring of such functions can hold each other up.

``SCRIPT_ONLY`` is the exception list. Every entry carries the kind of
exception it claims to be, and ``test_every_exception_is_what_it_says``
checks that claim against what actually reaches the name, so a reason cannot
go quietly false the way a comment can.
"""

from __future__ import annotations

import ast
import collections
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
TESTS = pathlib.Path(__file__).resolve().parent
SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"

# Invoked by a framework, not by our code: routes, settings, ORM models.
# A module that runs itself (``if __name__ == "__main__":``) is a root too, and
# is found by reading the modules rather than named here.
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
    # Remediation, run against a job that has already finished, and the record
    # it hands back to the script that ran it.
    "backfill_job": SCRIPT,
    "BackfillResult": SCRIPT,
    # The analytics layer, which the API does not expose. Wiring or deleting
    # it is open work, not an accident to be silently tolerated. Each module's
    # types and the helpers only that module calls are unreached for that one
    # reason and are listed beside it, rather than reading as separate work.
    "rank_analogs": NOT_WIRED,
    "score_analog": NOT_WIRED,
    "normalise": NOT_WIRED,
    "AnalogMatch": NOT_WIRED,
    "ProductProfile": NOT_WIRED,
    "calculate_revenue_uptake": NOT_WIRED,
    "time_to_ninety_percent_peak": NOT_WIRED,
    "UptakePoint": NOT_WIRED,
    "TimeToThreshold": NOT_WIRED,
    "select_peak_estimate": NOT_WIRED,
    "aggregate_comparable_sales": NOT_WIRED,
    "peak_eligible": NOT_WIRED,
    "PeakEstimate": NOT_WIRED,
    "calculate_competitive_snapshot": NOT_WIRED,
    "build_launch_peers": NOT_WIRED,
    "count_universe_entries": NOT_WIRED,
    "categorize_snapshots": NOT_WIRED,
    "band_for_peer_count": NOT_WIRED,
    "CompetitivePeer": NOT_WIRED,
    "CompetitiveSnapshot": NOT_WIRED,
    "RegistryEntry": NOT_WIRED,
    "assess_intensity": NOT_WIRED,
    "peers_at_launch": NOT_WIRED,
    "compare_to_rule": NOT_WIRED,
    "ChatJSON": NOT_WIRED,
    "IntensityAssessment": NOT_WIRED,
    "IntensityComparison": NOT_WIRED,
    "Peer": NOT_WIRED,
    # Adjudication verdicts. The arithmetic they wrap is used; the verdicts
    # are not, because nothing yet asks them. `rounding_tolerance` is the slack
    # those verdicts allow, and only they ask for it. See the plan.
    "adjudicate_split_ownership_quarter": NOT_WIRED,
    "adjudicate_total_against_parts": NOT_WIRED,
    "adjudicate_reported_value": NOT_WIRED,
    "adjudicate_positional_solutions": NOT_WIRED,
    "rounding_tolerance": NOT_WIRED,
    # Assembling a quarter split by an acquisition, for which nothing asks: the
    # derivation the pipeline runs is the residual arithmetic beside it.
    "assemble_split_ownership_quarter": NOT_WIRED,
    # Alternative readers. `app/` reads through another path and these are
    # exercised only by tests. An eval cannot reach them either:
    # `test_the_eval_runs_the_pipeline` fails any `scripts/eval*.py` that
    # imports `app`, so "kept for the evals" was never a thing they could be.
    # `html_tables` is what `html_table_grids` reaches a caller through, and
    # `read_positional_block` is what builds a `PositionalRow`, so each pair
    # stands or falls together.
    "html_tables": NOT_WIRED,
    "html_table_grids": NOT_WIRED,
    "read_positional_block": NOT_WIRED,
    "PositionalRow": NOT_WIRED,
    # An importer for a file an analyst prepares by hand - with no caller and
    # no route, so there is nothing to hand the file to - and the row and the
    # error it reports with.
    "read_peak_sales_csv": NOT_WIRED,
    "PeakImportRow": NOT_WIRED,
    "PeakImportError": NOT_WIRED,
    # Domain types and predicates constructed only by tests. The pipeline
    # builds its equivalents inline, so these are a second spelling of a
    # capability rather than the one that ships. `Citation` is not even that:
    # nothing anywhere builds one, the pipeline writing a citation as the
    # `citation_json` of the row it belongs to.
    "CompetitiveIntensity": NOT_WIRED,
    "PharmaAssertion": NOT_WIRED,
    "RevenueCandidate": NOT_WIRED,
    "Citation": NOT_WIRED,
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
            public = isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ) and not node.name.startswith("_")
            if public:
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


def _referenced_names(node: ast.AST) -> set[str]:
    """Every name the subtree mentions, under the spelling it is defined with."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
        elif isinstance(child, ast.ImportFrom):
            names.update(alias.name for alias in child.names)
        elif isinstance(child, ast.Import):
            names.update(alias.name.split(".")[-1] for alias in child.names)
    return names


def _runs_itself(node: ast.stmt) -> bool:
    return isinstance(node, ast.If) and ast.unparse(node.test).replace("'", '"') == (
        '__name__ == "__main__"'
    )


def _graph() -> tuple[dict, dict, set]:
    """`app/` as a graph over its own definitions, with the framework's roots.

    A node is a top-level definition, or a module's own body under the name
    ``<module>``. An edge is a name the definition mentions, joined to every
    definition that spells itself that way: an import, a call, an attribute.
    Joining on the name over-approximates - two modules may define `settles`
    and an edge is drawn to both - and it errs towards calling a name wired,
    which is the direction that does not invent work.
    """
    owners: collections.defaultdict = collections.defaultdict(set)
    edges: collections.defaultdict = collections.defaultdict(set)
    roots: set = set()
    for path in sorted(APP.rglob("*.py")):
        relative = str(path.relative_to(APP))
        module = (relative, "<module>")
        body = ast.parse(path.read_text()).body
        framework = relative.startswith(FRAMEWORK) or any(_runs_itself(n) for n in body)
        if framework:
            roots.add(module)
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                here = (relative, node.name)
                owners[node.name].add(here)
                edges[here] |= _referenced_names(node) - {node.name}
                if framework:
                    roots.add(here)
            else:
                edges[module] |= _referenced_names(node)
    return owners, edges, roots


def _reaches(seed: set) -> set[str]:
    """The names a chain of references reaches, starting from `seed`."""
    owners, edges, _roots = _graph()
    seen, pending = set(seed), list(seed)
    while pending:
        node = pending.pop()
        # Reaching anything in a module means the module was imported, so its
        # own body ran: a registry built at import time wires what it holds.
        nodes = [(node[0], "<module>")] if node[1] != "<module>" else []
        nodes += [
            target for name in edges.get(node, ()) for target in owners.get(name, ())
        ]
        for target in nodes:
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return {name for _path, name in seen}


def _wired() -> set[str]:
    """The names the framework reaches: routes, settings, models, a worker."""
    return _reaches(_graph()[2])


def _reached_from_scripts() -> set[str]:
    """The names `scripts/` reaches, through `app/`'s own chains."""
    if not SCRIPTS.exists():
        return set()
    owners = _graph()[0]
    named: set[str] = set()
    for path in sorted(SCRIPTS.rglob("*.py")):
        try:
            named |= _referenced_names(ast.parse(path.read_text()))
        except SyntaxError:
            continue
    return _reaches({target for name in named for target in owners.get(name, ())})


def test_no_capability_is_reachable_only_from_an_eval():
    wired = _wired()
    from_scripts = _reached_from_scripts()
    in_tests = _references(TESTS)

    unwired = []
    for name, home in sorted(_definitions().items()):
        if name in wired or name in SCRIPT_ONLY:
            continue
        reached = "tests/scripts" if (in_tests.get(name) or name in from_scripts) else "nothing"
        unwired.append(f"{name} ({home}) is called by {reached}, never from app/'s roots")

    assert not unwired, (
        "\n".join(unwired)
        + "\n\nWire it into the pipeline, delete it, or add it to SCRIPT_ONLY "
        "with the reason it is correctly one-directional."
    )


def test_every_exception_is_what_it_says():
    """An exception's reason is checked against what reaches the name.

    The list is hand-maintained, which is fine - what is not fine is a reason
    nobody re-checks. The three ways an entry can be wrong are each a failure
    here: the framework reaches it after all, so the entry does nothing; it
    claims a script and none reaches it; or it claims nothing runs it and a
    script does.
    """
    wired = _wired()
    from_scripts = _reached_from_scripts()
    in_app = _references(APP)
    definitions = _definitions()

    wrong = []
    for name, kind in sorted(SCRIPT_ONLY.items()):
        if name not in definitions:
            wrong.append(f"{name}: on the list and defined nowhere in app/")
            continue
        if name in wired:
            where = sorted(str(p.relative_to(APP)) for p in in_app.get(name, ()))
            wrong.append(
                f"{name}: reached from app/'s roots ({', '.join(where)}), so the "
                f"exception is a no-op - drop it from the list"
            )
            continue
        if kind == SCRIPT and name not in from_scripts:
            wrong.append(f"{name}: marked {SCRIPT!r} and no script reaches it")
        if kind == NOT_WIRED and name in from_scripts:
            wrong.append(f"{name}: marked {NOT_WIRED!r} but a script in {SCRIPTS.name}/ reaches it")

    assert not wrong, "\n".join(wrong)


def test_the_list_says_which_kind_each_entry_is():
    """A bare name carries no claim, and a claim nobody states cannot be
    checked. Every entry has to be one of the kinds above."""
    kinds = {SCRIPT, NOT_WIRED}
    bad = {name: kind for name, kind in SCRIPT_ONLY.items() if kind not in kinds}
    assert not bad, f"unknown exception kinds: {bad}"
