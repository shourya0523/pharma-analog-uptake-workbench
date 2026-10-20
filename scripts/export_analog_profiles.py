"""Write the analog profiles workbook in the four-section layout of the rubric.

Sections, their descriptions, criteria and options are read from
docs/analog_profiles.md, so the workbook follows the rubric without a second
list to keep in step. Profile values and their evidence are read from
seed/analog_profiles.csv and seed/analog_profile_evidence.csv.

Sheets:
  Criteria          the four sections, each with its description and its
                    criteria and options, as the rubric states them
  Product Profiles  one row per product; a header row names the section each
                    column belongs to
  Profile Evidence  one row per value: the document it was read from and the
                    sentence that decides it

    python scripts/export_analog_profiles.py [OUT.xlsx] [--profiles P.csv --evidence E.csv]
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

REPO = Path(__file__).resolve().parents[1]
RUBRIC = REPO / "docs" / "analog_profiles.md"
PROFILES = REPO / "seed" / "analog_profiles.csv"
EVIDENCE = REPO / "seed" / "analog_profile_evidence.csv"
OUT = REPO / "exports" / "analog_profiles.xlsx"

INK = "1F3864"
SECTION_FILLS = ["2E75B6", "548235", "BF8F00", "7030A0"]
FONT = "Arial"
THIN = Side(style="thin", color="BFBFBF")
GRID = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
ID_COLUMNS = ["drug_name", "generic_name", "launch_indication"]


def sections() -> list[dict]:
    """[{title, description, criteria: [(name, options)]}] in rubric order."""
    text = RUBRIC.read_text()
    out = []
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        title, _, body = block.partition("\n")
        rows = re.findall(r"^\| ([^|]+?) \| ([^|]+?) \|", body, re.M)
        criteria = [(a.strip(), b.strip()) for a, b in rows
                    if a.strip() != "Criterion" and not set(a.strip()) <= {"-"}]
        if not criteria:
            continue
        description = next((p.strip() for p in body.split("\n\n") if p.strip() and not p.strip().startswith("|")), "")
        out.append({"title": title.strip(), "description": description, "criteria": criteria})
    return out


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def cell(ws, row, col, value, *, bold=False, color=None, fill=None, wrap=False, size=10, italic=False):
    c = ws.cell(row, col, value)
    c.font = Font(name=FONT, bold=bold, color=color, size=size, italic=italic)
    if fill:
        c.fill = PatternFill("solid", fgColor=fill)
    c.alignment = Alignment(wrap_text=wrap, vertical="top")
    return c


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", nargs="?", type=Path, default=OUT)
    parser.add_argument("--profiles", type=Path, default=PROFILES)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    args = parser.parse_args()
    parts = sections()
    profiles = sorted(read(args.profiles), key=lambda r: r["drug_name"].lower())
    evidence = read(args.evidence)

    wb = Workbook()
    ws = wb.active
    ws.title = "Criteria"
    row = 1
    cell(ws, row, 1, "Analog selection criteria", bold=True, size=14, color=INK)
    row += 2
    for index, part in enumerate(parts):
        fill = SECTION_FILLS[index % len(SECTION_FILLS)]
        cell(ws, row, 1, part["title"].title(), bold=True, color="FFFFFF", fill=fill, size=12)
        cell(ws, row, 2, None, fill=fill)
        row += 1
        cell(ws, row, 1, part["description"], wrap=True, italic=True)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        ws.row_dimensions[row].height = 48
        row += 1
        cell(ws, row, 1, "Criterion", bold=True, color="FFFFFF", fill=INK).border = GRID
        cell(ws, row, 2, "Variable options", bold=True, color="FFFFFF", fill=INK).border = GRID
        row += 1
        for name, options in part["criteria"]:
            cell(ws, row, 1, name).border = GRID
            cell(ws, row, 2, options, wrap=True).border = GRID
            row += 1
        row += 1
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 110

    ws = wb.create_sheet("Product Profiles")
    columns = list(ID_COLUMNS)
    owners = [None] * len(ID_COLUMNS)
    for index, part in enumerate(parts):
        for name, _ in part["criteria"]:
            columns.append(name)
            owners.append(index)
    for col, name in enumerate(columns, start=1):
        owner = owners[col - 1]
        fill = SECTION_FILLS[owner % len(SECTION_FILLS)] if owner is not None else INK
        label = parts[owner]["title"].title() if owner is not None else "Product"
        cell(ws, 1, col, label, bold=True, color="FFFFFF", fill=fill)
        cell(ws, 2, col, name.replace("_", " ").capitalize() if owner is None else name,
             bold=True, color="FFFFFF", fill=fill, wrap=True).border = GRID
        ws.column_dimensions[get_column_letter(col)].width = 34 if name == "launch_indication" else 18
    start = 1
    for col in range(2, len(columns) + 2):
        if col > len(columns) or owners[col - 1] != owners[start - 1]:
            if col - 1 > start:
                ws.merge_cells(start_row=1, start_column=start, end_row=1, end_column=col - 1)
            start = col
    ws.row_dimensions[2].height = 42
    for r, profile in enumerate(profiles, start=3):
        for col, name in enumerate(columns, start=1):
            value = profile.get(name)
            if name == "Launch Year" and value and value.isdigit():
                value = int(value)
            c = cell(ws, r, col, value, wrap=(name == "launch_indication"))
            c.border = GRID
            if value == "Unknown":
                c.font = Font(name=FONT, size=10, color="808080", italic=True)
    ws.freeze_panes = "B3"
    ws.auto_filter.ref = f"A2:{get_column_letter(len(columns))}{len(profiles) + 2}"

    ws = wb.create_sheet("Profile Evidence")
    ev_cols = ["drug_name", "criterion", "value", "basis", "source_url", "source_quote"]
    widths = {"drug_name": 18, "criterion": 28, "value": 24, "basis": 60, "source_url": 50, "source_quote": 80}
    for col, name in enumerate(ev_cols, start=1):
        cell(ws, 1, col, name, bold=True, color="FFFFFF", fill=INK).border = GRID
        ws.column_dimensions[get_column_letter(col)].width = widths[name]
    order = {name: i for i, name in enumerate(columns)}
    evidence.sort(key=lambda r: (r["drug_name"].lower(), order.get(r["criterion"], 99)))
    for r, record in enumerate(evidence, start=2):
        for col, name in enumerate(ev_cols, start=1):
            cell(ws, r, col, record.get(name), wrap=name in ("basis", "source_quote")).border = GRID
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:F{len(evidence) + 1}"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(args.out)
    print(f"wrote {args.out}: {len(profiles)} products, {len(evidence)} evidence rows, {len(parts)} sections")


if __name__ == "__main__":
    main()
