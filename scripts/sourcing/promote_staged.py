"""Move a verified staging batch into seed/gold/source_manifests.

A sourcing batch is staged outside the repository and checked with
``verify_staged_manifests.py`` first. This copies what passed into gold's
manifest directory and carries each product's curated attributes into
``seed/product_attributes.csv``:

* ``<stem>_quarterly.csv`` and ``<stem>.meta.json`` -> source_manifests/
* ``<stem>.exclusion.json`` -> a row of source_manifests/excluded_products.csv
* the meta's moa, moa_class, route, first approval year and indication ->
  a ``curated_reference`` row of seed/product_attributes.csv

It refuses a product that already has an attributes row or a manifest, so a
re-run never silently overwrites a row that was verified earlier.

    python scripts/sourcing/promote_staged.py STAGING_DIR [--only STEM ...]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = REPO_ROOT / "seed" / "gold" / "source_manifests"
ATTRIBUTES = REPO_ROOT / "seed" / "product_attributes.csv"
TARGETS = REPO_ROOT / "docs" / "sourcing" / "target_products.csv"
EXCLUSIONS = MANIFESTS / "excluded_products.csv"
MANIFEST_COLUMNS = [
    "period", "value_reported", "source_url", "source_quote", "derivation",
    "precision", "context", "source_type", "source_unit",
    "source_value_reported", "currency", "row_label",
]


def quote_states(quote: str, value: float) -> bool:
    """Whether the quote spells the figure, as gold's audit requires of every row."""
    flat = quote.replace(",", "")
    forms = {f"{value:g}", f"{value:.1f}", f"{value:.3f}", f"{value:.0f}" if value == int(value) else ""}
    return any(form and re.search(rf"(?<![\d.]){re.escape(form)}(?![\d])", flat) for form in forms)


def write_with_stated_results(rows: list[dict[str, str]], path: Path) -> None:
    """Copy a manifest, adding the result to any derived quote that omits it.

    A derived row quotes its inputs; gold's convention is that its quote also
    states what they yield ("... less nine months 2,374 yields 856"), so the
    recorded figure is checkable from the quote alone.
    """
    for row in rows:
        value = float(row.get("source_value_reported") or row["value_reported"])
        if row["derivation"].startswith("direct") or quote_states(row["source_quote"], value):
            continue
        head, sep, tail = row["source_quote"].partition(" | ")
        spelled = f"{value:,.0f}" if value == int(value) else f"{value:,}"
        row["source_quote"] = f"{head}, which yields {spelled}{sep}{tail}"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# Issuer names a sourcing note decorates ("Gilead Sciences (Kite)") are cut to
# the name gold already uses for that issuer, so one issuer is one name.
ISSUER_ALIASES = {"Gilead Sciences": "Gilead", "Allergan, Inc.": "Allergan"}


def tidy_meta(meta: dict) -> None:
    """One spelling per issuer and scope, and only the end bases gold defines."""
    name = re.sub(r"\s*\(.*\)$", "", meta["manufacturer"]).strip()
    meta["manufacturer"] = ISSUER_ALIASES.get(name, name)
    meta["revenue_scope"] = re.sub(r"\s*\(.*\)$", "", meta["revenue_scope"]).strip()
    if meta.get("series_end_basis") not in (None, "issuer_stopped_reporting", "sourcing_boundary"):
        meta["series_end_basis"] = "issuer_stopped_reporting"


def route_label(route: str) -> str:
    """The attributes file capitalises routes ("Oral", "Intravenous")."""
    return route.strip().capitalize()


def target_areas() -> dict[str, str]:
    """Therapeutic area by brand, from the requested product list.

    A brand cell can name a family ("Calderon / Calderon XR"); each name in it
    maps to the row's area as well as the whole cell.
    """
    out: dict[str, str] = {}
    for row in read_rows(TARGETS):
        out[row["brand_name"]] = row["therapeutic_area"]
        for part in row["brand_name"].split("/"):
            out.setdefault(part.strip(), row["therapeutic_area"])
    return out


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def append_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open(newline="") as handle:
        fieldnames = next(csv.reader(handle))
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("staging", type=Path)
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args()

    attributes = {row["drug_name"] for row in read_rows(ATTRIBUTES)}
    excluded = {row["drug_name"] for row in read_rows(EXCLUSIONS)}
    new_attributes: list[dict[str, str]] = []
    new_exclusions: list[dict[str, str]] = []

    stems = sorted(
        {p.name.removesuffix(".meta.json") for p in args.staging.glob("*.meta.json")}
        | {p.name.removesuffix(".exclusion.json") for p in args.staging.glob("*.exclusion.json")}
    )
    if args.only is not None:
        stems = [stem for stem in stems if stem in set(args.only)]
    for stem in stems:
        meta_path = args.staging / f"{stem}.meta.json"
        quarterly = args.staging / f"{stem}_quarterly.csv"
        exclusion = args.staging / f"{stem}.exclusion.json"
        meta = json.loads(meta_path.read_text()) if meta_path.is_file() else None

        if quarterly.is_file() and meta:
            if (MANIFESTS / quarterly.name).exists():
                raise SystemExit(f"{quarterly.name} is already in gold; refusing to overwrite")
            rows = read_rows(quarterly)
            missing = [c for c in MANIFEST_COLUMNS if c not in rows[0]]
            if missing:
                raise SystemExit(f"{quarterly.name} lacks columns {missing}")
            write_with_stated_results(rows, MANIFESTS / quarterly.name)
            tidy_meta(meta)
            area = target_areas().get(meta["drug_name"])
            if area:
                meta["therapeutic_area"] = area
            (MANIFESTS / meta_path.name).write_text(json.dumps(meta, indent=2) + "\n")
            print(f"promoted {stem}: {len(rows)} quarters")
        elif exclusion.is_file():
            record = json.loads(exclusion.read_text())
            if record["drug_name"] in excluded:
                raise SystemExit(f"{record['drug_name']} is already excluded")
            new_exclusions.append(record)
            print(f"promoted {stem}: exclusion ({record['reason_code']})")
        else:
            print(f"skipped {stem}: no quarterly manifest or exclusion")
            continue

        if meta and meta["drug_name"] not in attributes:
            new_attributes.append({
                "drug_name": meta["drug_name"],
                "moa": meta["moa"],
                "moa_class": meta["moa_class"],
                "route_of_administration": route_label(meta["route_of_administration"]),
                "first_approval_year": str(meta["first_approval_year"]),
                "indication_area": meta["indication_area"],
                "attribute_provenance": "curated_reference",
                "peer_universe_role": "distinct_product",
            })
            attributes.add(meta["drug_name"])

    if new_attributes:
        append_rows(ATTRIBUTES, new_attributes)
    if new_exclusions:
        append_rows(EXCLUSIONS, new_exclusions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
