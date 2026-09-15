"""Export the gold dataset to a single Excel workbook.

Every data sheet is a faithful dump of a gold file - no re-derivation, no
rounding, no reordering of values. Quarterly Matrix, Launch-Aligned Matrix,
Refresh Status and Product Summary are views over those sheets, all generated
here rather than written as Excel formulas, because an openpyxl-written formula
carries no cached value and reads back blank to pandas and to previewers until
something recalculates it. Regenerate the workbook instead of editing it - this
script is the only thing that should write to exports/.

Reads seed/gold only. It must never import from the gold builder or from
application code, and must never name a file the pipeline reads: this is a
presentation of the oracle, not part of it, and a script that touches both
directions is what backend/tests/test_gold_is_not_an_input.py fails. That is
why first_approval_year is taken from gold's own product_profiles rather than
from the reference data those profiles were built out of.

    python scripts/export_gold_workbook.py
"""

import json
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

REPO = Path(__file__).resolve().parents[1]
GOLD = REPO / "seed" / "gold"
OUT = REPO / "exports" / "pah_gold_dataset.xlsx"

FONT = "Arial"
INK = "1F3864"
HEAD_FILL = PatternFill("solid", fgColor=INK)
BAND_FILL = PatternFill("solid", fgColor="F2F5FA")
TITLE = Font(name=FONT, size=14, bold=True, color=INK)
HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
BODY = Font(name=FONT, size=10)
NOTE = Font(name=FONT, size=9, italic=True, color="5B6B7F")
BOLD = Font(name=FONT, size=10, bold=True)
THIN = Side(style="thin", color="D6DCE4")
GRID = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

MONEY = '#,##0.0;(#,##0.0);-'
PCT = '0.0"%"'


