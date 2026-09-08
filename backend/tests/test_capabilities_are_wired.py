"""Every capability the pipeline builds is called by the pipeline.

Twice in this project a reader was written, tested, measured in an eval and
never wired into the application: the XBRL fact reader, and the two
derivations in ``extraction/derive.py``. Both times the eval reported a
number the product could not produce, which is worse than the missing
feature - it is a true measurement of something nobody was running.

An eval script may only measure what ``app/`` actually does, so a capability
reachable from ``scripts/`` and ``tests/`` alone fails here.
"""

from __future__ import annotations

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# Entry points that answer a revenue question. Adding one here is a claim that
# the pipeline uses it; if it does not, this test says so.
ENTRY_POINTS = {
    "candidates_from_instance": "app/extraction/tagged.py",
    "complete_series": "app/extraction/derive.py",
    "extract_revenue_candidates": "app/extraction/candidates.py",
    "read_prose": "app/extraction/prose.py",
}


def _calls_in(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    }


def test_every_entry_point_has_a_caller_in_the_application():
    modules = sorted(APP.rglob("*.py"))
    unwired = []
    for name, home in ENTRY_POINTS.items():
        callers = [
            path
            for path in modules
            if not str(path).endswith(home.split("app/", 1)[1])
            and name in _calls_in(path)
        ]
        if not callers:
            unwired.append(f"{name} (defined in {home}) is called by no module under app/")
    assert not unwired, "\n".join(unwired)
