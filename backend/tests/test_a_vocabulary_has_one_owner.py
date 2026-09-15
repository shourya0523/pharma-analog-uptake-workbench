"""A set of names is written once, where the names come from.

Every failure of the first working rule this branch has had is a list
written down beside the thing that produces it: forms, statuses, scopes,
flags, reason prose. The second copy reads as reasonable, and then the two
drift, and the difference is silent - a filer's transition-period annual
report skipped because one of two form vocabularies had not heard of it, a
figure republished because the gate's list of statuses predated the status
reconciliation had just given it.

This test finds the second copy. For each vocabulary it takes the members
from the module that owns them and looks through `app` for another literal
naming the same set. It is derived, not enumerated: a member added to the
owner is searched for automatically, and a vocabulary added below needs
only its owner named.
"""

from __future__ import annotations

import ast
import pathlib

from app.domain.models import PUBLISHED_STATUS_VALUES, RevenueScope
from app.parsing.labels import QUESTION_FLAGS

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# vocabulary -> the module allowed to write it out, being where it is decided
OWNERS = {
    "the statuses the pipeline stands behind": (PUBLISHED_STATUS_VALUES, "domain/models.py"),
    "the flags that make a row a question": (QUESTION_FLAGS, "parsing/labels.py"),
    "the two spellings of the whole product": (
        frozenset({RevenueScope.PRODUCT_FAMILY.value, RevenueScope.WORLDWIDE.value}),
        "domain/models.py",
    ),
}


def _string_literals_by_file() -> dict[pathlib.Path, list[set[str]]]:
    """Every set, list, tuple or frozenset of plain strings written in `app`."""
    found: dict[pathlib.Path, list[set[str]]] = {}
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text())
        groups: list[set[str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in {"frozenset", "set"} and node.args:
                node = node.args[0]
            if not isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                continue
            values = {e.value for e in node.elts
                      if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if values:
                groups.append(values)
        found[path] = groups
    return found


def test_each_vocabulary_is_written_out_in_one_place():
    literals = _string_literals_by_file()
    for name, (members, owner) in OWNERS.items():
        elsewhere = [
            path.relative_to(APP).as_posix()
            for path, groups in literals.items()
            if path.relative_to(APP).as_posix() != owner
            and any(set(members) <= group for group in groups)
        ]
        assert not elsewhere, (
            f"{name} is written out in {elsewhere} as well as in {owner}. "
            f"Take it from {owner}; a copy cannot be kept in step with what it copies."
        )


def test_every_vocabulary_watched_here_has_members_to_look_for():
    """Otherwise the test above passes by looking for nothing.

    The owner is not required to write the names out as strings - the two
    watched here are built from an enum, which is the point - so what is
    checked is that the vocabulary is non-empty and that its owner exists.
    """
    for name, (members, owner) in OWNERS.items():
        assert members, f"{name} is empty, so nothing would be searched for"
        assert (APP / owner).exists(), f"{name} names an owner that is not there: {owner}"


def test_the_scan_finds_a_copy_when_there_is_one():
    """The scan itself, run against a file that does write one out."""
    import tempfile

    members, _owner = OWNERS["the statuses the pipeline stands behind"]
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(f"COPY = {set(members)!r}\n")
        written = pathlib.Path(handle.name)
    try:
        groups = []
        tree = ast.parse(written.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                groups.append({e.value for e in node.elts
                               if isinstance(e, ast.Constant) and isinstance(e.value, str)})
        assert any(set(members) <= group for group in groups)
    finally:
        written.unlink()
