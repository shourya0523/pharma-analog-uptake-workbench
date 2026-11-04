"""Write the analog profiles workbook in the four-section layout of the rubric.

Sections, their descriptions, criteria and options are read from
docs/analog_profiles.md, so the workbook follows the rubric without a second
list to keep in step. Profile values and their evidence are read from
seed/analog_profiles.csv and seed/analog_profile_evidence.csv.

Sheets:
  Criteria          the four sections, each with its description and its
                    criteria and options, as the rubric states them
  Settings          the inputs the peak and uptake formulas read
  Product Profiles  one row per product; a header row names the section each
                    column belongs to. The Revenue section after the product
                    columns counts the product's carried and withheld rows and
                    refers to its peak on Peak Benchmarks or Annual Peaks
  Profile Evidence  one row per value: the document it was read from and the
                    sentence that decides it
  Quarterly Revenue one row per product-quarter this workbook carries
  Annual Revenue    the peak-benchmark years of products whose benchmark is annual
  Revenue Matrix    products by calendar quarter, USD millions; a withheld
                    quarter reads "withheld"
  Rolling 4Q Matrix rolling four-quarter totals, as formulas over Revenue Matrix
  Peak Benchmarks   per product: the peak, its status and the selected
                    benchmark, as formulas (uptake_workbook.write_peaks)
  Annual Peaks      the annual peak rule, as formulas, for annual benchmarks
  Uptake Curves     per product: the profile criteria, then each quarter from
                    launch as a share of the quarterly benchmark, as formulas

Rows whose source this workbook may not carry are withheld as in the gold
workbook (uptake_workbook.split), and the saved file is recalculated in
headless LibreOffice so every formula's value is stored.

Profiled products gold holds no revenue for keep their row, with the reason in
peak_status, and are printed when the workbook is written.

    python scripts/export_analog_profiles.py [OUT.xlsx] [--profiles P.csv --evidence E.csv]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from export_peak_candidates import GOLD, load
from peak_benchmarks import load_consensus_estimates, select_peak
from uptake_workbook import (
    recalculate, split, write_annual_peaks, write_peaks, write_quarterly_matrix, write_rolling_matrix,
    write_settings, write_uptake,
)

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
REVENUE_FILL = "C55A11"
ID_COLUMNS = ["drug_name", "generic_name", "launch_indication", "in_requested_list"]
TARGETS = REPO / "docs" / "sourcing" / "target_products.csv"
# Fields of a peak_records() row, in the order the Revenue section shows them;
# currencies_reported is added here from the product's gold rows.
REVENUE_COLUMNS = [
    "peak_status", "first_quarter", "last_quarter", "quarters", "rows_withheld",
    "observed_peak_usd_m", "peak_window_or_year", "selected_peak_usd_m", "peak_basis",
    "currencies_reported",
]
QUARTER_COLUMNS = [
    "drug_name", "period", "value_reported", "currency", "source_value_reported", "source_unit",
    "value_normalized_usd_millions",
    "issuer", "manufacturer", "revenue_scope", "geography", "source_url", "source_quote",
]
ANNUAL_COLUMNS = [
    "drug_name", "period", "value_reported", "currency", "source_value_reported", "source_unit",
    "value_normalized_usd_millions", "fx_rate_to_usd", "manufacturer", "revenue_scope",
    "geography", "series_role", "source_url", "source_quote",
]
WIDE = {"drug_name": 18, "source_url": 50, "source_quote": 80, "peak_status": 26,
        "revenue_scope": 22, "geography": 18, "manufacturer": 20, "issuer": 18}


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


def number_format(name: str) -> str | None:
    if name.endswith("_usd_m") or name.endswith("_usd_millions"):
        return "#,##0.0"
    return None


def revenue(names: list[str]) -> dict:
    """Gold revenue for the profiled products, keyed by profile drug_name, less withheld rows.

    A profile name is matched to gold's drug_name exactly, then ignoring case.
    Returns {"quarters": {name: kept rows}, "annual": {name: kept peak-benchmark
    rows}, "series": [one dict per profiled product with a quarterly series],
    "annual_products": [one dict per profiled product whose benchmark is
    annual], "cells", "withheld", "years", "year_cells", "year_withheld",
    "missing": {name: reason}}.
    """
    quarterly_all = load("quarterly_revenue.jsonl")
    annual_all = [row for row in load("annual_revenue.jsonl") if row.get("series_role") == "peak_benchmark"]
    quarterly, quarterly_withheld = split(quarterly_all)
    annual, annual_withheld = split(annual_all)
    excluded = {row["drug_name"]: row for row in load("excluded_products.jsonl")}
    coverage = {row["drug_name"]: row for row in load("series_coverage.jsonl")}
    known = {row["drug_name"] for row in quarterly_all + annual_all} | set(excluded)
    folded = {name.casefold(): name for name in known}
    gold_name = {name: name if name in known else folded.get(name.casefold()) for name in names}

    by_drug = defaultdict(list)
    for row in quarterly_all:
        by_drug[row["drug_name"]].append(row)
    kept = defaultdict(list)
    for row in quarterly:
        kept[row["drug_name"]].append(row)
    annual_kept = defaultdict(list)
    for row in annual:
        annual_kept[row["drug_name"]].append(row)
    annual_names = {row["drug_name"] for row in annual_all}

    out = {"quarters": {}, "annual": {}, "series": [], "annual_products": [], "missing": {},
           "withheld_count": defaultdict(int)}
    for row in quarterly_withheld + annual_withheld:
        out["withheld_count"][row["drug_name"]] += 1
    estimates = load_consensus_estimates()
    for name in names:
        gold = gold_name[name]
        rows = sorted(by_drug.get(gold, []), key=lambda r: r["period"])
        if rows:
            out["quarters"][name] = sorted(kept.get(gold, []), key=lambda r: r["period"])
            cov = coverage.get(gold, {})
            launch = cov.get("launch_quarter")
            geography = " | ".join(sorted({r["geography"] for r in rows}))
            on_file = estimates.get(gold, [])
            choice = select_peak(on_file, geography, {r["period"]: r["value_normalized_usd_millions"]
                                                     for r in kept.get(gold, [])})
            out["series"].append({
                "drug_name": name, "benchmark_identity": rows[0]["benchmark_identity"],
                "revenue_scope": rows[0]["revenue_scope"], "geography": geography,
                "series_end_quarter": cov.get("series_end_quarter") or rows[-1]["period"],
                "launch_anchor_quarter": launch or rows[0]["period"],
                "alignment_basis": ANCHOR_STATED if launch else ANCHOR_ASSUMED,
                "launch_gap_quarters": cov.get("launch_gap_quarters"),
                "gold_launch_review": cov.get("peak_judgement"),
                "consensus_peak_usd_m": choice["consensus"],
                "consensus_estimate_count": len(choice["usable"]),
                "consensus_sources": " | ".join(f"{i['as_stated']}, {i['publisher']} ({i['attribution']}): {i['url']}"
                                                for i in choice["usable"]),
                "estimates_set_aside": " | ".join(f"{i['as_stated']} {i['publisher']} - {choice['reasons'][id(i)]}"
                                                  for i in on_file if choice["reasons"][id(i)] is not None),
                "first_period": rows[0]["period"], "last_period": rows[-1]["period"],
            })
        if gold in annual_names:
            out["annual"][name] = sorted(annual_kept.get(gold, []), key=lambda r: r["period"])
            exemplar = next(row for row in annual_all if row["drug_name"] == gold)
            out["annual_products"].append({"drug_name": name, "gold_name": gold,
                                           "revenue_scope": exemplar["revenue_scope"],
                                           "geography": exemplar["geography"]})
        if not rows and gold not in annual_names:
            out["missing"][name] = (f"excluded from gold: {excluded[gold].get('reason_code')}"
                                    if gold in excluded else "not in gold")
    identities = {s["benchmark_identity"] for s in out["series"]}
    out["periods"] = sorted({r["period"] for r in quarterly_all if r["benchmark_identity"] in identities})
    out["cells"] = {(r["benchmark_identity"], r["period"]): r["value_normalized_usd_millions"]
                    for r in quarterly if r["benchmark_identity"] in identities}
    out["withheld"] = {(r["benchmark_identity"], r["period"]) for r in quarterly_withheld
                       if r["benchmark_identity"] in identities}
    golds = {p["gold_name"]: p["drug_name"] for p in out["annual_products"]}
    out["years"] = sorted({r["period"] for r in annual_all if r["drug_name"] in golds})
    out["year_cells"] = {(golds[r["drug_name"]], r["period"]): r["value_normalized_usd_millions"]
                         for r in annual if r["drug_name"] in golds}
    out["year_withheld"] = {(golds[r["drug_name"]], r["period"]) for r in annual_withheld
                            if r["drug_name"] in golds}
    return out


ANCHOR_STATED = "launch quarter stated in gold"
ANCHOR_ASSUMED = "no launch quarter in gold - anchored on first reported quarter"
MATRIX_LEAD = ["drug_name", "benchmark_identity", "revenue_scope"]


def revenue_cells(name: str, gold: dict, peak_sheet, annual_sheet, series_row: dict, annual_row: dict) -> dict:
    """The Revenue section of a product's profile row: counts as values, the peak as references."""
    rows = gold["quarters"].get(name, [])
    out = {
        "first_quarter": rows[0]["period"] if rows else None,
        "last_quarter": rows[-1]["period"] if rows else None,
        "quarters": len(rows),
        "rows_withheld": gold["withheld_count"].get(name, 0),
        "currencies_reported": ", ".join(sorted({r["currency"] for r in rows + gold["annual"].get(name, [])})) or None,
    }
    if name in annual_row:
        r = annual_row[name]
        out |= {"peak_status": f"={annual_sheet.cell('peak_status', r)}",
                "observed_peak_usd_m": f"={annual_sheet.cell('highest_year_usd_m', r)}",
                "peak_window_or_year": f"={annual_sheet.cell('peak_year', r)}",
                "selected_peak_usd_m": f"={annual_sheet.cell('highest_year_usd_m', r)}",
                "peak_basis": "highest calendar year - annual benchmark"}
    elif name in series_row:
        r = series_row[name]
        out |= {"peak_status": f"={peak_sheet.cell('peak_status', r)}",
                "observed_peak_usd_m": f"={peak_sheet.cell('observed_peak_4q_usd_m', r)}",
                "peak_window_or_year": f"={peak_sheet.cell('peak_window_end', r)}",
                "selected_peak_usd_m": f"={peak_sheet.cell('selected_peak_usd_m', r)}",
                "peak_basis": f"={peak_sheet.cell('peak_basis', r)}"}
    else:
        out["peak_status"] = f"no gold revenue - {gold['missing'].get(name, 'not in gold')}"
    return out


