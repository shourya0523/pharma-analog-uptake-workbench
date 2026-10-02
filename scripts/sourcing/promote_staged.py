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
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = REPO_ROOT / "seed" / "gold" / "source_manifests"
ATTRIBUTES = REPO_ROOT / "seed" / "product_attributes.csv"
EXCLUSIONS = MANIFESTS / "excluded_products.csv"
MANIFEST_COLUMNS = [
    "period", "value_reported", "source_url", "source_quote", "derivation",
    "precision", "context", "source_type", "source_unit",
    "source_value_reported", "currency", "row_label",
]


def route_label(route: str) -> str:
    """The attributes file capitalises routes ("Oral", "Intravenous")."""
    return route.strip().capitalize()


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
            shutil.copy(quarterly, MANIFESTS / quarterly.name)
            shutil.copy(meta_path, MANIFESTS / meta_path.name)
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
