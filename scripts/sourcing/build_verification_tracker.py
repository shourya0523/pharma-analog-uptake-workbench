"""Write exports/gold_verification_tracker.xlsx: the checklist a reviewer ticks.

One row per requested product (docs/sourcing/target_products.csv), joined to
what gold holds for it and to the verification log, plus a sheet of every
quarterly row with a clickable source so a reviewer can spot-check any of
them. The reviewer columns are left blank and carry a Yes/No dropdown.

The verification log (docs/sourcing/verification_log.json) is keyed by
drug name and records, per product, the automated check of each row and the
verifier's own reading:

    {"Calderon": {"status": "verified", "notes": "...",
                  "automated": {"2024Q1": true, ...},
                  "hand_checked": ["2024Q1", ...]}}

    python scripts/sourcing/build_verification_tracker.py
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD = REPO_ROOT / "seed" / "gold"
TARGETS = REPO_ROOT / "docs" / "sourcing" / "target_products.csv"
LOG = REPO_ROOT / "docs" / "sourcing" / "verification_log.json"
OUT = REPO_ROOT / "exports" / "gold_verification_tracker.xlsx"

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
REVIEW_FILL = PatternFill("solid", fgColor="FFF2CC")
STATUS_FILLS = {
    "verified": PatternFill("solid", fgColor="E2EFDA"),
    "verified_with_notes": PatternFill("solid", fgColor="FFF2CC"),
    "excluded_verified": PatternFill("solid", fgColor="DDEBF7"),
    "pending": PatternFill("solid", fgColor="F2F2F2"),
    "failed": PatternFill("solid", fgColor="F8CBAD"),
}


def load_jsonl(name: str) -> list[dict]:
    path = GOLD / name
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def names_for(brand: str) -> list[str]:
    """A brand cell can name a product family ("Calderon / Calderon XR")."""
    return [brand] + [part.strip() for part in brand.split("/") if part.strip() != brand]


def header(sheet, columns: list[str], review: set[str]) -> None:
    sheet.append(columns)
    for index, name in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=index)
        cell.font = Font(bold=True, color="FFFFFF" if name not in review else "000000")
        cell.fill = REVIEW_FILL if name in review else HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "E2"


def main() -> int:
    targets = list(csv.DictReader(TARGETS.open(newline="")))
    log = json.loads(LOG.read_text()) if LOG.is_file() else {}
    quarterly = load_jsonl("quarterly_revenue.jsonl")
    coverage = {row["drug_name"]: row for row in load_jsonl("series_coverage.jsonl")}
    excluded = {row["drug_name"]: row for row in load_jsonl("excluded_products.jsonl")}
    annual = defaultdict(list)
    for row in load_jsonl("annual_revenue.jsonl"):
        annual[row["drug_name"]].append(row)
    by_drug = defaultdict(list)
    for row in quarterly:
        by_drug[row["drug_name"]].append(row)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Drug Checklist"
    review_columns = ["Manually Verified", "Verified By", "Date Verified", "Reviewer Notes"]
    columns = [
        "ID", "Therapeutic Area", "Brand Name", "Generic / INN", "Gold Status",
        "Issuer", "Series (benchmark identity)", "Revenue Scope", "Currency",
        "First Quarter", "Last Quarter", "Quarters", "Distinct Sources",
        "Derived Rows", "Automated Check (rows passing / total)",
        "Claude Verification", "Claude Notes",
    ] + review_columns
    header(sheet, columns, set(review_columns))

    detail_rows = []
    for target in targets:
        drug = next((n for n in names_for(target["brand_name"]) if n in by_drug or n in excluded or n in annual), target["brand_name"])
        rows = sorted(by_drug.get(drug, []), key=lambda r: r["period"])
        entry = log.get(drug, {})
        automated = entry.get("automated", {})
        if rows:
            status = "Quarterly series"
        elif drug in excluded:
            status = f"Excluded ({excluded[drug]['reason_code']})"
        elif drug in annual:
            status = "Annual only"
        else:
            status = "Not yet in gold"
        cov = coverage.get(drug, {})
        sheet.append([
            target["id"], target["therapeutic_area"], target["brand_name"], target["generic_name"],
            status,
            rows[0]["manufacturer"] if rows else "",
            rows[0]["benchmark_identity"] if rows else "",
            rows[0]["revenue_scope"] if rows else "",
            rows[0]["currency"] if rows else "",
            rows[0]["period"] if rows else "",
            rows[-1]["period"] if rows else "",
            len(rows) or "",
            len({r["source_url"] for r in rows}) or "",
            sum(not r["derivation"].startswith("direct") for r in rows) if rows else "",
            f"{sum(1 for v in automated.values() if v)} / {len(automated)}" if automated else "",
            entry.get("status", "pending"),
            "; ".join(filter(None, [entry.get("notes", ""), cov.get("series_start_reason", ""), cov.get("series_end_reason", "")])),
            "", "", "", "",
        ])
        fill = STATUS_FILLS.get(entry.get("status", "pending"))
        if fill:
            sheet.cell(row=sheet.max_row, column=columns.index("Claude Verification") + 1).fill = fill
        for row in rows:
            detail_rows.append((target["id"], row, automated.get(row["period"]), row["period"] in set(entry.get("hand_checked", []))))

    validation = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
    sheet.add_data_validation(validation)
    col = get_column_letter(columns.index("Manually Verified") + 1)
    validation.add(f"{col}2:{col}{sheet.max_row}")
    widths = [6, 22, 18, 26, 26, 18, 34, 16, 9, 11, 11, 9, 9, 9, 14, 18, 60, 12, 14, 13, 40]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.auto_filter.ref = sheet.dimensions

    rows_sheet = workbook.create_sheet("Rows")
    row_columns = [
        "ID", "Drug", "Period", "Value Reported (millions)", "Currency",
        "USD Millions", "Derivation", "Source", "Source Quote",
        "Automated Check", "Hand-Checked by Claude", "Spot-Check (reviewer)",
    ]
    header(rows_sheet, row_columns, {"Spot-Check (reviewer)"})
    for target_id, row, auto, hand in detail_rows:
        rows_sheet.append([
            target_id, row["drug_name"], row["period"], row["value_reported"], row["currency"],
            row["value_normalized_usd_millions"], row["derivation"], row["source_url"],
            row["source_quote"],
            "" if auto is None else ("pass" if auto else "FAIL"),
            "yes" if hand else "",
            "",
        ])
        link = rows_sheet.cell(row=rows_sheet.max_row, column=8)
        link.hyperlink = row["source_url"]
        link.font = Font(color="0563C1", underline="single")
    spot = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
    rows_sheet.add_data_validation(spot)
    spot.add(f"L2:L{max(rows_sheet.max_row, 2)}")
    for index, width in enumerate([6, 18, 9, 12, 8, 12, 24, 50, 80, 10, 10, 12], start=1):
        rows_sheet.column_dimensions[get_column_letter(index)].width = width
    rows_sheet.auto_filter.ref = rows_sheet.dimensions

    readme = workbook.create_sheet("Read Me", 0)
    for line in [
        ["Gold Verification Tracker"],
        [],
        ["Drug Checklist", "One row per requested product. Yellow columns are yours: set Manually Verified to Yes once you have checked the product."],
        ["Rows", "Every quarterly row behind those products, with a clickable source. Spot-Check is optional per row."],
        [],
        ["Automated Check", "Every row's source was fetched and the row label plus every quoted figure was found on one line of that document, in order."],
        ["Claude Verification", "verified = automated check passed and Claude read the hand-checked rows at source; verified_with_notes = passed with a caveat in Claude Notes; excluded_verified = the exclusion evidence was read at source; pending = not yet reviewed."],
        ["Hand-Checked by Claude", "Rows Claude opened and read in the source document: every derived row, every failing row, each series' first, last and peak quarter, and a sample of the rest."],
    ]:
        readme.append(line)
    readme["A1"].font = Font(bold=True, size=14)
    readme.column_dimensions["A"].width = 24
    readme.column_dimensions["B"].width = 120

    OUT.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(OUT)
    print(f"wrote {OUT} ({len(targets)} products, {len(detail_rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
