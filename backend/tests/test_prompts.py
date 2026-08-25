"""Prompts must not hand the model a period it can copy instead of reading the source.

Two production defects came from copyable prompt content: revenue_extractor shipped
"period": "YYYYQn or YYYY" and the model wrote literal "YYYY" periods to the database,
then a concrete "2024Q1" example caused 2026 values to be labelled 2024Q1.
"""

import re

from app.llm.client import PROMPTS_DIR, load_prompt
from app.quality.candidate_filters import is_placeholder_period

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
PERIOD_PROMPTS = ["revenue_extractor", "completeness_analyzer"]


def _prompt_text(name: str) -> str:
    prompt = load_prompt(name)
    return f"{prompt['system']}\n{prompt['user_template']}"


def test_period_prompts_contain_no_copyable_year():
    for name in PERIOD_PROMPTS:
        found = YEAR_RE.findall(_prompt_text(name))
        assert not found, f"{name} exposes copyable year(s) {found}"


def test_period_prompts_have_no_yyyy_placeholder():
    for name in PERIOD_PROMPTS:
        assert "YYYY" not in _prompt_text(name), f"{name} still ships a YYYY placeholder"


def test_prompt_period_examples_are_dropped_if_echoed():
    """Any period example a model copies verbatim must be rejected downstream."""
    examples = re.findall(r'"period":\s*"([^"]*)"', _prompt_text("revenue_extractor"))
    examples += re.findall(r'"period":\s*"([^"]*)"', _prompt_text("completeness_analyzer"))
    assert examples, "expected the response skeletons to define a period field"
    for example in examples:
        assert is_placeholder_period(example), f"echoing {example!r} would be stored as a real period"


def test_every_prompt_loads():
    for path in sorted(PROMPTS_DIR.glob("*.yaml")):
        prompt = load_prompt(path.stem)
        assert prompt["system"].strip()
        assert prompt["user_template"].strip()
