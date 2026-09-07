"""The model path reads meaning from the fingerprint, never from the document's shape.

Three locks. (1) No module on the model path imports the degraded readers
or the legacy extractors. (2) Every regular expression on the model path is
listed in a manifest with a one-line justification; a new or changed
pattern fails here until it is added, and the allowed kinds are number
formatting, EDGAR artefacts, contract format validation, money or revenue
detection for triage, and label normalisation for scoring. (3) The
degraded package is frozen: it is unscored, so a change to it counts
toward nothing and must be made deliberately by updating the freeze file.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

MODEL_PATH = [
    "app/extraction/described.py",
    "app/extraction/columns.py",
    "app/extraction/series.py",
    "app/extraction/readers.py",
    "app/extraction/units.py",
    "app/fingerprint/llm.py",
    "app/fingerprint/triage.py",
    "app/sourcing/edgar.py",
    "app/benchmark/schema.py",
]
FORBIDDEN_IMPORTS = (
    "app.extraction.degraded",
    "app.extraction.extract",
    "app.extraction.fingerprint",
    "app.extraction.candidates",
    "app.extraction.positional",
    "app.connectors.sources",
)
DEGRADED = BACKEND / "app" / "extraction" / "degraded"


def _imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _regex_literals(tree: ast.AST) -> set[str]:
    """Every string literal handed to a function of the ``re`` module."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "re"):
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.add(first.value)
    return found


def test_model_path_modules_do_not_import_the_degraded_or_legacy_readers():
    offenders = []
    for relative in MODEL_PATH:
        tree = ast.parse((BACKEND / relative).read_text())
        for name in sorted(_imports(tree)):
            if name.startswith(FORBIDDEN_IMPORTS):
                offenders.append(f"{relative} imports {name}")
    assert not offenders, "\n".join(offenders)


def test_every_regex_on_the_model_path_is_in_the_manifest_with_a_reason():
    manifest = json.loads((FIXTURES / "model_path_regexes.json").read_text())
    listed = {(entry["file"], entry["pattern"]): entry.get("why", "") for entry in manifest}
    found: set[tuple[str, str]] = set()
    for relative in MODEL_PATH:
        tree = ast.parse((BACKEND / relative).read_text())
        found.update((relative, pattern) for pattern in _regex_literals(tree))
    unlisted = sorted(found - set(listed))
    stale = sorted(set(listed) - found)
    unjustified = sorted(key for key, why in listed.items() if not why.strip())
    problems = []
    if unlisted:
        problems.append("patterns not in tests/fixtures/model_path_regexes.json (add each with a one-line why, or read the "
                        "meaning from the fingerprint instead):\n  " + "\n  ".join(f"{f}: {p!r}" for f, p in unlisted))
    if stale:
        problems.append("manifest entries no longer in the code:\n  " + "\n  ".join(f"{f}: {p!r}" for f, p in stale))
    if unjustified:
        problems.append("manifest entries without a why:\n  " + "\n  ".join(f"{f}: {p!r}" for f, p in unjustified))
    assert not problems, "\n\n".join(problems)


def test_the_degraded_package_is_frozen():
    freeze = json.loads((FIXTURES / "degraded_freeze.json").read_text())
    current = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(DEGRADED.glob("*.py"))
    }
    assert current == freeze, (
        "app/extraction/degraded changed. Degraded mode is unscored: a change there never counts toward a gate. "
        "If the change is deliberate, regenerate tests/fixtures/degraded_freeze.json "
        "(uv run python -m tests.freeze_degraded) and say so in the commit."
    )
