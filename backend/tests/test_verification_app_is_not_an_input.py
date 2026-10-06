"""The verification app is gold-side. The pipeline may not read it.

tools/verification-app serves gold rows to people checking them, and its
database holds their verdicts on gold. Both are the answer key wearing a
different hat (CLAUDE.md rule 3), so the application never names the app,
its data or its database, and the app never names a file the pipeline reads.
"""

from __future__ import annotations

import pathlib
import re

from tests.test_gold_is_not_an_input import PIPELINE_INPUTS

REPO = pathlib.Path(__file__).resolve().parents[2]
APP = REPO / "backend" / "app"
TOOL = REPO / "tools" / "verification-app"

# How the application could reach the tool: its directory, its database
# service, or the verdicts exported from it.
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


def test_the_verification_app_never_names_a_pipeline_input():
    assert TOOL.is_dir()
    offenders = [
        (str(p.relative_to(REPO)), name)
        for p, text in _text_files(TOOL)
        if "data" not in p.relative_to(TOOL).parts
        for name in PIPELINE_INPUTS
        if name in text
    ]
    assert offenders == []