def load(name):
    path = GOLD / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def flat(value):
    """Lists and dicts have to become text, but they must stay readable."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        if not value:
            return ""
        if all(isinstance(item, str) for item in value):
            return " | ".join(value)
        return json.dumps(value, separators=(", ", ": "))
    if isinstance(value, dict):
        return json.dumps(value, separators=(", ", ": "))
    return value


def quarter_index(period):
    """A monotonic integer for a ``YYYYQn`` label, so quarters can be subtracted."""
    return int(period[:4]) * 4 + int(period[5]) - 1


def quarters_between(start, end):
    """Quarters from start to end inclusive; negative when end precedes start."""
    return quarter_index(end) - quarter_index(start) + 1


def launch_label(offset):
    """The relative-quarter heading `offset` quarters after a series' anchor.

    Offset 0 is the anchor quarter itself and reads Year 1 Q1, so the fifth
    quarter after launch reads Year 2 Q1.
    """
    return f"Year {offset // 4 + 1} Q{offset % 4 + 1}"


def write_sheet(wb, title, columns, rows, *, widths=None, formats=None, wrap=(), note=None):
    ws = wb.create_sheet(title)
    header_row = 1
    if note:
        ws.cell(1, 1, note).font = NOTE
        header_row = 2

    for index, column in enumerate(columns, start=1):
        cell = ws.cell(header_row, index, column)
        cell.font = HEAD
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = GRID
    ws.row_dimensions[header_row].height = 30

    for offset, row in enumerate(rows):
        excel_row = header_row + 1 + offset
        for index, column in enumerate(columns, start=1):
            cell = ws.cell(excel_row, index, flat(row.get(column)))
            cell.font = BODY
            cell.border = GRID
            if offset % 2:
                cell.fill = BAND_FILL
            if formats and column in formats:
                cell.number_format = formats[column]
            if column in wrap:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            else:
                cell.alignment = Alignment(vertical="top")

    for index, column in enumerate(columns, start=1):
        letter = get_column_letter(index)
        ws.column_dimensions[letter].width = (widths or {}).get(column, 16)

    last = header_row + len(rows)
    if rows:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(columns))}{last}"
    ws.freeze_panes = ws.cell(header_row + 1, 3)
    return ws


wb = Workbook()
wb.remove(wb.active)

# ---------------------------------------------------------------- data sheets

profiles = load("product_profiles")
quarterly = load("quarterly_revenue")
annual = load("annual_revenue")
coverage = load("series_coverage")
peaks = load("peak_sales")
excluded = load("excluded_products")
cases = load("adjudication_cases")
unresolved = load("unresolved_quarters")
manifest = json.loads((GOLD / "manifest.json").read_text())
report = json.loads((GOLD / "build_report.json").read_text())

# Quarterly rows carry a `sources` list; the primary source already has its own
# columns, so only the extras need somewhere to go.
for row in quarterly:
    extras = [s["source_url"] for s in (row.get("sources") or []) if s["source_url"] != row["source_url"]]
    row["additional_source_urls"] = " | ".join(extras)
    row["bridge_components"] = flat(row.get("bridge_components"))

Q_COLS = [
    "drug_name", "generic_name", "manufacturer", "period", "calendar_year", "calendar_quarter",
    "fiscal_year", "fiscal_quarter", "period_basis", "benchmark_identity", "revenue_scope",
    "geography", "value_normalized_usd_millions", "unit", "currency", "value_reported",
    "source_value_reported", "source_unit", "precision", "derivation", "confidence_score",
    "validation_status", "therapeutic_area", "route_of_administration", "formulation",
    "extraction_method", "source_type", "source_url", "source_quote", "additional_source_urls",
    "bridge_components", "gold_notes", "gold_id",
]
write_sheet(
    wb, "Quarterly Revenue", Q_COLS, quarterly,
    note="Every reported quarter in gold. value_normalized_usd_millions is the figure to use; "
         "value_reported and source_value_reported preserve the issuer's own number and unit.",
    widths={"drug_name": 18, "generic_name": 18, "manufacturer": 20, "benchmark_identity": 30,
            "revenue_scope": 22, "geography": 16, "value_normalized_usd_millions": 14,
            "derivation": 30, "therapeutic_area": 24, "source_url": 52, "source_quote": 70,
            "additional_source_urls": 40, "bridge_components": 40, "gold_notes": 40,
            "gold_id": 34, "extraction_method": 26, "validation_status": 16, "precision": 16},
    formats={"value_normalized_usd_millions": MONEY, "value_reported": MONEY,
             "source_value_reported": '#,##0.0##', "confidence_score": "0.00"},
    wrap=("source_quote", "gold_notes", "bridge_components", "additional_source_urls"),
)

A_COLS = [
    "drug_name", "generic_name", "manufacturer", "period", "period_basis", "benchmark_identity",
    "revenue_scope", "geography", "value_normalized_usd_millions", "unit", "currency",
    "value_reported", "source_value_reported", "source_unit", "fx_rate_to_usd", "fx_rate_source",
    "derivation", "series_role", "confidence_score", "validation_status", "extraction_method",
    "source_type", "source_url", "source_quote", "gold_id",
]
write_sheet(
    wb, "Annual Revenue", A_COLS, annual,
    note="Annual figures. series_role says whether a row is a peak benchmark in its own right "
         "or annual context for a series measured quarterly.",
    widths={"drug_name": 18, "generic_name": 18, "manufacturer": 20, "benchmark_identity": 34,
            "revenue_scope": 26, "geography": 16, "value_normalized_usd_millions": 14,
            "derivation": 30, "series_role": 18, "source_url": 52, "source_quote": 70,
            "gold_id": 34, "extraction_method": 26, "fx_rate_source": 24},
    formats={"value_normalized_usd_millions": MONEY, "value_reported": MONEY,
             "source_value_reported": '#,##0.0##', "confidence_score": "0.00",
             "fx_rate_to_usd": "0.0000"},
    wrap=("source_quote",),
)

P_COLS = [
    "drug_name", "indication_area", "moa", "moa_class", "route_of_administration",
    "first_approval_year", "approval_era", "competitive_intensity_at_launch",
    "marketed_peers_at_launch", "competitive_intensity_basis", "peer_universe_role",
    "attribute_provenance",
]
write_sheet(
    wb, "Product Profiles", P_COLS, profiles,
    note="The analog-matching attributes, one row per product appearing anywhere in gold. "
         "Competitive intensity is derived from marketed_peers_at_launch, not hand-assigned; "
         "outside the catalog's own indication universe it is left unassessed rather than guessed.",
    widths={"drug_name": 20, "indication_area": 34, "moa": 40, "moa_class": 26,
            "route_of_administration": 22, "first_approval_year": 14, "approval_era": 14,
            "competitive_intensity_at_launch": 18, "marketed_peers_at_launch": 14,
            "competitive_intensity_basis": 38, "peer_universe_role": 24,
            "attribute_provenance": 20},
    wrap=("moa",),
)

C_COLS = [
    "drug_name", "benchmark_identity", "moa", "moa_class", "route_of_administration",
    "approval_era", "competitive_intensity_at_launch", "launch_quarter", "commercial_start_quarter",
    "series_start_reason", "series_end_quarter", "series_end_basis", "series_end_reason",
    "as_of_quarter", "expected_quarters", "observed_quarters", "coverage_pct", "missing_quarters",
    "quarters_beyond_series_end", "benchmark_eligible",
]
latest_quarter = max(row["period"] for row in quarterly)
short_series = [row for row in coverage if row["series_end_quarter"] != latest_quarter]

write_sheet(
    wb, "Series Coverage", C_COLS, coverage,
    note=f"One row per series the gold builder carries metadata for. Coverage is measured "
         f"against each series' own series_end_quarter, not against the newest quarter in the "
         f"dataset ({latest_quarter}), so a series that ends earlier is complete rather than "
         f"short. {len(short_series)} of {len(coverage)} end before {latest_quarter}; "
         f"series_end_basis says whether more sourcing could move that end, and the Refresh "
         f"Status sheet ranks every series by how far behind it sits.",
    widths={"drug_name": 20, "benchmark_identity": 34, "moa": 38, "moa_class": 26,
            "route_of_administration": 20, "series_start_reason": 46, "series_end_reason": 46,
            "series_end_basis": 26, "missing_quarters": 24, "quarters_beyond_series_end": 24},
    formats={"coverage_pct": PCT},
    wrap=("moa", "series_start_reason", "series_end_reason"),
)

# ------------------------------------------------------------ refresh status
# Series Coverage has a row only where the gold builder carries metadata for a
# product, so it cannot answer "is this series current?" for a series the
# builder does not know about. This sheet is a census of the series the data
# actually holds, which is the wider set, and for each one it reports the
# distance to the newest quarter anywhere in gold and whether that distance is
# closable. An end because the issuer stopped publishing the line is a fact
# about the world; an end because sourcing stopped is a fact about this
# dataset, and only the second one is closable by refreshing.

REFRESH_OUTLOOK = {
    "issuer_stopped_reporting": "closed - issuer stopped reporting the line",
    "sourcing_boundary": "extendable - sourcing stopped, the issuer did not",
}

coverage_by_name = {row["drug_name"]: row for row in coverage}
observed_periods = defaultdict(set)
series_facts = {}
for row in quarterly:
    observed_periods[row["drug_name"]].add(row["period"])
    series_facts[row["drug_name"]] = (row["benchmark_identity"], row.get("manufacturer"))

refresh = []
for drug_name, periods in observed_periods.items():
    seen = coverage_by_name.get(drug_name)
    last_reported = max(periods)
    end_quarter = seen["series_end_quarter"] if seen else last_reported
    basis = (seen or {}).get("series_end_basis")
    if end_quarter == latest_quarter:
        outlook = "current"
    elif seen is None:
        # No coverage row means no metadata stated an end, so nothing says
        # whether the issuer stopped or the sourcing did.
        outlook = "unknown - series has no coverage row"
    else:
        outlook = REFRESH_OUTLOOK.get(basis, f"unstated basis: {basis}")
    identity, manufacturer = series_facts[drug_name]
    refresh.append({
        "drug_name": drug_name,
        "benchmark_identity": identity,
        "manufacturer": manufacturer,
        "first_reported_quarter": min(periods),
        "last_reported_quarter": last_reported,
        "series_end_quarter": end_quarter,
        "latest_quarter_in_gold": latest_quarter,
        "quarters_behind_latest": quarters_between(end_quarter, latest_quarter) - 1,
        "refresh_outlook": outlook,
        "series_end_basis": basis,
        "in_series_coverage": seen is not None,
        "series_end_reason": (seen or {}).get("series_end_reason"),
    })
refresh.sort(key=lambda item: (-item["quarters_behind_latest"], item["drug_name"]))

behind = [item for item in refresh if item["quarters_behind_latest"] > 0]
extendable = [item for item in behind if item["series_end_basis"] == "sourcing_boundary"]
uncovered = [item for item in refresh if not item["in_series_coverage"]]
write_sheet(
    wb, "Refresh Status", list(refresh[0].keys()), refresh,
    note=f"How current each quarterly series is, for planning a refresh. The newest quarter "
         f"anywhere in gold is {latest_quarter}; {len(behind)} of {len(refresh)} series end "
         f"before it. Of those, {len(extendable)} are marked sourcing_boundary, meaning the "
         f"issuer kept publishing and this dataset stopped - those are the ones a refresh can "
         f"move. {len(uncovered)} series have no Series Coverage row at all, so no end quarter "
         f"or reason was ever stated for them. Sorted by how far behind each series sits.",
    widths={"drug_name": 20, "benchmark_identity": 34, "manufacturer": 20,
            "first_reported_quarter": 14, "last_reported_quarter": 14,
            "series_end_quarter": 14, "latest_quarter_in_gold": 14,
            "quarters_behind_latest": 13, "refresh_outlook": 40,
            "series_end_basis": 24, "in_series_coverage": 13, "series_end_reason": 60},
    formats={"quarters_behind_latest": "0"},
    wrap=("refresh_outlook", "series_end_reason"),
)

K_COLS = [
    "drug_name", "moa", "moa_class", "route_of_administration", "approval_era",
    "competitive_intensity_at_launch", "peak_status", "numeric_peak_available", "peak_value",
    "peak_year", "highest_observed_value", "highest_observed_year", "highest_observed_period",
    "post_peak_years", "annual_observations", "revenue_scope", "geography", "unit", "currency",
    "selection_method", "benchmark_eligible", "input_ids", "gold_id",
]
write_sheet(
    wb, "Peak Sales", K_COLS, peaks,
    note="peak_status observed means the series has turned down and stayed down; "
         "not_yet_observed means the highest value so far is still the latest value.",
    widths={"drug_name": 20, "moa": 38, "moa_class": 26, "route_of_administration": 20,
            "selection_method": 46, "input_ids": 60, "gold_id": 26, "revenue_scope": 22,
            "peak_status": 18, "highest_observed_period": 18},
    formats={"peak_value": MONEY, "highest_observed_value": MONEY},
    wrap=("moa", "selection_method", "input_ids"),
)

E_COLS = [
    "drug_name", "benchmark_status", "reason_code", "details", "moa", "moa_class",
    "route_of_administration", "approval_era", "competitive_intensity_at_launch",
    "extraction_method", "source_url", "source_quote", "gold_id",
]
write_sheet(
    wb, "Excluded Products", E_COLS, excluded,
    note="Products deliberately kept out of the benchmark set, each with the evidence for why. "
         "An exclusion still carries its matching attributes, so it can serve as an analog "
         "comparator even where it cannot serve as a peak benchmark.",
    widths={"drug_name": 20, "reason_code": 34, "details": 60, "moa": 38, "moa_class": 26,
            "source_url": 52, "source_quote": 70, "gold_id": 24, "extraction_method": 26},
    wrap=("details", "source_quote"),
)

for case in cases:
    case["expect"] = flat(case.get("expect"))
    case["inputs"] = flat(case.get("inputs"))
CASE_COLS = ["case_id", "kind", "provenance", "why", "inputs", "expect"]
write_sheet(
    wb, "Adjudication Cases", CASE_COLS, cases,
    note="Genuinely ambiguous situations found in the filings, with the expected resolution. "
         "These are the test cases an extraction pipeline is graded against.",
    widths={"case_id": 34, "kind": 20, "provenance": 14, "why": 80, "inputs": 70, "expect": 34},
    wrap=("why", "inputs", "expect"),
)

# ------------------------------------------------------------- source index

index_rows = {}
for row in quarterly:
    entry = index_rows.setdefault(row["source_url"], {
        "source_url": row["source_url"], "source_type": row["source_type"],
        "products": set(), "periods": set(), "quarterly_rows": 0, "annual_rows": 0})
    entry["quarterly_rows"] += 1
    entry["products"].add(row["drug_name"])
    entry["periods"].add(row["period"])
for row in annual:
    entry = index_rows.setdefault(row["source_url"], {
        "source_url": row["source_url"], "source_type": row["source_type"],
        "products": set(), "periods": set(), "quarterly_rows": 0, "annual_rows": 0})
    entry["annual_rows"] += 1
    entry["products"].add(row["drug_name"])
    entry["periods"].add(row["period"])

sources = []
for entry in index_rows.values():
    periods = sorted(entry["periods"])
    sources.append({
        "source_url": entry["source_url"],
        "source_type": entry["source_type"],
        "quarterly_rows": entry["quarterly_rows"],
        "annual_rows": entry["annual_rows"],
        "total_rows": entry["quarterly_rows"] + entry["annual_rows"],
        "products_cited": ", ".join(sorted(entry["products"])),
        "earliest_period": periods[0],
        "latest_period": periods[-1],
    })
sources.sort(key=lambda item: (-item["total_rows"], item["source_url"]))
write_sheet(
    wb, "Source Index", list(sources[0].keys()), sources,
    note="Every distinct filing or release cited by a revenue row, and how much of gold rests "
         "on it. Row counts are a census of the two revenue sheets.",
    widths={"source_url": 80, "source_type": 16, "products_cited": 50,
            "quarterly_rows": 13, "annual_rows": 11, "total_rows": 11,
            "earliest_period": 14, "latest_period": 14},
    wrap=("products_cited",),
)

# ---------------------------------------------------------- quarterly matrix
# A pivot of Quarterly Revenue, generated rather than written as SUMIFS: an
# openpyxl formula carries no cached value, so the cells would read back blank
# until something recalculated the file.

periods = sorted({row["period"] for row in quarterly})
series = sorted({(row["drug_name"], row["benchmark_identity"], row["revenue_scope"])
                 for row in quarterly})
# first_approval_year is a curated attribute, carried here from gold's own
# profiles. A series whose product has no profile row leaves the column empty
# rather than borrowing a year from somewhere else.
approval_year = {row["drug_name"]: row.get("first_approval_year") for row in profiles}
# The pivot reads from a dict keyed exactly as the data sheet stores each row,
# so a value can only appear here if that same row appears there.
cube = {(row["benchmark_identity"], row["period"]): row["value_normalized_usd_millions"]
        for row in quarterly}
assert len(cube) == len(quarterly), "a series reports the same quarter twice"

LEAD = ["drug_name", "benchmark_identity", "revenue_scope", "first_approval_year"]

ws = wb.create_sheet("Quarterly Matrix")
ws.cell(1, 1, "Reported revenue by series and calendar quarter, USD millions. A pivot of the "
              "Quarterly Revenue sheet - the same figures rearranged, nothing recomputed. A "
              "blank cell means the quarter is outside that series' reported window, never a "
              "reported zero. Launch-Aligned Matrix holds these same values against each "
              "product's own launch instead of the calendar.").font = NOTE
headers = LEAD + periods
for index, name in enumerate(headers, start=1):
    cell = ws.cell(2, index, name)
    cell.font = HEAD
    cell.fill = HEAD_FILL
    cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
    cell.border = GRID
ws.row_dimensions[2].height = 30

for offset, (drug, identity, scope) in enumerate(series):
    row_index = 3 + offset
    for index, value in enumerate((drug, identity, scope, approval_year.get(drug)), start=1):
        cell = ws.cell(row_index, index, value)
        cell.font = BODY
        cell.border = GRID
        if index == 4:
            cell.number_format = "0"
        if offset % 2:
            cell.fill = BAND_FILL
    for index, period in enumerate(periods, start=len(LEAD) + 1):
        cell = ws.cell(row_index, index, cube.get((identity, period)))
        cell.number_format = MONEY
        cell.font = BODY
        cell.border = GRID
        if offset % 2:
            cell.fill = BAND_FILL

for letter, width in zip("ABCD", (20, 34, 24, 13)):
    ws.column_dimensions[letter].width = width
for index in range(len(LEAD) + 1, len(LEAD) + 1 + len(periods)):
    ws.column_dimensions[get_column_letter(index)].width = 10
ws.freeze_panes = ws.cell(3, len(LEAD) + 1)

# ----------------------------------------------------- launch-aligned matrix
# The same cube read against each product's own launch instead of the calendar,
# so uptake curves can be compared side by side from their first quarter on
# sale rather than from a shared date.
#
# The anchor is the launch quarter gold states, NOT the first quarter the
# series reports. Those differ for most products here, and anchoring on the
# first reported quarter would label a series that begins mid-life as Year 1 Q1
# and align it against a genuine launch. Where the two differ the leading cells
# are blank and quarters_from_launch_to_first_report says how many, so a reader
# can see that the early curve is missing rather than flat.
#
#     a series launching 2019Q1 and first reported 2019Q3 puts its first value
#     at Year 1 Q3, with Year 1 Q1 and Year 1 Q2 blank
#
# Where gold states no launch quarter the anchor falls back to the first
# reported quarter, and the row says so: its Year 1 Q1 is where reporting
# starts, which need not be where the product launched. The fallback is not
# read as a launch date, because nothing in gold says it is one.

ANCHOR_STATED = "launch quarter stated in gold"
ANCHOR_ASSUMED = "no launch quarter in gold - anchored on first reported quarter"

aligned = []
for drug, identity, scope in series:
    first_reported = min(observed_periods[drug])
    launch = coverage_by_name.get(drug, {}).get("launch_quarter")
    aligned.append({
        "drug_name": drug,
        "benchmark_identity": identity,
        "revenue_scope": scope,
        "first_approval_year": approval_year.get(drug),
        "launch_anchor_quarter": launch or first_reported,
        "alignment_basis": ANCHOR_STATED if launch else ANCHOR_ASSUMED,
        "quarters_from_launch_to_first_report":
            quarters_between(launch or first_reported, first_reported) - 1,
    })

A_LEAD = list(aligned[0].keys())
span = max(quarters_between(row["launch_anchor_quarter"], max(observed_periods[row["drug_name"]]))
           for row in aligned)

la = wb.create_sheet("Launch-Aligned Matrix")
la.cell(1, 1, f"The Quarterly Matrix values re-indexed to each product's own launch: Year 1 Q1 "
              f"is the anchor quarter in launch_anchor_quarter, Year 2 Q1 the fifth quarter "
              f"after it. Same figures, same blanks-are-not-zeros rule - only the column "
              f"headings change. Read alignment_basis before comparing curves: a row anchored "
              f"on a stated launch quarter is launch-to-date, a row anchored on its first "
              f"reported quarter is not. quarters_from_launch_to_first_report is how much of "
              f"the early curve gold does not hold; it is 0 where the series starts at launch.").font = NOTE
headers = A_LEAD + [launch_label(offset) for offset in range(span)]
for index, name in enumerate(headers, start=1):
    cell = la.cell(2, index, name)
    cell.font = HEAD
    cell.fill = HEAD_FILL
    cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
    cell.border = GRID
la.row_dimensions[2].height = 30

for offset, row in enumerate(aligned):
    row_index = 3 + offset
    for index, column in enumerate(A_LEAD, start=1):
        cell = la.cell(row_index, index, row[column])
        cell.font = BODY
        cell.border = GRID
        if column in ("first_approval_year", "quarters_from_launch_to_first_report"):
            cell.number_format = "0"
        if offset % 2:
            cell.fill = BAND_FILL
    for period in sorted(observed_periods[row["drug_name"]]):
        index = len(A_LEAD) + quarters_between(row["launch_anchor_quarter"], period)
        cell = la.cell(row_index, index, cube[(row["benchmark_identity"], period)])
        cell.number_format = MONEY
        cell.font = BODY
        cell.border = GRID
    # The banding has to be painted across the whole row, not only where the
    # series reports, or a short series loses its stripe past its last quarter.
    if offset % 2:
        for index in range(len(A_LEAD) + 1, len(headers) + 1):
            la.cell(row_index, index).fill = BAND_FILL

for index, width in enumerate((20, 34, 24, 13, 15, 30, 15), start=1):
    la.column_dimensions[get_column_letter(index)].width = width
for index in range(len(A_LEAD) + 1, len(headers) + 1):
    la.column_dimensions[get_column_letter(index)].width = 11
la.freeze_panes = la.cell(3, len(A_LEAD) + 1)

# ------------------------------------------------------- product summary
# One row per product, joining the profile, the revenue sheets and the peak
# sheet so a single product can be read without crossing four tabs. Every
# number here is generated from those sheets' own rows, for the same reason the
# matrices are: a written formula would read back blank until recalculated.

SUMMARY_STATUS = {}
quarterly_products = {row["drug_name"] for row in quarterly}
annual_products = {row["drug_name"] for row in annual}
excluded_products = {row["drug_name"] for row in excluded}
for row in profiles:
    name = row["drug_name"]
    if name in quarterly_products:
        SUMMARY_STATUS[name] = "quarterly series"
    elif name in annual_products:
        SUMMARY_STATUS[name] = "annual benchmark only"
    elif name in excluded_products:
        SUMMARY_STATUS[name] = "excluded from benchmark"
    else:
        SUMMARY_STATUS[name] = "attributes only"

ps = wb.create_sheet("Product Summary")
ps.cell(1, 1, "One row per product, so a product can be read without crossing four tabs. "
              "Attributes come from Product Profiles; counts, totals and peaks are a census of "
              "the Quarterly Revenue, Annual Revenue and Peak Sales sheets, generated by "
              "scripts/export_gold_workbook.py. A blank peak column means the product has no "
              "peak row - it is either excluded or has not turned down yet.").font = NOTE

S_HEAD = ["drug_name", "role in gold", "indication_area", "moa_class", "route_of_administration",
          "approval_era", "competitive_intensity_at_launch", "marketed_peers_at_launch",
          "quarterly rows", "annual rows", "first calendar year", "last calendar year",
          "total reported (USD mm)", "peak status", "peak value (USD mm)", "peak year"]
for index, name in enumerate(S_HEAD, start=1):
    cell = ps.cell(2, index, name)
    cell.font = HEAD
    cell.fill = HEAD_FILL
    cell.alignment = Alignment(vertical="center", wrap_text=True)
    cell.border = GRID
ps.row_dimensions[2].height = 32

peak_index = {row["drug_name"]: row for row in peaks}
for offset, row in enumerate(sorted(profiles, key=lambda item: item["drug_name"])):
    n = 3 + offset
    name = row["drug_name"]
    q_rows = [item for item in quarterly if item["drug_name"] == name]
    a_rows = [item for item in annual if item["drug_name"] == name]
    peak = peak_index.get(name)
    years = [item["calendar_year"] for item in q_rows]
    values = [
        name, SUMMARY_STATUS[name], row.get("indication_area"), row.get("moa_class"),
        row.get("route_of_administration"), row.get("approval_era"),
        row.get("competitive_intensity_at_launch"), row.get("marketed_peers_at_launch"),
        len(q_rows), len(a_rows),
        min(years) if years else None,
        max(years) if years else None,
        round(sum(item["value_normalized_usd_millions"] for item in q_rows), 1) if q_rows else None,
        peak["peak_status"] if peak else None,
        peak["peak_value"] if peak else None,
        peak["peak_year"] if peak else None,
    ]
    for index, value in enumerate(values, start=1):
        cell = ps.cell(n, index, value)
        cell.font = BODY
        cell.border = GRID
        if offset % 2:
            cell.fill = BAND_FILL
        if index in (13, 15):
            cell.number_format = MONEY
        if index in (11, 12, 16):
            cell.number_format = "0"
        cell.alignment = Alignment(vertical="top")

for index, width in enumerate([20, 22, 34, 26, 20, 13, 15, 13, 12, 11, 13, 13, 16, 18, 14, 11], start=1):
    ps.column_dimensions[get_column_letter(index)].width = width
ps.auto_filter.ref = f"A2:P{2 + len(profiles)}"
ps.freeze_panes = "C3"

# --------------------------------------------------------------- read me

read = wb.create_sheet("Read Me", 0)
read.column_dimensions["A"].width = 34
read.column_dimensions["B"].width = 16
read.column_dimensions["C"].width = 92


def line(row, label, value=None, *, font=BODY, note=None, fmt=None):
    cell = read.cell(row, 1, label)
    cell.font = font
    if value is not None:
        v = read.cell(row, 2, value)
        v.font = BOLD
        v.alignment = Alignment(horizontal="left")
        if fmt:
            v.number_format = fmt
    if note:
        n = read.cell(row, 3, note)
        n.font = BODY
        n.alignment = Alignment(vertical="top", wrap_text=True)
    return row + 1


r = 1
read.cell(r, 1, "PAH Peak Sales - Gold Dataset").font = TITLE
r += 1
read.cell(r, 1, f"Independently researched from issuer filings and releases. "
                f"As of {manifest['as_of_quarter']}.").font = NOTE
r += 2

read.cell(r, 1, "WHAT THIS IS").font = BOLD
r += 1
read.cell(r, 3, "This is the oracle an extraction pipeline is measured against, not pipeline "
                "output. Every revenue figure was read from the issuer's own filing or press "
                "release and carries the URL plus the verbatim line it came from, so any number "
                "here can be checked at source. Where a figure is not printed directly, the "
                "derivation column names the arithmetic used.").font = BODY
read.cell(r, 3).alignment = Alignment(vertical="top", wrap_text=True)
read.row_dimensions[r].height = 62
r += 2

read.cell(r, 1, "CONTENTS").font = BOLD
r += 1
for label, header in [("Sheet", "Rows")]:
    read.cell(r, 1, label).font = HEAD
    read.cell(r, 1).fill = HEAD_FILL
    read.cell(r, 2, header).font = HEAD
    read.cell(r, 2).fill = HEAD_FILL
    read.cell(r, 3, "What it holds").font = HEAD
    read.cell(r, 3).fill = HEAD_FILL
r += 1

CONTENTS = [
    ("Product Summary", len(profiles),
     "Start here. One row per product, joining attributes, revenue totals and peak."),
    ("Quarterly Revenue", len(quarterly),
     "Every reported quarter, with source URL and verbatim quote on each row."),
    ("Quarterly Matrix", len(series),
     "The same quarters pivoted to series x calendar period, one row per series."),
    ("Launch-Aligned Matrix", len(aligned),
     "The same values re-indexed to Year 1 Q1 onwards from each product's own launch, "
     "so uptake curves line up at the start of launch instead of at a shared date."),
    ("Annual Revenue", len(annual),
     "Annual figures: peak benchmarks in their own right, plus annual context for quarterly series."),
    ("Product Profiles", len(profiles),
     "Analog-matching attributes: mechanism, route, approval era, competitive intensity."),
    ("Series Coverage", len(coverage),
     "Per-series completeness: expected vs observed quarters, and any gaps."),
    ("Refresh Status", len(refresh),
     "How current each series is, and whether a series that stops early stops because the "
     "issuer did or because sourcing did. Start here when planning a quarterly refresh."),
    ("Peak Sales", len(peaks),
     "Peak value and year per product, and whether the peak has actually been observed."),
    ("Excluded Products", len(excluded),
     "Products kept out of the benchmark set, each with the evidence for why."),
    ("Adjudication Cases", len(cases),
     "Ambiguous situations found in the filings, with their expected resolution."),
    ("Source Index", len(sources),
     "Every distinct filing cited, and how many rows depend on it."),
    ("Build Manifest", None,
     "The dataset's own manifest and build report, as generated."),
]
for label, ref, description in CONTENTS:
    link = read.cell(r, 1, label)
    link.font = Font(name=FONT, size=10, color="0563C1", underline="single")
    link.hyperlink = f"#'{label}'!A1"
    if ref is not None:
        read.cell(r, 2, ref).font = BOLD
        read.cell(r, 2).number_format = "#,##0"
        read.cell(r, 2).alignment = Alignment(horizontal="left")
    read.cell(r, 3, description).font = BODY
    read.cell(r, 3).alignment = Alignment(vertical="top", wrap_text=True)
    r += 1

r += 1
read.cell(r, 1, "HOW TO READ A REVENUE ROW").font = BOLD
r += 1
GLOSSARY = [
    ("value_normalized_usd_millions", "The figure to use. USD millions, whatever the filing's own unit."),
    ("value_reported / source_unit", "The issuer's own number in the issuer's own unit, preserved."),
    ("derivation", "direct_reported means the issuer printed this figure. Anything else names "
                   "the arithmetic - e.g. annual_less_reported_first_nine_months is a Q4 backed "
                   "out of a stated full year."),
    ("benchmark_identity", "The series key. A product can have more than one - a U.S. line and a "
                           "worldwide line are different series, not the same series twice."),
    ("revenue_scope", "What the issuer's line actually covers. Never mix scopes within a series."),
    ("source_quote", "The verbatim line from the filing, so the number can be checked without "
                     "re-reading the document."),
]
for term, meaning in GLOSSARY:
    read.cell(r, 1, term).font = BODY
    read.cell(r, 3, meaning).font = BODY
    read.cell(r, 3).alignment = Alignment(vertical="top", wrap_text=True)
    if len(meaning) > 110:
        read.row_dimensions[r].height = 28
    r += 1

r += 1
read.cell(r, 1, "COVERAGE AS BUILT").font = BOLD
r += 1
STATS = [
    ("Catalog products", report["catalog_coverage"]["catalog_products"],
     "All accounted for: a quarterly series, an annual benchmark, or an evidenced exclusion."),
    ("Quarterly series", len(refresh),
     f"Counted from the Quarterly Revenue sheet. The build report knows "
     f"{report['complete_quarterly_series']} of them, and those are complete."),
    ("Quarterly observations", len(quarterly),
     "Counted from the Quarterly Revenue sheet."),
    ("Quarterly coverage", report["quarterly_coverage_pct"],
     "No missing quarter in any series, each measured to its own series_end_quarter."),
    ("Series current to " + latest_quarter, len(refresh) - len(behind),
     f"Of {len(refresh)} quarterly series. {len(extendable)} of the rest stop at a sourcing "
     f"boundary rather than because the issuer stopped - see Refresh Status."),
    ("Annual observations", report["annual_rows"], ""),
    ("Observed peaks", report["observed_peaks"],
     "Products whose series has turned down and stayed down."),
    ("Peaks not yet observed", report["not_yet_observed_peaks"],
     "Still at their highest value; no peak claimed."),
    ("Evidenced exclusions", report["excluded_products"], ""),
    ("Distinct sources cited", len(sources), ""),
    ("Products with attributes", report["product_profiles"]["products"], ""),
    ("Competitive intensity assessed", report["product_profiles"]["competitive_intensity_assessed"],
     "Only inside the catalog's own indication universe; elsewhere it is left unassessed."),
]
for label, value, note in STATS:
    read.cell(r, 1, label).font = BODY
    cell = read.cell(r, 2, value)
    cell.font = BOLD
    cell.number_format = PCT if isinstance(value, float) else "#,##0"
    cell.alignment = Alignment(horizontal="left")
    read.cell(r, 3, note).font = BODY
    read.cell(r, 3).alignment = Alignment(vertical="top", wrap_text=True)
    r += 1

drift = [
    (label, stated, counted)
    for label, stated, counted in (
        ("quarterly rows", report["quarterly_rows"], len(quarterly)),
        ("quarterly series", report["complete_quarterly_series"], len(refresh)),
        ("products with attributes", report["product_profiles"]["products"], len(profiles)),
    )
    if stated != counted
]
if drift:
    r += 1
    read.cell(r, 1, "BUILD REPORT IS BEHIND THE DATA").font = BOLD
    r += 1
    read.cell(r, 3, "The manifest and build report on the Build Manifest sheet were written by "
                    "a build that did not produce every row now in the dataset, so their counts "
                    "are lower than a census of the sheets: "
                    + "; ".join(f"{label} {stated} stated vs {counted} counted"
                                for label, stated, counted in drift)
                    + ". The sheets are the data; the report is a record of one build. Anything "
                      "derived from the report - Series Coverage, peaks, competitive intensity - "
                      "covers only the series that build knew about, which Refresh Status "
                      "flags per series.").font = BODY
    read.cell(r, 3).alignment = Alignment(vertical="top", wrap_text=True)
    read.row_dimensions[r].height = 76
    r += 1

r += 1
read.cell(r, 1, "THE ONE RULE WORTH STATING").font = BOLD
r += 1
read.cell(r, 3, "Where an issuer prints regional lines and also states a worldwide figure, the "
                "worldwide figure is what gold records - never the sum of the regions. Issuers "
                "round each line independently, so the parts need not add to the whole they "
                "themselves publish.").font = BODY
read.cell(r, 3).alignment = Alignment(vertical="top", wrap_text=True)
read.row_dimensions[r].height = 46
r += 1

# ------------------------------------------------------- build manifest sheet

mf = wb.create_sheet("Build Manifest")
mf.column_dimensions["A"].width = 40
mf.column_dimensions["B"].width = 100
mf.cell(1, 1, "Build Manifest").font = TITLE
mf.cell(2, 1, "Generated by scripts/build_independent_gold.py. Reproduced verbatim.").font = NOTE
row_index = 4


def dump(prefix, obj, row_index):
    for key, value in obj.items():
        if isinstance(value, dict):
            cell = mf.cell(row_index, 1, f"{prefix}{key}")
            cell.font = BOLD
            row_index += 1
            row_index = dump(f"{prefix}{key}.", value, row_index)
        else:
            mf.cell(row_index, 1, f"{prefix}{key}").font = BODY
            cell = mf.cell(row_index, 2, flat(value))
            cell.font = BODY
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            row_index += 1
    return row_index


mf.cell(row_index, 1, "manifest.json").font = TITLE
row_index += 1
row_index = dump("", manifest, row_index)
row_index += 1
mf.cell(row_index, 1, "build_report.json").font = TITLE
row_index += 1
row_index = dump("", report, row_index)
row_index += 1
mf.cell(row_index, 1, "unresolved_quarters.jsonl").font = BOLD
mf.cell(row_index, 2, f"{len(unresolved)} rows - no quarter in any series is unresolved.").font = BODY

wb.move_sheet("Read Me", offset=-wb.sheetnames.index("Read Me"))
OUT.parent.mkdir(parents=True, exist_ok=True)
wb.save(OUT)
print("wrote", OUT, "sheets:", wb.sheetnames)
