"""Gold is the answer key. Nothing the pipeline runs on may be derived from it.

There is already a test that the gold builder imports nothing from the
application, so the dataset cannot be quietly produced by the thing it judges.
This is the same rule in the other direction, which was missing and was broken
within a day of the gap existing: a register mapping XBRL member names to
products was built by reading gold's product list, which made a file the
pipeline reads at run time a function of the answer key.

The failure is not that a wrong number gets published. It is that the score
stops meaning anything - a pipeline holding gold's decisions is being asked
whether it agrees with itself.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
APP = REPO / "backend" / "app"
SEED = REPO / "seed"

# Files under seed/ that the pipeline itself reads. Gold is not among them.
# A script that writes one of these and also reads gold is the channel this
# test exists to close.
PIPELINE_INPUTS = ("product_attributes.csv", "xbrl_members.csv", "example_drugs.csv")

# The gold builder reads pipeline reference data and writes gold. That is the
# permitted direction and the reason this test is about direction rather than
# about contact: reference data may flow into the answer key, never back.
BUILDS_GOLD = {"build_independent_gold.py"}

GOLD_MARKERS = ("seed/gold", 'seed" / "gold', "quarterly_revenue.jsonl",
                "product_profiles.jsonl", "series_coverage.jsonl", "peak_sales.jsonl")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Identity of every Constant that is a docstring, so prose can mention gold."""
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            found.add(id(body[0].value))
    return found


def _code_mentions_gold(source: str) -> bool:
    """Gold named anywhere but a docstring or a comment."""
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            if any(marker in node.value for marker in GOLD_MARKERS):
                return True
    return False


def test_the_application_never_reads_the_answer_key():
    """No module under app/ may open, name or import a gold file."""
    offenders = [
        str(path.relative_to(REPO))
        for path in APP.rglob("*.py")
        if _code_mentions_gold(path.read_text())
    ]
    assert not offenders, "application code reaching for gold:\n  " + "\n  ".join(offenders)


def test_files_the_pipeline_reads_are_not_built_from_gold():
    """A script naming a pipeline input must not also read the answer key.

    seed/ holds both: gold, which scores the pipeline, and the files the
    pipeline runs on - product attributes, the XBRL member register. A script
    that reads the first and writes the second is a channel from the oracle
    into the thing being measured, and it does not announce itself at run time.
    The gold builder reads product_attributes.csv, which is the same dependency
    pointing the other way and is the direction that is allowed.
    """
    offenders = []
    for path in (REPO / "scripts").rglob("*.py"):
        if path.name in BUILDS_GOLD:
            continue
        source = path.read_text()
        touches_input = any(name in source for name in PIPELINE_INPUTS)
        if touches_input and _code_mentions_gold(source):
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, (
        "these write into seed/ and read gold:\n  " + "\n  ".join(offenders)
        + "\nBuild pipeline inputs from pipeline reference data, not from the answer key."
    )


def test_the_member_register_names_only_products_we_track_independently():
    """Every product in the register comes from seed/product_attributes.csv.

    A product that appears in the register but not in the pipeline's own
    reference data could only have come from somewhere else - in practice, from
    gold.
    """
    import csv

    register = SEED / "xbrl_members.csv"
    if not register.exists():
        return
    with (SEED / "product_attributes.csv").open(newline="") as handle:
        known = {row["drug_name"].strip() for row in csv.DictReader(handle)}
    with register.open(newline="") as handle:
        named = {row["product"].strip() for row in csv.DictReader(handle)
                 if row["product"].strip() not in ("", "-")}
    assert named <= known, (
        f"register names products absent from product_attributes.csv: {sorted(named - known)}"
    )
