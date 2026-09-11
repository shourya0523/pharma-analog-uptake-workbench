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
import re

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


# ---------------------------------------------------------------------------
# The two checks above are about code: a module that opens gold, a script that
# reads gold and writes a pipeline input. Both were enough until the failure
# arrived in a shape neither watches - a seed file written by hand, whose rows
# were copied from gold's own columns. No script reads gold, no module names
# it, and the file still carries the answer key's evidence into the thing being
# scored.
#
# What was proposed was a table of investor-relations document URLs, "seeded
# by pattern where the pattern is regular, hand-added for one-offs". Its rows
# would have been gold's `source_url` column. Measuring against gold would then
# have confirmed that a URL copied from gold fetches the document gold cited.
# ---------------------------------------------------------------------------

GOLD_ROWS = SEED / "gold" / "quarterly_revenue.jsonl"
# The columns that are evidence rather than reference data. Product names are
# deliberately excluded: gold is built from product_attributes.csv, so those
# overlap by design and in the permitted direction.
EVIDENCE_FIELDS = ("source_url", "source_quote", "gold_id")
# Short strings collide by accident; a quote or a URL this long does not.
DISTINCTIVE = 24


def _gold_evidence() -> set[str]:
    import json

    if not GOLD_ROWS.exists():
        return set()
    values: set[str] = set()
    for line in GOLD_ROWS.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        for field in EVIDENCE_FIELDS:
            value = str(row.get(field) or "").strip()
            if len(value) >= DISTINCTIVE:
                values.add(value)
    return values


def test_no_pipeline_input_carries_gold_evidence():
    """A file the pipeline reads must not contain gold's URLs or quotes.

    This is the value-level form of the rule the tests above enforce on code.
    Overfitting to an answer key does not usually arrive as an import; it
    arrives as someone reading the key and typing what it says into a file the
    pipeline loads at run time.

    Product names are not checked, and must not be: gold is built from
    seed/product_attributes.csv, so the overlap there is the dependency running
    in the direction that is allowed.
    """
    evidence = _gold_evidence()
    assert evidence, "no gold evidence found to check against; this test would pass vacuously"
    offenders: list[str] = []
    for name in PIPELINE_INPUTS:
        path = SEED / name
        if not path.exists():
            continue
        text = path.read_text(errors="replace")
        for value in evidence:
            if value in text:
                offenders.append(f"{name} contains gold {value[:70]!r}")
                break
    assert not offenders, (
        "pipeline inputs carrying the answer key's own evidence:\n  "
        + "\n  ".join(offenders)
        + "\nBuild pipeline inputs from the documents, not from gold's citations."
    )


def _seed_files_the_app_reads() -> set[str]:
    """Every file under seed/ that application code resolves a path to."""
    found: set[str] = set()
    pattern = re.compile(r'"seed"\s*/\s*"([^"]+)"|seed/([A-Za-z0-9_.-]+\.(?:csv|jsonl|json))')
    for path in APP.rglob("*.py"):
        for match in pattern.finditer(path.read_text()):
            name = match.group(1) or match.group(2)
            if name and "." in name:
                found.add(name)
    return found


def test_a_new_file_the_pipeline_reads_has_to_be_declared():
    """Adding a seed input should be a visible decision, not a side effect.

    `PIPELINE_INPUTS` is what the checks above watch. A file wired into the
    pipeline but missing from that list is unwatched by every one of them, and
    nothing else would say so.

    The question to answer in the commit that adds one is whether it is a cache
    or a mechanism: delete it, and does the pipeline still work on a product it
    has never seen? seed/xbrl_members.csv passes - 76 of its 363 members
    resolve from the string rules alone and the other 287 go to the model that
    decided them in the first place, so removing it costs calls, not
    capability. A table of document URLs fails: remove it and there is no rule
    that produces a URL, because none exists. Cost in time is a cache; cost in
    capability is the answer key wearing a different hat.
    """
    undeclared = sorted(_seed_files_the_app_reads() - set(PIPELINE_INPUTS))
    assert not undeclared, (
        "the pipeline reads these seed files and they are not in PIPELINE_INPUTS:\n  "
        + "\n  ".join(undeclared)
        + "\nAdd them there - and say in the commit whether deleting the file "
          "would cost time or cost capability."
    )


def test_every_declared_input_is_real_and_used():
    """A stale name in the list quietly narrows every check above."""
    missing = [name for name in PIPELINE_INPUTS if not (SEED / name).exists()]
    assert not missing, f"PIPELINE_INPUTS names files that do not exist: {missing}"
