"""Every run option the API accepts changes something the pipeline does.

An option the API declares is persisted into the run's `options_json`, so a
caller reading the run back believes the thing was toggled. If nothing in
`app/` reads it, unticking it changes nothing and the record says otherwise -
a false account of what was run, which is worse than an option that is not
offered at all.

The declared set is read off the model, and the readers are found by walking
`app/`, so neither side is written down here.
"""

from __future__ import annotations

import ast
import pathlib

from app.domain.models import ExtractionOptions

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
DECLARATION = APP / "domain" / "models.py"


def _key_of(node: ast.AST) -> str | None:
    """The option name an expression reads, where it reads one by key.

    A string is a reader only where it is the key: the argument of `.get`,
    `.pop` or `.setdefault`, the second of `getattr`, or a subscript. The same
    string in a prompt, a log line or a docstring reads nothing, and counting
    it made an option look wired by the mention of its own name.
    """
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        key = node.slice
        return key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None
    if isinstance(node, ast.Call):
        function = node.func
        at = None
        if isinstance(function, ast.Attribute) and function.attr in ("get", "pop", "setdefault"):
            at = 0
        elif isinstance(function, ast.Name) and function.id == "getattr":
            at = 1
        if at is not None and len(node.args) > at:
            argument = node.args[at]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                return argument.value
    return None


def _names_used_in_app() -> set[str]:
    """Every name `app/` reads as an option, bar the declaration.

    Attribute access catches `options.transcripts`; a key catches an option
    reached through the dict the run stores. The module that declares the
    model is excluded, or every field would find itself.
    """
    used: set[str] = set()
    for path in APP.rglob("*.py"):
        if path == DECLARATION:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            key = _key_of(node)
            if key is not None:
                used.add(key)
    return used


def test_no_run_option_is_accepted_and_never_read():
    used = _names_used_in_app()
    unread = sorted(name for name in ExtractionOptions.model_fields if name not in used)
    assert not unread, (
        f"these run options are accepted, stored in options_json and read by "
        f"nothing in app/: {unread}. Wire them, or remove them so the run "
        f"record stops claiming a choice nobody had."
    )
