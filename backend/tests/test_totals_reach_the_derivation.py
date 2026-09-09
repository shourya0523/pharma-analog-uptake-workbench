"""No producer may default to withholding the totals a Q4 is derived from.

A fourth quarter is never tagged and never printed as a quarter. A 10-K reports
the year: Gilead's FY2015 10-K carries 45 product facts and every one of them is
twelve months long. So Q4 exists only as the year less the nine months, and
`complete_series` can only compute it if the producer handed over the totals.

`quarterly_only=True` drops exactly those totals. It has cost this project the
fourth quarter three times:

* `_extract_revenue` called `extract_revenue_candidates` without the argument,
  and eleven end-to-end runs emitted zero derived rows - see
  `test_derivation_is_reachable.py`, which was written for that.
* The orchestrator's call to `candidates_from_instance` carries a five-line
  comment explaining why it must pass False, which is a scar, not a design.
* A coverage measurement of the bulk reader took the default and lost 182 of
  684 rows - 26.6% of the band - to a flag nobody meant to set.

Every one of the eight call sites in this repository passes False. Nothing
wants True. A default that no caller wants and that silently deletes a quarter
is a trap, so this test holds the defaults where they belong; fixing the call
site and leaving the default armed is what let it fire three times.
"""

from __future__ import annotations

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
FLAG = "quarterly_only"


def _defaults() -> dict[str, bool]:
    """Every function in app/ taking the flag, and what it defaults to."""
    found: dict[str, bool] = {}
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            names = [a.arg for a in args.kwonlyargs]
            if FLAG not in names:
                continue
            default = args.kw_defaults[names.index(FLAG)]
            where = f"{path.relative_to(APP)}::{node.name}"
            found[where] = default is not None and getattr(default, "value", None) is True
    return found


def test_the_flag_exists_somewhere():
    """If this fails the test is stale, not the code."""
    assert _defaults(), f"no function in app/ takes {FLAG}; delete or update this test"


def test_no_producer_withholds_period_totals_by_default():
    armed = [where for where, defaults_true in _defaults().items() if defaults_true]
    assert not armed, (
        f"{FLAG} defaults to True in: {', '.join(armed)}. A caller that takes "
        "the default loses every fourth quarter, because Q4 is only ever the "
        "year less the nine months. Default to False and let a caller that "
        "genuinely wants quarters alone ask for them."
    )
