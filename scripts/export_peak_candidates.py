"""Export the products whose peak gold has not observed, with their best rolling year so far.

Gold's peak is a product's highest rolling four-quarter total, and it is
labelled observed once two later totals follow it, none higher
(``quarterly_peak`` in build_independent_gold.py). A series that ends before
then is labelled line ended at high. Every other peak-eligible product is listed here,
with the highest sum of four consecutive quarters it has reached, so an
external peak estimate can be set against it.

A rolling window is four consecutive calendar quarters, all present in the
series; a window that spans an unreported quarter is skipped rather than
summed short. Values are ``value_normalized_usd_millions``, so a series that
changes currency at an ownership change stays comparable across the step.

Rows whose source is a company website are withheld here as in the
workbooks (``uptake_workbook.split``): nothing is computed from them, a
product shows how many it has, and a product whose highest window has a
withheld quarter at or before it within a year, or after it, reads
``peak unknown - restricted data`` - the rule Peak Benchmarks applies.

    python scripts/export_peak_candidates.py   # writes exports/peak_candidates.xlsx
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from uptake_workbook import LAUNCH_HISTORY_MISSING, RESTRICTED, split  # noqa: E402
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


def best_reported(rows: list[dict], best: tuple[str, str, float]) -> tuple[float, str] | None:
    """The best window summed in the currency the issuer reported, or None if it spans two."""
    span = {label(i) for i in range(quarter_index(best[0]), quarter_index(best[1]) + 1)}
    window = [r for r in rows if r["period"] in span]
    currencies = {r["currency"] for r in window}
    if len(currencies) != 1:
        return None
    return round(sum(r["value_reported"] for r in window), 3), currencies.pop()


def peak_records() -> list[dict]:
    """One record per product gold has revenue for, in the order the export lists them.

    Quarterly products carry their best and latest rolling windows; annual-only
    peak-benchmark products carry their highest complete year in
    ``best_annual_usd_m`` and ``quarters`` of 0. Sorted not-yet-peaked first,
    then peak observed, then not judged, each by best rolling year descending.
    """
    quarterly, withheld = split(load("quarterly_revenue.jsonl"))
    held: dict[str, set[int]] = defaultdict(set)
    for row in withheld:
        held[row["drug_name"]].add(quarter_index(row["period"]))
    peaks = {row["drug_name"]: row for row in load("peak_sales.jsonl")}
    coverage = {row["drug_name"]: row for row in load("series_coverage.jsonl")}
    profiles = {row["drug_name"]: row for row in load("product_profiles.jsonl")}
    latest_in_gold = max(row["period"] for row in quarterly)

    series: dict[str, list[dict]] = defaultdict(list)
    for row in quarterly:
        series[row["drug_name"]].append(row)
    for drug in held:
        series.setdefault(drug, [])

    records = []
    for drug, rows in series.items():
        peak = peaks.get(drug)
        rows.sort(key=lambda r: r["period"])
        windows = rolling_windows(rows)
        best = max(windows, key=lambda w: w[2]) if windows else None
        latest = windows[-1] if windows else None
        first, last = (rows[0], rows[-1]) if rows else ({}, {})
        profile = profiles.get(drug, {})
        best_end = quarter_index(best[1]) if best else None
        # The order of Peak Benchmarks' peak_status formula.
        if coverage.get(drug, {}).get("peak_judgement") == LAUNCH_HISTORY_MISSING:
            status = "not judged - launch history too short or missing"
        elif held[drug] and (best is None or any(k >= best_end - 4 for k in held[drug])):
            status = RESTRICTED
        elif best is None:
            status = "not yet peaked"
        elif peak is None:
            status = "not judged - launch history too short or missing"
        elif peak["peak_status"] == "observed":
            status = "peak observed"
        elif peak["peak_status"] == "line_ended_at_high":
            status = "line ended at high"
        else:
            status = "not yet peaked"
        currencies = sorted({r["currency"] for r in rows if r.get("currency")})
        reported = best_reported(rows, best) if best else None
        records.append({
            "drug_name": drug,
            "peak_status": status,
            "manufacturer": first.get("manufacturer"),
            "therapeutic_area": first.get("therapeutic_area"),
            "revenue_scope": first.get("revenue_scope"),
            "geography": first.get("geography"),
            "first_quarter": first.get("period"),
            "last_quarter": last.get("period"),
            "series_current": last.get("period") == latest_in_gold,
            "quarters": len(rows),
            "quarters_withheld": len(held[drug]),
            "best_rolling_4q_usd_m": round(best[2], 3) if best else None,
            "best_window": f"{best[0]}-{best[1]}" if best else None,
            "best_window_is_latest": bool(best and latest and best[1] == latest[1]),
            "currencies_reported": ", ".join(currencies) or None,
            "best_rolling_4q_reported": reported[0] if reported else None,
            "best_window_currency": reported[1] if reported else None,
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
    annual_held: dict[str, set[int]] = defaultdict(set)
    kept, withheld_years = split([row for row in load("annual_revenue.jsonl") if row.get("series_role") == "peak_benchmark"])
    for row in kept:
        annual[row["drug_name"]].append(row)
    for row in withheld_years:
        annual_held[row["drug_name"]].add(int(row["period"]))
    for drug, peak in peaks.items():
        if drug in series or drug not in annual:
            continue
        years = sorted(annual[drug], key=lambda r: r["period"])
        top = max(years, key=lambda r: r["value_normalized_usd_millions"] or 0)
        # Annual Peaks' rule: a withheld year from the one before the highest onward hides the peak.
        restricted = any(y >= int(top["period"]) - 1 for y in annual_held[drug])
        records.append({key: None for key in records[0]} | {
            "drug_name": drug,
            "peak_status": RESTRICTED if restricted else
                           "peak observed" if peak["peak_status"] == "observed" else "not yet peaked",
            "manufacturer": years[0].get("manufacturer"),
            "revenue_scope": years[0].get("revenue_scope"),
            "geography": years[0].get("geography"),
            "first_quarter": f"{years[0]['period']} (annual only)",
            "last_quarter": f"{years[-1]['period']} (annual only)",
            "quarters": 0,
            "quarters_withheld": 0,
            "best_annual_usd_m": round(top["value_normalized_usd_millions"], 3),
            "highest_calendar_year": peak.get("highest_observed_year"),
            "peak_year": peak.get("peak_year"),
            "peak_selection": peak.get("selection_method"),
            "first_approval_year": profiles.get(drug, {}).get("first_approval_year"),
            "moa_class": profiles.get(drug, {}).get("moa_class"),
        })

    order = {"not yet peaked": 0, "line ended at high": 1, "peak observed": 2,
             "not judged - launch history too short or missing": 3, RESTRICTED: 4}
    records.sort(key=lambda r: (order[r["peak_status"]], -(r["best_rolling_4q_usd_m"] or 0)))
    return records


OUTCOMES = {
    "peak_observed": "peak identifiable from the reported quarters",
    "not_yet_observed": "not yet peaked - needs an external estimate",
    "line_ended_at_high": "series ends at its highest window - a benchmark, not a confirmed peak",
    "highest_window_is_first_window": "highest window is the first window - the peak may predate the series",
    "no_complete_four_quarter_window": "no complete four-quarter window reported",
    "launch_history_missing": "substantial launch history missing - flag for exclusion",
}


def launch_gap_review(records: list[dict]) -> list[dict]:
    """Gold's launch-gap review of every series that starts after launch, beside its rolling figures.

    The review itself - the gap, its band and the peak judgement - is made by
    ``launch_gap_review`` in build_independent_gold.py and stored on the
    series' coverage row; this only lays it out.
    """
    from build_independent_gold import full_annual_totals

    quarterly, _ = split(load("quarterly_revenue.jsonl"))
    by_name = {record["drug_name"]: record for record in records}
    out = []
    for cov in load("series_coverage.jsonl"):
        if not cov.get("series_starts_after_launch") or cov.get("peak_judgement") not in OUTCOMES:
            continue
        drug = cov["drug_name"]
        record = by_name[drug]
        totals = full_annual_totals(quarterly, drug)
        top = max(totals, key=lambda r: r["value_reported"]) if totals else None
        out.append({
            "drug_name": drug,
            "outcome": OUTCOMES[cov["peak_judgement"]],
            "launch_reference": cov["launch_reference_quarter"],
            "launch_reference_basis": cov["launch_reference_basis"],
            "first_quarter": record["first_quarter"],
            "launch_gap_quarters": cov["launch_gap_quarters"],
            "gap_band": cov["launch_gap_band"],
            "unreported_quarters_inside": len(cov.get("unreported_quarters") or []) + len(cov.get("missing_quarters") or []),
            "quarters": record["quarters"],
            "complete_years": len(totals),
            "highest_calendar_year": int(top["period"]) if top else None,
            "highest_calendar_year_usd_m": round(top["value_reported"], 3) if top else None,
            "lower_years_after_highest": sum(1 for r in totals if top and r["period"] > top["period"]
                                             and r["value_reported"] < top["value_reported"]),
            "best_rolling_4q_usd_m": record["best_rolling_4q_usd_m"],
            "best_window": record["best_window"],
            "best_window_is_latest": record["best_window_is_latest"],
            "currencies_reported": record["currencies_reported"],
            "revenue_scope": record["revenue_scope"],
            "geography": record["geography"],
            "series_start_reason": cov.get("series_start_reason"),
        })
    order = list(OUTCOMES.values())
    out.sort(key=lambda r: (order.index(r["outcome"]), r["launch_gap_quarters"] or 0, r["drug_name"].lower()))
    return out


def estimate_list(records: list[dict]) -> list[dict]:
    """Every series without an observed peak that an external estimate could be set against."""
    after_launch = {row["drug_name"]: row for row in load("peak_sales.jsonl") if row.get("series_starts_after_launch")}
    rows = []
    for record in records:
        if record["peak_status"] != "not yet peaked":
            continue
        gap = after_launch.get(record["drug_name"])
        rows.append({
            "drug_name": record["drug_name"],
            "group": f"starts after launch, gap {gap['launch_gap_band']}" if gap else "series starts at launch",
            "best_rolling_4q_usd_m": record["best_rolling_4q_usd_m"],
            "best_window": record["best_window"],
            "best_rolling_4q_reported": record["best_rolling_4q_reported"],
            "best_window_currency": record["best_window_currency"],
            "currencies_reported": record["currencies_reported"],
            "best_annual_usd_m": record["best_annual_usd_m"],
            "best_window_is_latest": record["best_window_is_latest"],
            "latest_vs_best": record["latest_vs_best"],
            "geography": record["geography"],
            "revenue_scope": record["revenue_scope"],
            "manufacturer": record["manufacturer"],
            "first_quarter": record["first_quarter"],
            "last_quarter": record["last_quarter"],
            "series_current": record["series_current"],
            "first_approval_year": record["first_approval_year"],
        })
    rows.sort(key=lambda r: -(r["best_rolling_4q_usd_m"] or r["best_annual_usd_m"] or 0))
    return rows


def main() -> None:
    records = peak_records()
    review = launch_gap_review(records)
    sheets = {
        "For external estimates": estimate_list(records),
        "Not yet peaked": [r for r in records if r["peak_status"] == "not yet peaked"],
        "Peak observed": [r for r in records if r["peak_status"] == "peak observed"],
        "Line ended at high": [r for r in records if r["peak_status"] == "line ended at high"],
        "Not judged": [r for r in records if r["peak_status"].startswith("not judged")],
        "Peak unknown - restricted": [r for r in records if r["peak_status"] == RESTRICTED],
        "Starts after launch review": review,
    }
    notes = {
        "For external estimates": "Every series without an observed peak, including the series that start "
                                  "after launch with few enough launch quarters missing to be judged. "
                                  "best_rolling_4q_reported is the same window in the issuer's own currency "
                                  "(best_window_currency); blank where the window spans a currency change. "
                                  "Annual-only products carry best_annual_usd_m instead.",
        "Starts after launch review": "Series that start after launch, with gold's launch-gap review: "
                                      "launch_gap_quarters counts the quarters between launch and the first "
                                      "reported quarter, and outcome is gold's peak judgement for the series.",
        "Not yet peaked": "Products whose highest rolling four-quarter total is not yet followed by "
                          "two later totals. best_rolling_4q_usd_m is the highest sum of four consecutive "
                          "quarters in USD millions; best_window_is_latest marks a product whose best "
                          "window is still its latest, i.e. still growing.",
        "Peak observed": "Products whose highest rolling four-quarter total is followed by at least two "
                         "later totals, none higher.",
        "Line ended at high": "Products whose series stops before two totals follow its highest one. The "
                              "high is a benchmark; time to peak is not known.",
        "Peak unknown - restricted": "Products with a quarter (or year) whose source is a company website "
                                     "at or near their highest window; those rows are withheld from this "
                                     "export, so the peak cannot be read. quarters_withheld counts them.",
        "Not judged": "Series that start after launch and that gold's launch-gap review does not judge: too "
                      "many launch quarters missing, no complete four-quarter window, or a highest window that "
                      "may not be the peak.",
    }

    wb = Workbook()
    wb.remove(wb.active)
    head = Font(name="Arial", bold=True, color="FFFFFF")
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        ws.cell(1, 1, notes[title]).font = Font(name="Arial", italic=True)
        columns = list(dict.fromkeys(key for record in (rows or records) for key in record))
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
