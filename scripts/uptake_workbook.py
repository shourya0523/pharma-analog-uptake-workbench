"""The revenue, peak and uptake sheets both exported workbooks share, written as Excel formulas.

Two things live here so the two exporters cannot drift apart:

* **Which gold rows a shared workbook may carry.** A row whose ``source_type``
  is in ``RESTRICTED_SOURCE_TYPES`` is withheld unless its product is in
  ``EXEMPT_PRODUCTS``. A withheld quarter is not dropped silently: its cell in
  the quarterly matrix reads ``WITHHELD``, so a formula can tell a quarter that
  was reported but cannot be shown from one that was never reported.
* **The peak and uptake arithmetic, as formulas over the cells the workbook
  holds.** Nothing derived from a withheld quarter is written as a value.

The peak of a series is its highest rolling four-quarter total; of equal totals,
the earliest. Its status, in the order the checks apply:

    not judged                      gold's launch-gap review found too much of
                                    the launch missing, or launch quarters are
                                    missing and no lower window precedes the high
    not yet peaked                  no complete window, an unreported quarter
                                    beside the high, or a live series still short
                                    of the confirming quarters
    peak unknown - restricted data  a withheld quarter falls in, just before, or
                                    after the highest window
    peak observed                   at least the Settings number of later totals
    line ended at high              the series stopped before that many followed

The benchmark is the observed high for a product whose peak is observed, and
otherwise the median of the comparable published estimates where that median
is above the observed high. Uptake is a quarter's revenue over a quarter of the
benchmark, limited to 0-100%, and held at 100% from the last quarter of the
peak window on when the product's peak is observed and Settings says so.

openpyxl writes formulas without their values, so ``recalculate`` opens the
saved file in headless LibreOffice and saves it back with the values stored.
Only functions both Excel and LibreOffice evaluate without a prefix are used
(IF, AND, OR, NOT, INDEX, MATCH, MAX, MIN, COUNT, COUNTIF, SUM, ISNUMBER,
ISBLANK).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# The source types a shared workbook may not carry. gold's quarterly and
# annual rows name two source types, `sec_filing` and `company_ir`; a new one
# is carried until it is added here.
RESTRICTED_SOURCE_TYPES = frozenset({"company_ir"})
# Products whose restricted rows are kept anyway: an explicit decision about
# these three, not a property of the data, so no file produces the list.
EXEMPT_PRODUCTS = frozenset({"Opsumit", "Tracleer", "Uptravi"})
# The host every non-restricted revenue source lives on. A citation without a
# source_type (an exclusion's evidence, a secondary source) is judged by it.
FILING_HOST = "www.sec.gov"
WITHHELD = "withheld"
# Free-text fields on a revenue row that can cite a further source by URL.
NOTE_FIELDS = ("gold_notes", "why_separate", "continuation_evidence")

FONT = "Arial"
INK = "1F3864"
HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor=INK)
BODY = Font(name=FONT, size=10)
NOTE = Font(name=FONT, size=9, italic=True, color="5B6B7F")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
WITHHELD_FILL = PatternFill("solid", fgColor="D9D9D9")
WITHHELD_FONT = Font(name=FONT, size=9, italic=True, color="7F7F7F")
THIN = Side(style="thin", color="D6DCE4")
GRID = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MONEY = '#,##0.0;(#,##0.0);-'
SHARE = "0.0%"

PEAK_OBSERVED = "peak observed"
NOT_YET = "not yet peaked"
LINE_ENDED = "line ended at high"
RESTRICTED = "peak unknown - restricted data"
NOT_JUDGED = "not judged"
STATUSES = (PEAK_OBSERVED, NOT_YET, LINE_ENDED, RESTRICTED, NOT_JUDGED)

# gold's launch-gap judgement that leaves a series unjudged whatever its revenue.
LAUNCH_HISTORY_MISSING = "launch_history_missing"


# ------------------------------------------------------------------ rows


def is_withheld(row: dict) -> bool:
    """Whether a gold revenue row is kept out of a shared workbook."""
    return row.get("source_type") in RESTRICTED_SOURCE_TYPES and row["drug_name"] not in EXEMPT_PRODUCTS


def is_filing_url(url: str | None) -> bool:
    return bool(url) and urlparse(url).netloc == FILING_HOST


def split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """``(kept, withheld)``. Kept rows lose any secondary source that is not a filing."""
    kept, withheld = [], []
    for row in rows:
        if is_withheld(row):
            withheld.append(row)
            continue
        if row.get("sources") and row["drug_name"] not in EXEMPT_PRODUCTS:
            row = dict(row, sources=[s for s in row["sources"]
                                     if s.get("source_url") == row.get("source_url")
                                     or is_filing_url(s.get("source_url"))])
        kept.append(scrub_prose(row, NOTE_FIELDS))
    return kept, withheld


def scrub_citation(row: dict) -> dict:
    """A row whose only source is a citation: blank it when the citation is not a filing."""
    if row["drug_name"] in EXEMPT_PRODUCTS or is_filing_url(row.get("source_url")) or not row.get("source_url"):
        return row
    return dict(row, source_url=None, source_quote=None,
                citation_withheld="cited a source this workbook does not carry")


URL = re.compile(r"https?://[^\s)\]|,;'\"]+")


def scrub_prose(row: dict, fields: tuple[str, ...]) -> dict:
    """A row whose free-text fields cite sources: mask each cited URL that is not a filing."""
    if row["drug_name"] in EXEMPT_PRODUCTS:
        return row
    masked = {field: URL.sub(lambda m: m.group(0) if is_filing_url(m.group(0)) else "(source withheld)",
                             row[field])
              for field in fields if isinstance(row.get(field), str)}
    return dict(row, **masked)


# ---------------------------------------------------------------- sheets


def ref(sheet: str, cell: str) -> str:
    return f"'{sheet}'!{cell}"


def head(ws, row: int, columns: list[str]) -> None:
    for index, name in enumerate(columns, start=1):
        cell = ws.cell(row, index, name)
        cell.font = HEAD
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
        cell.border = GRID
    ws.row_dimensions[row].height = 30


def write_settings(wb, as_of_quarter: str, *, title: str = "Settings") -> dict[str, str]:
    """The inputs every peak and uptake formula reads. Returns their absolute references."""
    ws = wb.create_sheet(title)
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 90
    ws.cell(1, 1, "Settings").font = Font(name=FONT, size=14, bold=True, color=INK)
    ws.cell(2, 1, "Yellow cells are inputs. Change one and every peak status, benchmark and uptake "
                  "curve in the workbook recalculates.").font = NOTE
    rows = [
        ("decline", "Confirming quarters", 2,
         "How many rolling four-quarter totals must follow the highest one before it counts as the "
         "peak. None of them can be higher, because the peak is the highest."),
        ("flat", "Flat after peak", "Yes",
         "Yes holds an uptake curve at 100% from the last quarter of the peak window on, for products "
         "whose peak is observed. No shows the reported decline."),
        ("as_of", "As-of quarter", as_of_quarter,
         "A series whose last quarter is earlier than this has stopped being reported; its high is "
         "'line ended at high' rather than 'not yet peaked'."),
    ]
    refs = {}
    for offset, (key, label, value, note) in enumerate(rows):
        r = 4 + offset
        ws.cell(r, 1, label).font = BODY
        cell = ws.cell(r, 2, value)
        cell.font = Font(name=FONT, size=10, bold=True)
        cell.fill = INPUT_FILL
        cell.border = GRID
        n = ws.cell(r, 3, note)
        n.font = BODY
        n.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[r].height = 30
        refs[key] = f"'{title}'!$B${r}"
    return refs


class Matrix:
    """Where a series-by-quarter sheet put things, so other sheets can refer to it."""

    def __init__(self, sheet: str, lead: int, periods: list[str], first_row: int, rows: int):
        self.sheet, self.lead, self.periods = sheet, lead, periods
        self.first_row, self.rows = first_row, rows
        self.first_col = get_column_letter(lead + 1)
        self.last_col = get_column_letter(lead + len(periods))

    def col(self, period: str) -> str:
        return get_column_letter(self.lead + 1 + self.periods.index(period))

    def row_range(self, r: int) -> str:
        return f"'{self.sheet}'!${self.first_col}{r}:${self.last_col}{r}"

    def first(self, r: int) -> str:
        return f"'{self.sheet}'!${self.first_col}{r}"

    def last(self, r: int) -> str:
        return f"'{self.sheet}'!${self.last_col}{r}"

    def header(self) -> str:
        return f"'{self.sheet}'!${self.first_col}$2:${self.last_col}$2"


def write_quarterly_matrix(wb, title: str, series: list[dict], lead: list[str], periods: list[str],
                           values: dict, withheld: set, note: str) -> Matrix:
    """Series by calendar quarter, USD millions. A withheld quarter reads ``WITHHELD``."""
    ws = wb.create_sheet(title)
    ws.cell(1, 1, note).font = NOTE
    head(ws, 2, lead + periods)
    for offset, item in enumerate(series):
        r = 3 + offset
        for index, column in enumerate(lead, start=1):
            cell = ws.cell(r, index, item.get(column))
            cell.font = BODY
            cell.border = GRID
        identity = item["benchmark_identity"]
        for index, period in enumerate(periods, start=len(lead) + 1):
            key = (identity, period)
            if key in values:
                cell = ws.cell(r, index, values[key])
                cell.number_format = MONEY
                cell.font = BODY
            elif key in withheld:
                cell = ws.cell(r, index, WITHHELD)
                cell.fill = WITHHELD_FILL
                cell.font = WITHHELD_FONT
    widths(ws, lead, len(periods))
    return Matrix(title, len(lead), periods, 3, len(series))


def write_rolling_matrix(wb, title: str, series: list[dict], lead: list[str], quarterly: Matrix,
                         note: str) -> Matrix:
    """Rolling four-quarter totals as formulas over the quarterly matrix.

    A window with any quarter that is not a number - unreported or withheld -
    is blank, never a short sum.
    """
    ws = wb.create_sheet(title)
    ws.cell(1, 1, note).font = NOTE
    head(ws, 2, lead + quarterly.periods)
    for offset, item in enumerate(series):
        r = 3 + offset
        for index, column in enumerate(lead, start=1):
            cell = ws.cell(r, index, item.get(column))
            cell.font = BODY
            cell.border = GRID
        for position in range(3, len(quarterly.periods)):
            start = get_column_letter(quarterly.lead + 1 + position - 3)
            end = get_column_letter(quarterly.lead + 1 + position)
            window = f"'{quarterly.sheet}'!{start}{r}:{end}{r}"
            cell = ws.cell(r, quarterly.lead + 1 + position, f'=IF(COUNT({window})=4,SUM({window}),"")')
            cell.number_format = MONEY
            cell.font = BODY
    widths(ws, lead, len(quarterly.periods))
    return Matrix(title, len(lead), quarterly.periods, 3, len(series))


PEAK_LEAD = ["drug_name", "benchmark_identity", "revenue_scope", "geography", "series_end_quarter",
             "launch_anchor_quarter", "alignment_basis", "launch_gap_quarters", "gold_launch_review",
             "consensus_peak_usd_m", "consensus_estimate_count", "consensus_sources", "estimates_set_aside"]
PEAK_FORMULAS = ["observed_peak_4q_usd_m", "peak_position", "peak_window_end", "quarters_after_peak",
                 "withheld_at_or_after_high", "unreported_beside_high", "lower_window_before",
                 "launch_quarters_missing", "series_live", "peak_status", "selected_peak_usd_m",
                 "peak_basis", "quarterly_benchmark_usd_m"]


class Peaks:
    """Where the peak sheet put each column, keyed by column name."""

    def __init__(self, sheet: str, columns: list[str], first_row: int, rows: int):
        self.sheet, self.first_row, self.rows = sheet, first_row, rows
        self.letters = {name: get_column_letter(i) for i, name in enumerate(columns, start=1)}

    def cell(self, name: str, r: int) -> str:
        return f"'{self.sheet}'!${self.letters[name]}{r}"

    def column(self, name: str) -> str:
        letter = self.letters[name]
        return f"'{self.sheet}'!${letter}${self.first_row}:${letter}${self.first_row + self.rows - 1}"


def write_peaks(wb, title: str, series: list[dict], quarterly: Matrix, rolling: Matrix,
                settings: dict[str, str], note: str) -> Peaks:
    """One row per series, in the matrices' row order: inputs as values, the peak as formulas."""
    ws = wb.create_sheet(title)
    ws.cell(1, 1, note).font = NOTE
    columns = PEAK_LEAD + PEAK_FORMULAS
    head(ws, 2, columns)
    L = {name: get_column_letter(i) for i, name in enumerate(columns, start=1)}
    n = len(quarterly.periods)
    for offset, item in enumerate(series):
        r = 3 + offset
        for index, column in enumerate(PEAK_LEAD, start=1):
            cell = ws.cell(r, index, item.get(column))
            cell.font = BODY
            cell.border = GRID
            if column == "consensus_peak_usd_m":
                cell.number_format = MONEY
        c = {name: f"{L[name]}{r}" for name in columns}
        R, Q = rolling.row_range(r), quarterly.row_range(r)
        pos = c["peak_position"]
        formulas = {
            "observed_peak_4q_usd_m": f'=IF(COUNT({R})=0,"",MAX({R}))',
            "peak_position": f'=IF({c["observed_peak_4q_usd_m"]}="","",MATCH({c["observed_peak_4q_usd_m"]},{R},0))',
            "peak_window_end": f'=IF({pos}="","",INDEX({rolling.header()},{pos}))',
            "quarters_after_peak": f'=IF({pos}="","",COUNT(INDEX({R},{pos}):{rolling.last(r)})-1)',
            # From the quarter just before the window to the end of the series.
            "withheld_at_or_after_high":
                f'=IF({pos}="","",COUNTIF(INDEX({Q},MAX(1,{pos}-4)):{quarterly.last(r)},"{WITHHELD}")>0)',
            # An empty cell beside the window with reported quarters beyond it.
            "unreported_beside_high":
                f'=IF({pos}="","",OR('
                f'AND({pos}-4>=1,ISBLANK(INDEX({Q},MAX(1,{pos}-4))),'
                f'COUNT({quarterly.first(r)}:INDEX({Q},MAX(1,{pos}-4)))>0),'
                f'AND({pos}<{n},ISBLANK(INDEX({Q},MIN({n},{pos}+1))),'
                f'COUNT(INDEX({Q},MIN({n},{pos}+1)):{quarterly.last(r)})>0)))',
            "lower_window_before":
                f'=IF({pos}="","",IF({pos}=1,FALSE,'
                f'COUNTIF({rolling.first(r)}:INDEX({R},MAX(1,{pos}-1)),"<"&{c["observed_peak_4q_usd_m"]})>0))',
            # Launch quarters are missing when gold says so, or when quarters
            # before the window are withheld.
            "launch_quarters_missing":
                f'=IF({pos}="","",OR(N({c["launch_gap_quarters"]})>0,'
                f'COUNTIF({quarterly.first(r)}:INDEX({Q},MAX(1,{pos}-4)),"{WITHHELD}")>0))',
            "series_live": f'={c["series_end_quarter"]}={settings["as_of"]}',
            "peak_status":
                f'=IF({c["gold_launch_review"]}="{LAUNCH_HISTORY_MISSING}","{NOT_JUDGED}",'
                f'IF({c["observed_peak_4q_usd_m"]}="",IF(COUNTIF({Q},"{WITHHELD}")>0,"{RESTRICTED}","{NOT_YET}"),'
                f'IF({c["withheld_at_or_after_high"]},"{RESTRICTED}",'
                f'IF({c["unreported_beside_high"]},"{NOT_YET}",'
                f'IF(AND({c["launch_quarters_missing"]},NOT({c["lower_window_before"]})),"{NOT_JUDGED}",'
                f'IF({c["quarters_after_peak"]}>={settings["decline"]},"{PEAK_OBSERVED}",'
                f'IF({c["series_live"]},"{NOT_YET}","{LINE_ENDED}")))))))',
            "selected_peak_usd_m":
                f'=IF({c["peak_status"]}="{PEAK_OBSERVED}",{c["observed_peak_4q_usd_m"]},'
                f'IF(AND(ISNUMBER({c["consensus_peak_usd_m"]}),'
                f'OR({c["observed_peak_4q_usd_m"]}="",N({c["consensus_peak_usd_m"]})>N({c["observed_peak_4q_usd_m"]}))),'
                f'{c["consensus_peak_usd_m"]},{c["observed_peak_4q_usd_m"]}))',
            "peak_basis":
                f'=IF({c["selected_peak_usd_m"]}="","",'
                f'IF(AND({c["peak_status"]}<>"{PEAK_OBSERVED}",ISNUMBER({c["consensus_peak_usd_m"]}),'
                f'{c["selected_peak_usd_m"]}={c["consensus_peak_usd_m"]}),"consensus estimate","observed rolling 4Q high"))',
            "quarterly_benchmark_usd_m": f'=IF({c["selected_peak_usd_m"]}="","",{c["selected_peak_usd_m"]}/4)',
        }
        for name, formula in formulas.items():
            cell = ws[c[name]]
            cell.value = formula
            cell.font = BODY
            cell.border = GRID
            if name.endswith("_usd_m"):
                cell.number_format = MONEY
    for name, letter in L.items():
        ws.column_dimensions[letter].width = {"drug_name": 20, "benchmark_identity": 32, "consensus_sources": 40,
                                              "estimates_set_aside": 36, "alignment_basis": 28,
                                              "peak_status": 26, "peak_basis": 22}.get(name, 15)
    ws.freeze_panes = "C3"
    ws.auto_filter.ref = f"A2:{get_column_letter(len(columns))}{2 + len(series)}"
    return Peaks(title, columns, 3, len(series))


