"""The gold review tool is gold-side. The pipeline may not reach it.

The tool that serves gold rows to people checking them lives in its own
repository (shourya0523/gold-verification-app), with a Supabase database of
their verdicts. Both are the answer key wearing a different hat (CLAUDE.md
rule 3), so the application never names the tool, its database or the
verdicts exported from it.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
APP = REPO / "backend" / "app"

# How the application could reach the tool: its name, its database service, or
# the verdict snapshot it writes into this repository.
NAMES_THE_TOOL = re.compile(r"verification[-_]app|supabase|human_verdicts", re.IGNORECASE)


def _text_files(root: pathlib.Path):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix not in {".pyc", ".png", ".pdf", ".xlsx"}:
            try:
                yield path, path.read_text()
            except UnicodeDecodeError:
                continue


def test_the_application_never_names_the_verification_app():
    offenders = [str(p.relative_to(REPO)) for p, text in _text_files(APP) if NAMES_THE_TOOL.search(text)]
    assert offenders == []
