"""Measure how much of each product's commercial life the pipeline can deliver.

Accuracy answers "when the pipeline produces a number, is it right". That is
not the question a user of this data asks. They ask whether the series is
whole - whether every quarter from a product's launch to today is there - and a
pipeline can be perfectly accurate on the quarters it happens to read while
leaving a third of a product's history missing.

So the denominator here is every quarter of a product's commercial span, taken
from ``series_coverage.jsonl``, not the rows that happen to be readable. A row
counts as delivered only if the pipeline can produce it: read from source text,
or derived by arithmetic the issuer's own figures determine.

Usage:
    cd backend && uv run python ../scripts/eval_completeness.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.extraction.derive import (  # noqa: E402
    complete_quarters_from_totals,
    propagate_sole_formulation,
)
from app.extraction.process import Datapoint  # noqa: E402
from app.extraction.prose import read_prose  # noqa: E402

GOLD = REPO_ROOT / "seed" / "gold"

DERIVED = {
    "annual_less_reported_first_nine_months",
    "acquisition_bridge_sum",
    "full_year_less_other_reported_quarters",
    "identity_normalization_pre_dpi",
}
_LEGEND_RE = re.compile(
    r"\([^)]*(?:Q[1-4]\s*\d{4}|\d{4}\s*Q[1-4]|H[12]\s*\d{4}|Q[1-4]-Q[1-4])[^)]*\)"
)


def load(name: str) -> list[dict[str, Any]]:
    path = GOLD / name
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _point(row: dict[str, Any]) -> Datapoint:
    return Datapoint(
        product_label=row["drug_name"],
        period=row["period"],
        period_type=row.get("period_type", "quarterly"),
        value_normalized_usd_millions=row["value_reported"],
        value_as_reported=row["value_reported"],
        source_unit=row.get("unit", "millions"),
        source_currency=row.get("currency", "USD"),
        fx_rate_to_usd=None,
        source_quote=row.get("source_quote", ""),
        fingerprint_signature="gold",
        normalization_status="ok",
    )


def main() -> int:
    coverage = load("series_coverage.jsonl")
    rows = load("quarterly_revenue.jsonl")
    annual = load("annual_revenue.jsonl")

    by_drug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_drug[row["drug_name"]].append(row)

    print(
        f"{'product':20} {'expected':>8} {'read':>6} {'+derived':>9} "
        f"{'total':>6} {'gap':>5} {'complete':>9}"
    )
    print("-" * 72)

    totals = [0, 0, 0]
    for series in sorted(coverage, key=lambda c: c["drug_name"]):
        drug = series["drug_name"]
        expected = series["expected_quarters"]
        drug_rows = by_drug[drug]

        readable = {
            row["period"]
            for row in drug_rows
            if row["derivation"] not in DERIVED
            and not _LEGEND_RE.search(row.get("source_quote", ""))
        }

        # What the derivation stage can add, given only the readable quarters
        # plus the annual totals the issuer published.
        known = [_point(row) for row in drug_rows if row["period"] in readable]
        annual_points = [
            _point({**row, "period_type": "annual"})
            for row in annual
            if row["drug_name"] == drug
        ]
        # Annual totals are often stated only in prose ("Full-year 2002
        # Remodulin revenue was $21.174 million"), and that total is what makes
        # an unstated fourth quarter derivable. Only annual figures are taken:
        # reading a quarter out of the same sentence would be reading the
        # answer rather than deriving it.
        seen_annual = {point.period for point in annual_points}
        for row in drug_rows:
            for value in read_prose(row.get("source_quote", ""), product=drug):
                if value.period_type != "annual" or value.period in seen_annual:
                    continue
                seen_annual.add(value.period)
                annual_points.append(
                    _point(
                        {
                            "drug_name": drug,
                            "period": value.period,
                            "period_type": "annual",
                            "value_reported": value.value_as_reported,
                            "source_quote": value.source_quote,
                            "derivation": "direct_reported",
                        }
                    )
                )
        derived = {
            point.period
            for point in complete_quarters_from_totals(known + annual_points)
        }

        # A family line before its formulation split resolves the formulation's
        # own series; Tyvaso's family total covers Nebulized Tyvaso pre-DPI.
        if drug == "Nebulized Tyvaso":
            dpi = {
                row["period"]
                for row in by_drug.get("Tyvaso DPI", [])
            }
            family = [
                _point(row)
                for row in by_drug.get("Tyvaso", [])
                if row["derivation"] not in DERIVED
            ]
            derived |= {
                point.period
                for point in propagate_sole_formulation(
                    family, formulation_periods=dpi, formulation_label=drug
                )
            }

        derived -= readable
        delivered = len(readable) + len(derived)
        gap = expected - delivered
        totals[0] += expected
        totals[1] += len(readable)
        totals[2] += len(derived)
        flag = "" if gap == 0 else "  <--"
        print(
            f"{drug:20} {expected:>8} {len(readable):>6} {len(derived):>9} "
            f"{delivered:>6} {gap:>5} {100 * delivered / expected:>8.1f}%{flag}"
        )

    expected, read, derived_count = totals
    delivered = read + derived_count
    print("-" * 72)
    print(
        f"{'ALL QUARTERLY':20} {expected:>8} {read:>6} {derived_count:>9} "
        f"{delivered:>6} {expected - delivered:>5} "
        f"{100 * delivered / expected:>8.1f}%"
    )
    print(
        f"\nread-only completeness was {100 * read / expected:.1f}%; "
        f"derivation adds {derived_count} quarters"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