def launch_label(offset: int) -> str:
    return f"Year {offset // 4 + 1} Q{offset % 4 + 1}"


def quarter_index(period: str) -> int:
    return int(period[:4]) * 4 + int(period[-1]) - 1


def write_uptake(wb, title: str, series: list[dict], lead: list[str], quarterly: Matrix, peaks: Peaks,
                 settings: dict[str, str], note: str, *, row_of: dict[str, int] | None = None) -> int:
    """Each quarter from launch as a share of a quarter of the benchmark, as formulas.

    ``series`` rows carry ``launch_anchor_quarter``, ``first_period`` and
    ``last_period``. ``row_of`` maps a benchmark_identity to its row in the
    matrices and peak sheet when this sheet lists a different set or order of
    series; by default the rows line up. Returns the number of curves written.
    """
    ws = wb.create_sheet(title)
    ws.cell(1, 1, note).font = NOTE
    span = max((quarter_index(s["last_period"]) - quarter_index(s["launch_anchor_quarter"]) + 1
                for s in series), default=0)
    head(ws, 2, lead + [launch_label(i) for i in range(span)])
    for offset, item in enumerate(series):
        r = 3 + offset
        src = row_of[item["benchmark_identity"]] if row_of else r
        for index, column in enumerate(lead, start=1):
            cell = ws.cell(r, index, item.get(column))
            cell.font = BODY
            cell.border = GRID
        anchor = quarter_index(item["launch_anchor_quarter"])
        status, bench, pos = (peaks.cell("peak_status", src), peaks.cell("quarterly_benchmark_usd_m", src),
                              peaks.cell("peak_position", src))
        for index in range(quarter_index(item["first_period"]), quarter_index(item["last_period"]) + 1):
            period = f"{index // 4}Q{index % 4 + 1}"
            offset_from_launch = index - anchor
            if offset_from_launch < 0 or period not in quarterly.periods:
                continue
            q = f"'{quarterly.sheet}'!{quarterly.col(period)}{src}"
            position = quarterly.periods.index(period) + 1
            formula = (f'=IF(OR(NOT(ISNUMBER({q})),NOT(ISNUMBER({bench}))),"",'
                       f'IF(AND({settings["flat"]}="Yes",{status}="{PEAK_OBSERVED}",{position}>={pos}),1,'
                       f'MAX(0,MIN(1,{q}/{bench}))))')
            cell = ws.cell(r, len(lead) + 1 + offset_from_launch, formula)
            cell.number_format = SHARE
            cell.font = BODY
    widths(ws, lead, span)
    return len(series)


