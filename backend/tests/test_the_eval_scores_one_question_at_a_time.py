"""Two figures disagree only if they answer the same question.

The pipeline keeps a period's scopes apart - a worldwide figure, a U.S.
figure and an ex-U.S. figure are three answers to three questions. The eval
took the spread over all of them, so a filer that breaks a quarter out by
region read as the pipeline contradicting itself, and the expected figure
being present, published and returned did not stop it counting against the
score. The eval may not import the pipeline, so it carries the rule rather
than the module, and this is what says the two still agree.

Also here: what the score cannot see. It walks the case's expectations, so a
figure published for a period no expectation names is neither right nor
wrong, and a case file covering half of what a run answers reads exactly like
one covering all of it.
"""

from __future__ import annotations

import importlib.util
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]


def _load_eval():
    spec = importlib.util.spec_from_file_location("eval_script", REPO / "scripts" / "eval.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _published(period: str, value: float, scope: str) -> dict:
    return {"period": period, "value_normalized_usd_millions": value,
            "validation_status": "auto_pass", "revenue_scope": scope,
            "extraction_method": "acme_reader"}


CASE = {"drug_name": "Calderon",
        "expect": [{"period": "2024Q4", "value_normalized_usd_millions": 144.1}]}


def test_one_quarter_broken_out_by_region_is_not_a_conflict():
    ev = _load_eval()
    rows = ev.score(CASE, [
        _published("2024Q4", 144.1, "Worldwide"),
        _published("2024Q4", 124.1, "U.S."),
        _published("2024Q4", 20.0, "ex-U.S."),
    ])
    assert [r["state"] for r in rows] == ["published, correct"], rows
    assert rows[0]["read"] == 144.1


def test_the_two_spellings_of_the_whole_product_are_one_scope():
    """A sentence says Worldwide; a schedule's family line says Product
    family. Two different figures under those two labels are one question
    answered twice."""
    ev = _load_eval()
    assert ev.scope_key("Worldwide") == ev.scope_key("Product family")
    rows = ev.score(CASE, [
        _published("2024Q4", 144.1, "Worldwide"),
        _published("2024Q4", 138.5, "Product family"),
    ])
    assert rows[0]["state"] == "published, conflicting", rows


def test_two_figures_under_one_scope_still_conflict():
    ev = _load_eval()
    rows = ev.score(CASE, [
        _published("2024Q4", 144.1, "U.S."),
        _published("2024Q4", 138.5, "U.S."),
        _published("2024Q4", 144.1, "Worldwide"),
    ])
    assert rows[0]["state"] == "published, conflicting", rows


def test_a_period_no_expectation_names_is_counted_rather_than_ignored():
    ev = _load_eval()
    seen = ev.unexamined(CASE, [
        _published("2024Q4", 144.1, "Worldwide"),
        _published("2024Q3", 130.0, "Worldwide"),
        _published("2024Q2", 120.0, "U.S."),
        {"period": "2024Q1", "value_normalized_usd_millions": 110.0,
         "validation_status": "needs_review", "revenue_scope": "Worldwide"},
    ])
    assert seen == ["2024Q2", "2024Q3"], (
        "every published period the case says nothing about, and nothing the "
        "pipeline declined to stand behind"
    )


def test_a_job_that_did_not_finish_is_not_scored():
    """A mid-reconcile job holds every candidate for a quarter at once, which
    scores as the pipeline contradicting itself over a figure it had not
    finished choosing."""
    source = (REPO / "scripts" / "eval.py").read_text()
    assert '"scored": finished' in source, "the detail file records what was scored"
    assert "not scored and absent" in source, (
        "an unscored case has to be named, or the denominator quietly shrinks"
    )