def table(ws, columns, rows, *, top=1):
    """Write a header row at ``top`` and ``rows`` under it; freeze and filter."""
    for col, name in enumerate(columns, start=1):
        cell(ws, top, col, name, bold=True, color="FFFFFF", fill=INK, wrap=True).border = GRID
        ws.column_dimensions[get_column_letter(col)].width = WIDE.get(name, 15)
    for r, record in enumerate(rows, start=top + 1):
        for col, name in enumerate(columns, start=1):
            c = cell(ws, r, col, record.get(name), wrap=name == "source_quote")
            c.border = GRID
            if number_format(name):
                c.number_format = number_format(name)
    ws.freeze_panes = f"C{top + 1}"
    ws.auto_filter.ref = f"A{top}:{get_column_letter(len(columns))}{len(rows) + top}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", nargs="?", type=Path, default=OUT)
    parser.add_argument("--profiles", type=Path, default=PROFILES)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    args = parser.parse_args()
    parts = sections()
    profiles = sorted(read(args.profiles), key=lambda r: r["drug_name"].lower())
    evidence = read(args.evidence)
    gold = revenue([p["drug_name"] for p in profiles])

    wb = Workbook()
    ws = wb.active
    ws.title = "Criteria"
    settings = write_settings(wb, json.loads((GOLD / "manifest.json").read_text())["as_of_quarter"])
    series = gold["series"]
    quarterly = write_quarterly_matrix(
        wb, "Revenue Matrix", series, MATRIX_LEAD, gold["periods"], gold["cells"], gold["withheld"],
        note="Quarterly revenue by product and calendar quarter, USD millions. Blank is a quarter outside the "
             "reported window or unreported; 'withheld' was reported from a source this workbook does not carry.")
    rolling = write_rolling_matrix(
        wb, "Rolling 4Q Matrix", series, MATRIX_LEAD, quarterly,
        note="Each cell is the quarter plus the three before it in Revenue Matrix, as a formula; blank when any "
             "of the four is blank or withheld.")
    peak_sheet = write_peaks(
        wb, "Peak Benchmarks", series, quarterly, rolling, settings,
        note="One row per product with quarterly revenue. The peak is the highest rolling four-quarter total; "
             "its status, the selected benchmark and its basis are formulas, driven by Settings.")
    annual_sheet = write_annual_peaks(
        wb, "Annual Peaks", gold["annual_products"], gold["years"], gold["year_cells"], gold["year_withheld"],
        settings, note="Products whose peak benchmark is annual, USD millions by calendar year, with the annual "
                       "peak rule as formulas.")
    series_row = {item["drug_name"]: 3 + index for index, item in enumerate(series)}
    annual_row = {item["drug_name"]: 3 + index for index, item in enumerate(gold["annual_products"])}
    requested = {row["brand_name"] for row in csv.DictReader(TARGETS.open(newline=""))}
    for profile in profiles:
        profile["in_requested_list"] = "yes" if profile["drug_name"] in requested else "no"
        profile.update(revenue_cells(profile["drug_name"], gold, peak_sheet, annual_sheet, series_row, annual_row))
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
    columns = list(ID_COLUMNS) + list(REVENUE_COLUMNS)
    owners = [None] * len(ID_COLUMNS) + ["revenue"] * len(REVENUE_COLUMNS)
    for index, part in enumerate(parts):
        for name, _ in part["criteria"]:
            columns.append(name)
            owners.append(index)
    for col, name in enumerate(columns, start=1):
        owner = owners[col - 1]
        if owner is None:
            fill, title, heading = INK, "Product", name.replace("_", " ").capitalize()
        elif owner == "revenue":
            fill, title, heading = REVENUE_FILL, "Revenue", name
        else:
            fill, title, heading = SECTION_FILLS[owner % len(SECTION_FILLS)], parts[owner]["title"].title(), name
        cell(ws, 1, col, title, bold=True, color="FFFFFF", fill=fill)
        cell(ws, 2, col, heading, bold=True, color="FFFFFF", fill=fill, wrap=True).border = GRID
        ws.column_dimensions[get_column_letter(col)].width = (
            34 if name == "launch_indication" else 26 if name == "peak_status" else 18)
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
            if number_format(name):
                c.number_format = number_format(name)
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

    quarter_rows = [row for name in sorted(gold["quarters"], key=str.lower) for row in gold["quarters"][name]]
    table(wb.create_sheet("Quarterly Revenue"), QUARTER_COLUMNS, quarter_rows)
    annual_rows = [row for name in sorted(gold["annual"], key=str.lower) for row in gold["annual"][name]]
    table(wb.create_sheet("Annual Revenue"), ANNUAL_COLUMNS, annual_rows)
    criteria = [name for part in parts for name, _ in part["criteria"]]
    by_name = {p["drug_name"]: p for p in profiles}
    curve_rows = [by_name[item["drug_name"]] | item for item in series]
    curve_count = write_uptake(
        wb, "Uptake Curves", curve_rows, ["drug_name"] + criteria + ["launch_anchor_quarter", "alignment_basis"],
        quarterly, peak_sheet, settings,
        note="Each quarter from launch as a share of the quarterly benchmark on Peak Benchmarks (selected peak / 4), "
             "limited to 0-100%, aligned on launch: Year 1 Q1 is launch_anchor_quarter. For a product whose peak is "
             "observed the curve is held at 100% from the last quarter of the peak window on while Settings 'Flat "
             "after peak' is Yes. Blank cells are quarters gold does not report or this workbook withholds.")
    order = ["Criteria", "Settings", "Product Profiles", "Profile Evidence", "Quarterly Revenue", "Annual Revenue",
             "Revenue Matrix", "Rolling 4Q Matrix", "Peak Benchmarks", "Annual Peaks", "Uptake Curves"]
    wb._sheets.sort(key=lambda sheet: order.index(sheet.title))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(args.out)
    recalculate(args.out)
    print(f"wrote {args.out}: {len(profiles)} products, {len(evidence)} evidence rows, {len(parts)} sections, "
          f"{len(quarter_rows)} quarterly and {len(annual_rows)} annual revenue rows, "
          f"{len(series)} peak benchmarks, {len(gold['annual_products'])} annual peaks, {curve_count} uptake curves")
    for name, reason in sorted(gold["missing"].items(), key=lambda kv: kv[0].lower()):
        print(f"  no gold revenue: {name} ({reason})")


if __name__ == "__main__":
    main()