def widths(ws, lead: list[str], count: int) -> None:
    for index, name in enumerate(lead, start=1):
        ws.column_dimensions[get_column_letter(index)].width = {
            "drug_name": 20, "benchmark_identity": 32, "revenue_scope": 22, "alignment_basis": 30,
        }.get(name, 15)
    for index in range(len(lead) + 1, len(lead) + 1 + count):
        ws.column_dimensions[get_column_letter(index)].width = 10
    ws.freeze_panes = ws.cell(3, len(lead) + 1)


def write_annual_peaks(wb, title: str, products: list[dict], years: list[str], values: dict, withheld: set,
                       settings: dict[str, str], note: str) -> Peaks:
    """Products whose benchmark is annual: highest calendar year, then the confirming lower years.

    Years are columns. A withheld year reads ``WITHHELD`` and, at or after the
    highest year, leaves the peak unknown, as in the quarterly sheet.
    """
    ws = wb.create_sheet(title)
    ws.cell(1, 1, note).font = NOTE
    lead = ["drug_name", "revenue_scope", "geography"]
    tail = ["highest_year_usd_m", "highest_position", "peak_year", "years_after_peak",
            "withheld_at_or_after_high", "peak_status"]
    head(ws, 2, lead + years + tail)
    first, last = get_column_letter(len(lead) + 1), get_column_letter(len(lead) + len(years))
    T = {name: get_column_letter(len(lead) + len(years) + 1 + i) for i, name in enumerate(tail)}
    for offset, item in enumerate(products):
        r = 3 + offset
        for index, column in enumerate(lead, start=1):
            cell = ws.cell(r, index, item.get(column))
            cell.font = BODY
            cell.border = GRID
        for index, year in enumerate(years, start=len(lead) + 1):
            key = (item["drug_name"], year)
            if key in values:
                cell = ws.cell(r, index, values[key])
                cell.number_format = MONEY
                cell.font = BODY
            elif key in withheld:
                cell = ws.cell(r, index, WITHHELD)
                cell.fill = WITHHELD_FILL
                cell.font = WITHHELD_FONT
        row = f"${first}{r}:${last}{r}"
        c = {name: f"{T[name]}{r}" for name in tail}
        pos = c["highest_position"]
        formulas = {
            "highest_year_usd_m": f'=IF(COUNT({row})=0,"",MAX({row}))',
            "highest_position": f'=IF({c["highest_year_usd_m"]}="","",MATCH({c["highest_year_usd_m"]},{row},0))',
            "peak_year": f'=IF({pos}="","",INDEX(${first}$2:${last}$2,{pos}))',
            "years_after_peak": f'=IF({pos}="","",COUNT(INDEX({row},{pos}):${last}{r})-1)',
            "withheld_at_or_after_high":
                f'=IF({pos}="","",COUNTIF(INDEX({row},MAX(1,{pos}-1)):${last}{r},"{WITHHELD}")>0)',
            "peak_status":
                f'=IF({c["highest_year_usd_m"]}="",IF(COUNTIF({row},"{WITHHELD}")>0,"{RESTRICTED}","{NOT_YET}"),IF({c["withheld_at_or_after_high"]},"{RESTRICTED}",'
                f'IF(AND({c["years_after_peak"]}>=2,COUNTIF(INDEX({row},MIN({len(years)},{pos}+1)):${last}{r},'
                f'">="&{c["highest_year_usd_m"]})=0),"{PEAK_OBSERVED}","{NOT_YET}")))',
        }
        for name, formula in formulas.items():
            cell = ws[c[name]]
            cell.value = formula
            cell.font = BODY
            cell.border = GRID
            if name.endswith("_usd_m"):
                cell.number_format = MONEY
    widths(ws, lead, len(years) + len(tail))
    return Peaks(title, lead + years + tail, 3, len(products))


# ------------------------------------------------------------ recalculation


def recalculate(path: Path) -> None:
    """Store every formula's value in the file, by a headless LibreOffice round trip.

    Raises when LibreOffice is missing or fails, rather than leaving a workbook
    whose formulas read back blank.
    """
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        raise SystemExit("LibreOffice (soffice) is needed to store formula values in the workbook")
    with tempfile.TemporaryDirectory() as work:
        profile = Path(work) / "profile"
        out = Path(work) / "out"
        subprocess.run([soffice, f"-env:UserInstallation=file://{profile}", "--headless", "--calc",
                        "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir", str(out), str(path)],
                       check=True, capture_output=True, timeout=1800)
        produced = out / path.name
        if not produced.exists():
            raise SystemExit(f"LibreOffice did not write {produced}")
        shutil.move(str(produced), str(path))
