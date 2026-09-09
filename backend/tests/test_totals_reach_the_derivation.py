"""A producer says what it read. What is kept is the caller's decision.

A fourth quarter is never tagged and never printed as a quarter. A 10-K reports
the year: Gilead's FY2015 10-K carries 45 product facts and every one of them is
twelve months long. So Q4 exists only as the year less the nine months, and
`complete_series` can only compute it if the producer handed over the totals.

`quarterly_only` let a producer drop those totals before the caller ever saw
them, and it cost this project the fourth quarter three times:

* `_extract_revenue` called `extract_revenue_candidates` without the argument,
  and eleven end-to-end runs emitted zero derived rows - see
  `test_derivation_is_reachable.py`, which was written for that.
* The orchestrator's call to `candidates_from_instance` carried a five-line
  comment explaining why it had to pass False, which is a scar, not a design.
* A coverage measurement of the bulk reader took the default and lost 182 of
  684 rows - 26.6% of the band - to a flag nobody meant to set.

Twice the call site was fixed and the parameter left in place, so the next
caller walked into it. The parameter is now gone. It duplicated a decision the
orchestrator already makes and has to make: it routes each candidate by
`period_type`, quarters to datapoints and totals to the derivation pool, and
only the orchestrator knows both destinations. A producer copy of that decision
could never add anything - it could only delete one branch, and delete it so
quietly that an empty derivation pool looked exactly like a filing that had no
totals to begin with.

This test keeps the parameter from coming back. Selecting quarters is one
comprehension at the call site, which is what every caller now writes.
"""

from __future__ import annotations

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
FLAG = "quarterly_only"


def _producers_taking_the_flag() -> list[str]:
    """Any function in app/ that lets a caller ask it to withhold totals."""
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            names = (
                [a.arg for a in args.args]
                + [a.arg for a in args.kwonlyargs]
                + [a.arg for a in args.posonlyargs]
            )
            if FLAG in names:
                offenders.append(f"{path.relative_to(APP)}::{node.name}")
    return offenders


def test_no_producer_lets_a_caller_withhold_period_totals():
    offenders = _producers_taking_the_flag()
    assert not offenders, (
        f"{FLAG} is back in: {', '.join(offenders)}. The orchestrator already "
        "routes candidates by period_type - quarters to datapoints, totals to "
        "the derivation pool - and it is the only place that knows both "
        "destinations. A producer that drops the totals first deletes every "
        "fourth quarter, and does it so quietly that the empty derivation pool "
        "looks like a filing with no totals in it. Filter at the call site."
    )


def test_nothing_still_passes_the_flag():
    """A stale argument would be a TypeError at runtime, not at import."""
    passing = []
    for root in (APP, SCRIPTS):
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and any(
                    kw.arg == FLAG for kw in node.keywords
                ):
                    passing.append(f"{path.name}:{node.lineno}")
    assert not passing, f"{FLAG} is still passed at: {', '.join(passing)}"
