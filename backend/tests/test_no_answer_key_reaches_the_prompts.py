"""Nothing the pipeline reads at run time names a product an answer key holds.

Rule 3 says nothing the pipeline reads may come from the answer key, and rule
5 says an example of shape is spelled with an invented name, because a real
one teaches the next reader - and the next model - which brands the answers
are about. A prompt is both at once: it is read by the pipeline, and it is
prose the model reasons from.

The member resolver's prompt named three of the eighteen members its own
holdout scores, including a roll-up it told the model to refuse. The resolver
was then measured on that holdout. A test is the only thing that catches
this, because the file is data and nothing imports it.
"""

from __future__ import annotations

import pathlib
import re

from tests.answer_keys import scored_products

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# A name short enough to be an ordinary word would match prose that is not
# about it at all. The vocabulary of invented names is in CLAUDE.md.
SHORTEST_NAME = 4


def _files_the_pipeline_reads() -> list[pathlib.Path]:
    """Everything under `app` that ships with it: code, prompts and data."""
    return [p for p in sorted(APP.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".yaml", ".yml", ".json", ".csv"}
            and "__pycache__" not in p.parts]


def test_no_prompt_or_module_names_a_product_an_answer_key_scores():
    names = {n for n in scored_products() if len(n) >= SHORTEST_NAME}
    assert names, "no answer key was found, so this test is watching nothing"
    offenders: dict[str, list[str]] = {}
    for path in _files_the_pipeline_reads():
        text = path.read_text(errors="ignore")
        found = sorted({n for n in names if re.search(rf"\b{re.escape(n)}\b", text)})
        if found:
            offenders[str(path.relative_to(APP))] = found
    assert not offenders, (
        f"these name a product an answer key holds: {offenders}. Spell the shape "
        f"with an invented name - Calderon, Calderon XR, Nebulized Calderon, NuVessa."
    )
