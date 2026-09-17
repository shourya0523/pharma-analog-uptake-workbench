"""The gold case files are gold restated, and nothing else.

`seed/cases/gold_all.json` is what the headline is scored on. While it was
maintained by hand it held whatever gold held on the day someone last edited
it: the two drifted apart product by product, and a product gold had gained
was scored by nothing at all. A hand-maintained answer key does not announce
that it has gone stale, so this says it instead - the file must equal what
`scripts/build_gold_cases.py` produces from gold today, and gold's every
quarter must be in it.

The other property is rule 4's, mirrored: a set that only refuses is passed by
a system that always refuses, and a set that only publishes is passed by one
that publishes anything. Both answers have to be in the file, and both come
from gold's own records of absence.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from datetime import date, timedelta

REPO = pathlib.Path(__file__).resolve().parents[2]
CASES = REPO / "seed" / "cases"
BUILDER = REPO / "scripts" / "build_gold_cases.py"
GOLD_ROWS = REPO / "seed" / "gold" / "quarterly_revenue.jsonl"


def _builder():
    spec = importlib.util.spec_from_file_location("build_gold_cases", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _committed(name: str) -> list[dict]:
    return json.loads((CASES / name).read_text())


def test_the_case_file_is_what_the_builder_produces_from_gold():
    built = _builder().build()
    assert _committed("gold_all.json") == built, (
        "seed/cases/gold_all.json is not what gold produces today. "
        "Run: python scripts/build_gold_cases.py"
    )


def test_the_sample_is_a_subset_of_the_whole_and_is_derived_too():
    module = _builder()
    built = module.build()
    small = module.sample(built)
    assert _committed("gold_sample.json") == small, (
        "seed/cases/gold_sample.json is not what gold produces today. "
        "Run: python scripts/build_gold_cases.py"
    )
    assert all(case in built for case in small), "the sample holds a case gold does not"


def test_every_quarter_gold_holds_is_expected_exactly_once():
    """The hole the hand-maintained file had, stated as a property.

    Coverage is what the score is a fraction of. A product-year gold holds and
    the case file does not is not a lower score - it is a quarter nothing asks
    about, and the headline reads the same whether the pipeline would have got
    it right or wrong.
    """
    gold = [json.loads(line) for line in GOLD_ROWS.read_text().splitlines() if line.strip()]
    assert gold, "no gold rows found; this test would pass vacuously"
    expected: list[str] = [
        figure["gold_id"]
        for case in _committed("gold_all.json")
        for figure in case["expect"]
        if figure["value_normalized_usd_millions"] is not None
    ]
    assert sorted(expected) == sorted(row["gold_id"] for row in gold), (
        "the case file and gold hold different quarters"
    )


def test_both_answers_are_represented():
    """A set that only publishes is passed by a system that always publishes.

    Every figure in these files comes from gold's quarterly series, and every
    empty expectation from gold's own records of absence: the quarter after an
    issuer stopped reporting a line, and a product gold refused to build a
    series for. Neither is written by hand, so neither can be tuned.
    """
    for name in ("gold_all.json", "gold_sample.json"):
        figures = [e for case in _committed(name) for e in case["expect"]]
        stated = [e for e in figures if e["value_normalized_usd_millions"] is not None]
        empty = [e for e in figures if e["value_normalized_usd_millions"] is None]
        assert stated, f"{name} expects no figure"
        assert empty, f"{name} expects no silence"
        assert all(e.get("why") for e in empty), (
            f"{name}: an empty quarter without its reason cannot be told from a gap"
        )
        # The hard kind: the issuer still files and the product still sells,
        # and the line the analyst wants is gone. Without one, "nothing found"
        # and "correctly silent" are the same score.
        assert any(
            any(e["value_normalized_usd_millions"] is None for e in case["expect"])
            and any(e["value_normalized_usd_millions"] is not None for e in case["expect"])
            for case in _committed(name)
        ), f"{name}: no case mixes a stated quarter with an empty one"


def test_every_option_a_case_sets_is_one_the_api_declares():
    """An option the API does not declare is dropped on the way in without a
    word, so the case measures a configuration nobody chose. The declared set
    is read off the model rather than written down here."""
    from app.domain.models import ExtractionOptions

    declared = set(ExtractionOptions.model_fields)
    for name in ("gold_all.json", "gold_sample.json"):
        for case in _committed(name):
            unknown = set(case["options"]) - declared
            assert not unknown, (name, case["drug_name"], sorted(unknown))


def test_the_check_says_so_in_its_exit_status(monkeypatch):
    """A check that cannot fail reports to a reader and answers no caller.

    Asking for an option the committed files were not built with is the
    cheapest way to make them disagree with the builder, so it doubles as the
    check that the flag reaches every case rather than only the banner.
    """
    module = _builder()
    monkeypatch.setattr(sys, "argv", ["build_gold_cases.py", "--check"])
    assert module.main() == 0, "the committed files do not match what gold produces"
    monkeypatch.setattr(sys, "argv", ["build_gold_cases.py", "--check", "--openfda"])
    assert module.main() == 1, "a case file built for another configuration read as current"


def test_windows_reach_every_expected_quarter():
    """A quarter is reportable once it has ended; the annual report covering a
    year's last quarter follows within about a fiscal quarter of it. A window
    that closes before the report exists, or opens after every report of the
    quarter, scores nothing about that quarter."""
    for name in ("gold_all.json", "gold_sample.json"):
        for case in _committed(name):
            since = date.fromisoformat(case["options"]["earnings_since"])
            until = date.fromisoformat(case["options"]["earnings_until"])
            for figure in case["expect"]:
                ends = _builder().quarter_end(figure["period"])
                assert until >= ends + timedelta(days=1), (name, case["drug_name"], figure)
                assert since <= ends + timedelta(days=120), (name, case["drug_name"], figure)
