"""Export the products whose peak gold has not observed, with their best rolling year so far.

Gold labels a peak observed only when the highest calendar year is followed by
at least two lower complete years (``observed_peak`` in
build_independent_gold.py). Every other peak-eligible product is listed here,
with the highest sum of four consecutive quarters it has reached, so an
external peak estimate can be set against it.

A rolling window is four consecutive calendar quarters, all present in the
series; a window that spans an unreported quarter is skipped rather than
summed short. Values are ``value_normalized_usd_millions``, so a series that
changes currency at an ownership change stays comparable across the step.

    python scripts/export_peak_candidates.py   # writes exports/peak_candidates.xlsx
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

REPO = Path(__file__).resolve().parents[1]
GOLD = REPO / "seed" / "gold"
OUT = REPO / "exports" / "peak_candidates.xlsx"


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (GOLD / name).read_text().splitlines() if line.strip()]


def quarter_index(period: str) -> int:
    return int(period[:4]) * 4 + int(period[-1]) - 1


def label(index: int) -> str:
    return f"{index // 4}Q{index % 4 + 1}"


def rolling_windows(rows: list[dict]) -> list[tuple[str, str, float]]:
    """Every run of four consecutive quarters: (first, last, USD sum)."""
    by_index = {quarter_index(r["period"]): r["value_normalized_usd_millions"] for r in rows
                if r.get("value_normalized_usd_millions") is not None}
    windows = []
    for end in sorted(by_index):
        span = range(end - 3, end + 1)
        if all(i in by_index for i in span):
            windows.append((label(end - 3), label(end), sum(by_index[i] for i in span)))
    return windows


def main() -> None:
    quarterly = load("quarterly_revenue.jsonl")
    peaks = {row["drug_name"]: row for row in load("peak_sales.jsonl")}
    coverage = {row["drug_name"]: row for row in load("series_coverage.jsonl")}
    profiles = {row["drug_name"]: row for row in load("product_profiles.jsonl")}
    latest_in_gold = max(row["period"] for row in quarterly)

    series: dict[str, list[dict]] = defaultdict(list)
    for row in quarterly:
        series[row["drug_name"]].append(row)

    records = []
    for drug, rows in series.items():
        peak = peaks.get(drug)
        rows.sort(key=lambda r: r["period"])
        windows = rolling_windows(rows)
        best = max(windows, key=lambda w: w[2]) if windows else None
        latest = windows[-1] if windows else None
        first, last = rows[0], rows[-1]
        profile = profiles.get(drug, {})
        if peak is None:
            status = "not judged - series starts after launch"
        elif peak["peak_status"] == "observed":
            status = "peak observed"
        else:
            status = "not yet peaked"
        records.append({
            "drug_name": drug,
            "peak_status": status,
            "manufacturer": first.get("manufacturer"),
            "therapeutic_area": first.get("therapeutic_area"),
            "revenue_scope": first.get("revenue_scope"),
            "geography": first.get("geography"),
            "first_quarter": first["period"],
            "last_quarter": last["period"],
            "series_current": last["period"] == latest_in_gold,
            "quarters": len(rows),
            "best_rolling_4q_usd_m": round(best[2], 3) if best else None,
            "best_window": f"{best[0]}-{best[1]}" if best else None,
            "best_window_is_latest": bool(best and latest and best[1] == latest[1]),
            "latest_rolling_4q_usd_m": round(latest[2], 3) if latest else None,
            "latest_window": f"{latest[0]}-{latest[1]}" if latest else None,
            "latest_vs_best": round(latest[2] / best[2], 3) if best and latest and best[2] else None,
            "best_annual_usd_m": None,
            "highest_calendar_year": peak.get("highest_observed_year") if peak else None,
            "peak_year": peak.get("peak_year") if peak else None,
            "peak_selection": peak.get("selection_method") if peak else None,
            "first_approval_year": profile.get("first_approval_year"),
            "moa_class": profile.get("moa_class"),
            "series_end_basis": coverage.get(drug, {}).get("series_end_basis"),
        })

    # Annual-only products carry a peak label but no quarters to roll, so they
    # are listed with their highest complete year instead.
    annual = defaultdict(list)
    for row in load("annual_revenue.jsonl"):
        if row.get("series_role") == "peak_benchmark":
            annual[row["drug_name"]].append(row)
    for drug, peak in peaks.items():
        if drug in series or drug not in annual:
            continue
        years = sorted(annual[drug], key=lambda r: r["period"])
        top = max(years, key=lambda r: r["value_normalized_usd_millions"] or 0)
        records.append({key: None for key in records[0]} | {
            "drug_name": drug,
            "peak_status": "peak observed" if peak["peak_status"] == "observed" else "not yet peaked",
            "manufacturer": years[0].get("manufacturer"),
            "revenue_scope": years[0].get("revenue_scope"),
            "geography": years[0].get("geography"),
            "first_quarter": f"{years[0]['period']} (annual only)",
            "last_quarter": f"{years[-1]['period']} (annual only)",
            "quarters": 0,
            "best_annual_usd_m": round(top["value_normalized_usd_millions"], 3),
            "highest_calendar_year": peak.get("highest_observed_year"),
            "peak_year": peak.get("peak_year"),
            "peak_selection": peak.get("selection_method"),
            "first_approval_year": profiles.get(drug, {}).get("first_approval_year"),
            "moa_class": profiles.get(drug, {}).get("moa_class"),
        })

    order = {"not yet peaked": 0, "peak observed": 1, "not judged - series starts after launch": 2}
    records.sort(key=lambda r: (order[r["peak_status"]], -(r["best_rolling_4q_usd_m"] or 0)))
    sheets = {
        "Not yet peaked": [r for r in records if r["peak_status"] == "not yet peaked"],
        "Peak observed": [r for r in records if r["peak_status"] == "peak observed"],
        "Not judged": [r for r in records if r["peak_status"].startswith("not judged")],
    }
    notes = {
        "Not yet peaked": "Peak-eligible products whose highest calendar year is not followed by two lower "
                          "complete years. best_rolling_4q_usd_m is the highest sum of four consecutive "
                          "quarters in USD millions; best_window_is_latest marks a product whose best "
                          "window is still its latest, i.e. still growing.",
        "Peak observed": "Products whose highest calendar year is followed by at least two lower years.",
        "Not judged": "Series that start after launch, so their maximum is only the highest value inside "
                      "the window covered; gold does not label a peak for them.",
    }

    wb = Workbook()
    wb.remove(wb.active)
    head = Font(name="Arial", bold=True, color="FFFFFF")
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        ws.cell(1, 1, notes[title]).font = Font(name="Arial", italic=True)
        columns = list(dict.fromkeys(key for record in records for key in record))
        for c, name in enumerate(columns, start=1):
            cell = ws.cell(2, c, name)
            cell.font = head
            cell.fill = PatternFill("solid", fgColor="1F3864")
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            ws.column_dimensions[get_column_letter(c)].width = 22 if name in ("drug_name", "revenue_scope", "geography", "peak_status") else 15
        for r, row in enumerate(rows, start=3):
            for c, name in enumerate(columns, start=1):
                cell = ws.cell(r, c, row.get(name))
                cell.font = Font(name="Arial")
                if name.endswith("_usd_m"):
                    cell.number_format = "#,##0.0"
        ws.freeze_panes = "B3"
        ws.auto_filter.ref = f"A2:{get_column_letter(len(columns))}{len(rows) + 2}"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"wrote {OUT}: " + ", ".join(f"{k} {len(v)}" for k, v in sheets.items()))


if __name__ == "__main__":
    main()
