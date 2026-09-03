"""Replay the edge-case fixtures against the adjudicator.

Two things are being measured, and only one of them is the obvious one.

The obvious one: does the pipeline reach the right verdict on inputs that have
no single right answer - does it say "impossible" where no value is correct,
and "needs review" where more than one is.

The one that matters more: does it stay quiet everywhere else. A pipeline that
flags a quarter for review whenever it is unsure is not careful, it is useless -
the flags stop being read. So this script reports the fixtures alongside the
count of real gold rows that trip any verdict, and that second number must be
zero. ``test_no_real_series_trips_the_adjudicator`` enforces it in CI; the
number is printed here so it is visible while working.

Seven of the twelve fixtures are marked ``observed``: they are situations that
actually occurred in the documents this dataset is built from, several of them
found by this repo's own evals. Five are ``constructed`` - real figures mutated
to reach a branch that healthy data never reaches. The distinction is in the
fixture file so nobody mistakes a synthetic case for evidence that issuers
routinely publish contradictions. They do not.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.extraction.adjudicate import (  # noqa: E402
    Candidate,
    adjudicate_positional_solutions,
    adjudicate_reported_value,
    adjudicate_split_ownership_quarter,
    adjudicate_total_against_parts,
)

GOLD = REPO_ROOT / "seed" / "gold"


def run_case(case: dict) -> tuple[str, str]:
    kind, inputs = case["kind"], case["inputs"]
    if kind == "reported_value":
        if "solutions" in inputs:
            verdict = adjudicate_positional_solutions(
                inputs["requested_scope"],
                [tuple(solution) for solution in inputs["solutions"]],
            )
        else:
            verdict = adjudicate_reported_value(
                inputs["requested_scope"],
                [Candidate(**candidate) for candidate in inputs["candidates"]],
            )
    elif kind == "total_against_parts":
        verdict = adjudicate_total_against_parts(
            inputs["total"], inputs["parts"], expected_parts=inputs["expected_parts"]
        )
    elif kind == "split_ownership":
        verdict = adjudicate_split_ownership_quarter(
            inputs["period"], inputs["components"]
        )
    else:
        raise ValueError(f"unknown fixture kind: {kind}")
    return verdict.status, verdict.code


def real_rows_that_trip() -> list[str]:
    """Every complete year in gold, put through the same checks.

    This is the false-positive guard. Each series is grouped into calendar
    years, and any year with all four quarters is checked against the total
    those quarters imply - the same call the pipeline makes when deriving. None
    of them may come back as anything other than resolved.
    """
    quarterly = [
        json.loads(line)
        for line in (GOLD / "quarterly_revenue.jsonl").read_text().splitlines()
        if line.strip()
    ]
    annual = [
        json.loads(line)
        for line in (GOLD / "annual_revenue.jsonl").read_text().splitlines()
        if line.strip()
    ]
    # Normalised USD on both sides, never as-reported. Tracleer's annual series
    # is Actelion's CHF and its quarterly series is J&J's own dollar conversion
    # of the same history: comparing 1,020 francs against 1,035 dollars reports
    # a contradiction that is only a currency. This is the category error the
    # adjudicator is meant to catch, and it caught it here first.
    totals = {
        (row["drug_name"], str(row["period"])): row["value_normalized_usd_millions"]
        for row in annual
        if row.get("value_normalized_usd_millions") is not None
    }

    by_year: dict[tuple[str, int], dict[str, float]] = {}
    for row in quarterly:
        key = (row["drug_name"], row["calendar_year"])
        usd = row.get("value_normalized_usd_millions")
        if usd is None:
            continue
        by_year.setdefault(key, {})[row["period"]] = usd

    tripped = []
    for (drug, year), quarters in sorted(by_year.items()):
        stated = totals.get((drug, str(year)))
        if stated is None:
            # No published year to check against; the quarters stand on their
            # own citations and there is nothing here to adjudicate.
            continue
        verdict = adjudicate_total_against_parts(
            stated, quarters, expected_parts=len(quarters)
        )
        if not verdict.resolved:
            tripped.append(f"{drug} {year}: {verdict.code} - {verdict.detail}")

    for row in quarterly:
        components = row.get("bridge_components")
        if components:
            verdict = adjudicate_split_ownership_quarter(row["period"], components)
            if not verdict.resolved:
                tripped.append(f"{row['drug_name']} {row['period']}: {verdict.code}")
    return tripped


def main() -> int:
    cases = [
        json.loads(line)
        for line in (GOLD / "adjudication_cases.jsonl").read_text().splitlines()
        if line.strip()
    ]
    print(f"edge cases: {len(cases)}")
    counts = Counter(case["provenance"] for case in cases)
    print(f"  observed in real documents: {counts['observed']}")
    print(f"  constructed to reach a branch: {counts['constructed']}\n")

    print(f"{'case':38} {'provenance':12} {'expected':28} {'result'}")
    print("-" * 96)
    failures = 0
    for case in cases:
        status, code = run_case(case)
        want = case["expect"]
        ok = status == want["status"] and code == want["code"]
        failures += not ok
        got = f"{status}/{code}" if not ok else "ok"
        print(
            f"{case['case_id']:38} {case['provenance']:12} "
            f"{want['status'] + '/' + want['code']:28} {got}"
        )

    tripped = real_rows_that_trip()
    print(f"\nreal gold rows tripping any verdict: {len(tripped)}")
    for line in tripped:
        print(f"  {line}")
    if not tripped:
        print(
            "  (none - every complete year and every bridged quarter in the "
            "catalog resolves cleanly)"
        )

    if failures:
        print(f"\n{failures} fixture(s) did not reach the expected verdict")
    return 1 if failures or tripped else 0


if __name__ == "__main__":
    raise SystemExit(main())
