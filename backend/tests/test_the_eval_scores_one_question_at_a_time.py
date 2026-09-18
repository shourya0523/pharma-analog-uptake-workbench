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

from tests.eval_harness import Server, load_eval, score_cases


def _published(period: str, value: float, scope: str) -> dict:
    return {"period": period, "value_normalized_usd_millions": value,
            "validation_status": "auto_pass", "revenue_scope": scope,
            "extraction_method": "acme_reader"}


CASE = {"drug_name": "Calderon",
        "expect": [{"period": "2024Q4", "value_normalized_usd_millions": 144.1}]}


def test_one_quarter_broken_out_by_region_is_not_a_conflict():
    ev = load_eval()
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
    ev = load_eval()
    assert ev.scope_key("Worldwide") == ev.scope_key("Product family")
    rows = ev.score(CASE, [
        _published("2024Q4", 144.1, "Worldwide"),
        _published("2024Q4", 138.5, "Product family"),
    ])
    assert rows[0]["state"] == "published, conflicting", rows


def test_two_figures_under_one_scope_still_conflict():
    ev = load_eval()
    rows = ev.score(CASE, [
        _published("2024Q4", 144.1, "U.S."),
        _published("2024Q4", 138.5, "U.S."),
        _published("2024Q4", 144.1, "Worldwide"),
    ])
    assert rows[0]["state"] == "published, conflicting", rows


def test_a_period_no_expectation_names_is_counted_rather_than_ignored():
    ev = load_eval()
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


def test_a_job_that_did_not_finish_is_not_scored(tmp_path, monkeypatch, capsys):
    """A mid-reconcile job holds every candidate for a quarter at once, which
    scores as the pipeline contradicting itself over a figure it had not
    finished choosing.

    Driven against a server whose job is still running, with the answer it
    would have been scored on sitting there to be taken.
    """
    ev = load_eval()
    case = {**CASE, "options": {"earnings_since": "2024-01-01",
                                "earnings_until": "2025-03-31"},
            "source": "an invented filing"}
    running = {"id": "job-1", "drug_name": "Calderon", "status": "running",
               "current_step": "reconcile"}
    server = Server([running], {"job-1": [_published("2024Q4", 144.1, "Worldwide")]})
    code, detail = score_cases(ev, server, [case], tmp_path, monkeypatch)

    assert detail[0]["scored"] is False, detail
    assert detail[0]["rows"] == [], "an unfinished job was scored on what it had reached"
    assert not any(path.startswith("/jobs/") for _verb, path in server.calls), (
        "the datapoints of an unfinished job were read back to be scored"
    )
    printed = capsys.readouterr().out
    assert "not scored and absent" in printed, (
        "an unscored case has to be named, or the denominator quietly shrinks"
    )
    assert "Calderon" in printed and code != 0
