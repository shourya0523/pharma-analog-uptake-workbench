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
        currencies = sorted({r["currency"] for r in rows if r.get("currency")})
        reported = best_reported(rows, best) if best else None
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
    return records


# How many quarters may be missing between launch and the first reported
# quarter before the gap stops being small, and before it is substantial. A
# year missing leaves the launch shape mostly intact; more than two years
# leaves an uptake curve without its early quarters.
SMALL_GAP_QUARTERS = 4
SUBSTANTIAL_GAP_QUARTERS = 8

PEAK_FOUND = "peak identifiable from the reported years"
STILL_GROWING = "not yet peaked - needs an external estimate"
PEAK_BEFORE_SERIES = "highest year is the first complete year - the peak may predate the series"
TOO_SHORT = "no complete calendar year reported"
EXCLUDE = "substantial launch history missing - flag for exclusion"


def launch_gap_review(records: list[dict]) -> list[dict]:
    """For each series that starts after launch: how much is missing, and whether its peak can be read.

    The launch reference is the series' stated launch quarter, or where none is
    stated the fourth quarter of the first approval year, which makes the gap a
    minimum. The peak test is gold's own calendar-year rule (``observed_peak``)
    run on the reported years; a maximum with no lower complete year before it
    is not read as a peak when quarters are missing before the series, because
    the true peak may sit in them.
    """
    from build_independent_gold import full_annual_totals, observed_peak

    quarterly = load("quarterly_revenue.jsonl")
    coverage = {row["drug_name"]: row for row in load("series_coverage.jsonl")}
    profiles = {row["drug_name"]: row for row in load("product_profiles.jsonl")}
    out = []
    for record in records:
        if not record["peak_status"].startswith("not judged"):
            continue
        drug = record["drug_name"]
        cov = coverage.get(drug, {})
        if cov.get("launch_quarter"):
            reference, basis = cov["launch_quarter"], "stated launch quarter"
        else:
            year = profiles.get(drug, {}).get("first_approval_year")
            reference, basis = (f"{year}Q4", "approval year (gap is a minimum)") if year else (None, None)
        gap = max(quarter_index(record["first_quarter"]) - quarter_index(reference), 0) if reference else None
        totals = full_annual_totals(quarterly, drug)
        peak = observed_peak(drug, totals, "", "") if totals else None
        top = max(totals, key=lambda r: r["value_reported"]) if totals else None
        lower_before = bool(top and any(r["value_reported"] < top["value_reported"]
                                        for r in totals if r["period"] < top["period"]))
        if gap is None or gap > SUBSTANTIAL_GAP_QUARTERS:
            outcome = EXCLUDE
        elif not totals:
            outcome = TOO_SHORT
        elif peak["peak_status"] != "observed":
            outcome = STILL_GROWING
        elif lower_before or gap == 0:
            outcome = PEAK_FOUND
        else:
            outcome = PEAK_BEFORE_SERIES
        out.append({
            "drug_name": drug,
            "outcome": outcome,
            "launch_reference": reference,
            "launch_reference_basis": basis,
            "first_quarter": record["first_quarter"],
            "launch_gap_quarters": gap,
            "gap_band": (None if gap is None else "none" if gap == 0 else "small" if gap <= SMALL_GAP_QUARTERS
                         else "moderate" if gap <= SUBSTANTIAL_GAP_QUARTERS else "substantial"),
            "unreported_quarters_inside": len(cov.get("unreported_quarters") or []) + len(cov.get("missing_quarters") or []),
            "quarters": record["quarters"],
            "complete_years": len(totals),
            "highest_calendar_year": int(top["period"]) if top else None,
            "highest_calendar_year_usd_m": round(top["value_reported"], 3) if top else None,
            "lower_year_before_highest": lower_before,
            "lower_years_after_highest": sum(1 for r in totals if top and r["period"] > top["period"]
                                             and r["value_reported"] < top["value_reported"]),
            "calendar_year_rule": None if not peak else "observed" if peak["peak_status"] == "observed"
                                  else "beside an unreported year"
                                  if peak["selection_method"] == "maximum_beside_an_unreported_year"
                                  else "not observed",
            "best_rolling_4q_usd_m": record["best_rolling_4q_usd_m"],
            "best_window": record["best_window"],
            "best_window_is_latest": record["best_window_is_latest"],
            "currencies_reported": record["currencies_reported"],
            "revenue_scope": record["revenue_scope"],
            "geography": record["geography"],
            "series_start_reason": cov.get("series_start_reason"),
        })
    order = [PEAK_FOUND, STILL_GROWING, PEAK_BEFORE_SERIES, TOO_SHORT, EXCLUDE]
    out.sort(key=lambda r: (order.index(r["outcome"]), r["launch_gap_quarters"] or 0, r["drug_name"].lower()))
    return out


def estimate_list(records: list[dict], review: list[dict]) -> list[dict]:
    """Every series without an observed peak that an external estimate could be set against."""
    growing = {r["drug_name"]: r for r in review if r["outcome"] == STILL_GROWING}
    rows = []
    for record in records:
        if record["peak_status"] == "not yet peaked" or record["drug_name"] in growing:
            gap = growing.get(record["drug_name"])
            rows.append({
                "drug_name": record["drug_name"],
                "group": "starts after launch, gap " + gap["gap_band"] if gap else "peak-eligible, not yet peaked",
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
        "For external estimates": estimate_list(records, review),
        "Not yet peaked": [r for r in records if r["peak_status"] == "not yet peaked"],
        "Peak observed": [r for r in records if r["peak_status"] == "peak observed"],
        "Not judged": [r for r in records if r["peak_status"].startswith("not judged")],
        "Starts after launch review": review,
    }
    notes = {
        "For external estimates": "Every series without an observed peak: the peak-eligible products not yet "
                                  "peaked, and the series that start after launch whose reported years are "
                                  "still growing with at most " + str(SUBSTANTIAL_GAP_QUARTERS) + " launch "
                                  "quarters missing. best_rolling_4q_reported is the same window in the "
                                  "issuer's own currency (best_window_currency); blank where the window spans "
                                  "a currency change. Annual-only products carry best_annual_usd_m instead.",
        "Starts after launch review": "Series that start after launch. launch_gap_quarters counts the quarters "
                                      "between launch and the first reported quarter (small: up to "
                                      + str(SMALL_GAP_QUARTERS) + "; moderate: up to "
                                      + str(SUBSTANTIAL_GAP_QUARTERS) + "; substantial: more). outcome "
                                      "applies gold's calendar-year peak rule to the reported years; a "
                                      "highest year with no lower year before it is not read as a peak when "
                                      "launch quarters are missing.",
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
