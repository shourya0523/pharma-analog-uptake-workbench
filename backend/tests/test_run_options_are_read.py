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


def _names_used_in_app() -> set[str]:
    """Every attribute and string literal `app/` mentions, bar the declaration.

    Attributes catch `options.transcripts`; string constants catch a field
    reached by name through `getattr` or a dict. The module that declares the
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
            if isinstance(node, ast.Attribute):
                used.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                used.add(node.value)
    return used


def test_no_run_option_is_accepted_and_never_read():
    used = _names_used_in_app()
    unread = sorted(name for name in ExtractionOptions.model_fields if name not in used)
    assert not unread, (
        f"these run options are accepted, stored in options_json and read by "
        f"nothing in app/: {unread}. Wire them, or remove them so the run "
        f"record stops claiming a choice nobody had."
    )
