"""Measure how much of each product's commercial life the pipeline can deliver.

This is a score for the *pipeline*, not a measure of the gold dataset. The
dataset is complete on its own terms and the builder refuses to emit it
otherwise: every included series covers its full commercial span, and
``build_report.json`` records that under ``gold_completeness``. A shortfall
here is therefore a capability the pipeline is missing, not a hole in the
oracle - which is the entire point of scoring against an oracle.

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
    assemble_split_ownership_quarter,
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
# Disagreements that are the issuer's own rounding, not a defect. An issuer
# that rounds each published period independently makes two true statements
# that differ by 1: gold records the figure the issuer's own arithmetic gives,
# and the derivation recomputes it from the quarters, which round differently.
# Listing them by name is what lets a *new* disagreement mean something - an
# unlisted one fails this script rather than scrolling past as familiar noise.
KNOWN_ROUNDING_DISAGREEMENTS = {
    ("Adempas", "2025Q4"): (
        "Merck states nine-month 2025 Adempas sales of 229 while its own "
        "quarters sum to 230, so 312 less the stated nine months gives the 83 "
        "gold records and 312 less the summed quarters gives 82."
    ),
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

    print("pipeline delivery against the gold dataset (the dataset itself is complete)")
    print(
        f"{'product':20} {'in gold':>8} {'read':>6} {'+derived':>9} "
        f"{'total':>6} {'undel':>6} {'delivered':>10}"
    )
    print("-" * 72)

    totals = [0, 0, 0]
    mismatches: list[str] = []
    # Why each undelivered row is undelivered. A row can be present in gold and
    # still unusable as a benchmark row, and the two reasons are different
    # problems: a legend-annotated quote is a human decoding a column layout,
    # which no extractor can reproduce, while a gold-side derivation is a value
    # the pipeline is expected to recompute.
    weak_citation = 0
    gold_derived = 0
    bridged = 0
    delivered_periods: dict[str, set[str]] = {}
    for series in sorted(coverage, key=lambda c: c["drug_name"]):
        drug = series["drug_name"]
        expected = series["expected_quarters"]
        drug_rows = by_drug[drug]

        readable = set()
        for row in drug_rows:
            if row["derivation"] in DERIVED:
                gold_derived += 1
            elif _LEGEND_RE.search(row.get("source_quote", "")):
                weak_citation += 1
            else:
                readable.add(row["period"])

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
        derived_points = complete_quarters_from_totals(
            known + annual_points,
            # The series knows when the product started selling, so a
            # launch-year total is not treated as covering quarters that
            # predate the launch.
            commercial_start=series.get("commercial_start_quarter"),
        )
        derived = {point.period for point in derived_points}
        # A derived quarter counts as delivered only by period, so a derivation
        # that lands on the wrong number would still read as coverage. Compare
        # it against the gold value it is meant to reproduce and report any
        # disagreement, since a confidently wrong figure is worse than a gap.
        gold_values = {row["period"]: row["value_reported"] for row in drug_rows}
        for point in derived_points:
            expected_value = gold_values.get(point.period)
            if expected_value is None:
                continue
            actual = point.value_normalized_usd_millions
            if actual is None or abs(actual - expected_value) > 0.05:
                mismatches.append((
                    (drug, point.period),
                    f"{drug} {point.period}: derived {actual:g} vs gold {expected_value:g}",
                ))

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

        # A quarter split by an ownership change is assembled from the two
        # issuers' dated partial figures rather than read or back-solved. It
        # counts as delivered only if those parts actually tile the quarter and
        # sum to what gold records - the check is in the pipeline, not here.
        for row in drug_rows:
            components = row.get("bridge_components")
            if not components:
                continue
            assembled = assemble_split_ownership_quarter(row["period"], components)
            if assembled is None:
                continue
            if abs(assembled - float(row["value_reported"])) < 0.05:
                derived.add(row["period"])
                bridged += 1
            else:
                mismatches.append((
                    (drug, row["period"]),
                    f"{drug} {row['period']}: bridge assembles {assembled:g} "
                    f"vs gold {float(row['value_reported']):g}",
                ))

        derived -= readable
        delivered_periods[drug] = readable | derived
        delivered = len(readable) + len(derived)
        gap = expected - delivered
        totals[0] += expected
        totals[1] += len(readable)
        totals[2] += len(derived)
        flag = "" if gap == 0 else "  <--"
        print(
            f"{drug:20} {expected:>8} {len(readable):>6} {len(derived):>9} "
            f"{delivered:>6} {gap:>6} {100 * delivered / expected:>9.1f}%{flag}"
        )

    expected, read, derived_count = totals
    delivered = read + derived_count
    print("-" * 72)
    print(
        f"{'ALL QUARTERLY':20} {expected:>8} {read:>6} {derived_count:>9} "
        f"{delivered:>6} {expected - delivered:>6} "
        f"{100 * delivered / expected:>9.1f}%"
    )
    print(
        f"\nread-only completeness was {100 * read / expected:.1f}%; "
        f"derivation adds {derived_count} quarters"
    )
    print(
        f"\nof {read + weak_citation + gold_derived} gold rows: {read} carry a citation "
        f"an extractor can read, {weak_citation} cite a schedule that needs a "
        f"hand-written legend, {gold_derived} are gold-side derivations "
        f"(of which {bridged} are quarters assembled across an ownership change)"
    )
    # Delivery by issuer, because the overall number is dominated by whoever
    # publishes the most quarters. United Therapeutics is three quarters of this
    # dataset: a pipeline that read UTHR perfectly and every other issuer at
    # half would still score above 85% overall. The share column is there to be
    # uncomfortable - it is a property of the catalog, not of the pipeline.
    by_issuer: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        stat = by_issuer[row.get("manufacturer", "unknown")]
        stat[0] += 1
        if row["period"] in delivered_periods.get(row["drug_name"], set()):
            stat[1] += 1
    print(f"\n{'issuer':24} {'in gold':>8} {'share':>7} {'delivered':>10}")
    print("-" * 52)
    for issuer, (count, ok) in sorted(
        by_issuer.items(), key=lambda kv: -kv[1][0]
    ):
        share = 100 * count / len(rows)
        flag = "" if ok == count else "  <--"
        print(f"{issuer:24} {count:>8} {share:>6.1f}% {100 * ok / count:>9.1f}%{flag}")

    # Concentration, printed next to delivery because the two are read
    # together: a delivery rate is only as meaningful as the spread of the rows
    # it averages over. The numbers come from the build report so that this
    # script and the dataset cannot drift apart on what "balanced" means.
    report = json.loads((GOLD / "build_report.json").read_text())
    balance = report.get("concentration")
    if balance:
        print(f"\n{'concentration':26}{'':20}{'share':>7} {'target':>8}")
        print("-" * 62)
        for key, target in (
            ("largest issuer", "< 40%"),
            ("largest product", "< 10%"),
            ("largest therapeutic area", "< 60%"),
        ):
            field = key.replace(" ", "_")
            share = balance[f"{field}_share"]
            flag = "" if balance[f"{field}_within_target"] else "  <--"
            print(f"{key:26}{balance[field]:20.20}{share:>6.1f}% {target:>8}{flag}")
        areas_flag = "" if balance["therapeutic_areas_within_target"] else "  <--"
        print(
            f"{'therapeutic areas':26}{'':20}"
            f"{balance['therapeutic_area_count']:>7} {'>= 6':>8}{areas_flag}"
        )
        print(
            "\nbalanced" if balance["balanced"]
            else "\nnot yet balanced: the marked rows are above target"
        )

    known = [m for m in mismatches if m[0] in KNOWN_ROUNDING_DISAGREEMENTS]
    unknown = [m for m in mismatches if m[0] not in KNOWN_ROUNDING_DISAGREEMENTS]
    if known:
        print(
            f"\n{len(known)} derived quarter(s) differ by the issuer's own "
            "rounding, which is expected and recorded:"
        )
        for key, line in known:
            print(f"  {line}")
            print(f"      {KNOWN_ROUNDING_DISAGREEMENTS[key]}")
    if unknown:
        print(
            f"\n{len(unknown)} derived quarter(s) disagree with the gold value "
            "they reproduce, and are not a recorded rounding difference:"
        )
        for _, line in unknown:
            print(f"  {line}")
    return 1 if unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())
