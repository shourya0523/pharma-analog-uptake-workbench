"""A continuation carries a product on after its own series stops, and says how.

Companion lines are kept beside a gold series and never scored. A continuation
is the companion that picks up where the series ends - the product folded into
a broader line, reported by a new owner, or under a new label - so the rules
here are about that boundary and about what the line admits it holds.
"""

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD = REPO_ROOT / "seed" / "gold"
BUILDER_PATH = REPO_ROOT / "scripts" / "build_independent_gold.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_independent_gold", BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BUILDER = load_builder()


def meta(**continuation):
    return {"drug_name": "Calderon", "continuation": continuation}


def test_a_broader_line_that_starts_after_the_series_stands():
    problems = BUILDER.continuation_problems(
        meta(relation="broader_line", line_includes=["NuVessa"],
             evidence="footnote: 'Includes Calderon and NuVessa'"),
        ["2025Q1", "2025Q2"], series_end="2024Q4")
    assert problems == []


def test_a_continuation_may_not_overlap_the_series_it_continues():
    problems = BUILDER.continuation_problems(
        meta(relation="renamed_line", evidence="'Calderon Group'"),
        ["2024Q4", "2025Q1"], series_end="2024Q4")
    assert any("not after the series' last quarter" in problem for problem in problems)


def test_a_broader_line_must_say_what_else_it_holds():
    problems = BUILDER.continuation_problems(
        meta(relation="broader_line", evidence="footnote"), ["2025Q1"], series_end="2024Q4")
    assert any("line_includes" in problem for problem in problems)


def test_the_relation_comes_from_the_fixed_set_and_is_evidenced():
    problems = BUILDER.continuation_problems(
        meta(relation="close_enough"), ["2025Q1"], series_end="2024Q4")
    assert any("relation must be one of" in problem for problem in problems)
    assert any("quote the document" in problem for problem in problems)


def test_a_product_without_a_series_has_nothing_to_continue():
    problems = BUILDER.continuation_problems(
        meta(relation="successor_issuer", evidence="8-K"), ["2025Q1"], series_end=None)
    assert any("no quarterly gold series" in problem for problem in problems)


def test_a_plain_companion_is_not_held_to_the_continuation_rules():
    assert BUILDER.continuation_problems({"drug_name": "Calderon"}, ["2010Q1"], "2024Q4") == []


def test_every_continuation_in_gold_starts_after_its_series_and_is_never_scored():
    quarterly = [json.loads(line) for line in (GOLD / "quarterly_revenue.jsonl").read_text().splitlines()]
    ends: dict[str, str] = {}
    for row in quarterly:
        ends[row["drug_name"]] = max(ends.get(row["drug_name"], ""), row["period"])
    companions = [json.loads(line) for line in (GOLD / "companion_series.jsonl").read_text().splitlines()
                  if line.strip()]
    for row in companions:
        if row["series_role"] != "continuation":
            continue
        assert row["period"] > ends[row["drug_name"]], row["gold_id"]
        assert row["continuation_relation"] in BUILDER.CONTINUATION_RELATIONS, row["gold_id"]
        assert row["gold_id"] not in {q["gold_id"] for q in quarterly}, row["gold_id"]
