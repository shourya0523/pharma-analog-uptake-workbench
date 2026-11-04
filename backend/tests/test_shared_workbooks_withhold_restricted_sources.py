"""Neither exported workbook carries a withheld row, and their formulas evaluated.

Both workbooks withhold gold rows whose source type is restricted
(`scripts/uptake_workbook.py`), apart from the exempt products. A withheld row
can leak in more ways than its own cells: a secondary source on a kept row, a
URL in a series' explanation, an exclusion's evidence. So the check is on every
cell of every sheet, for every withheld URL and quote that no carried row also
cites.

The formulas are evaluated by a LibreOffice round trip when the workbook is
written. A function one engine names differently from the other comes back as
an error string, so any cell holding one fails here.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = REPO / "seed" / "gold"
WORKBOOKS = [REPO / "exports" / "pah_gold_dataset.xlsx", REPO / "exports" / "analog_profiles.xlsx"]
sys.path.insert(0, str(REPO / "scripts"))
from uptake_workbook import is_withheld  # noqa: E402

# Sheets that reproduce gold's own manifest and build report verbatim; they
# hold counts and file names, not rows.
VERBATIM = {"Build Manifest"}
URL = re.compile(r"https?://[^\s)\]|,;'\"]+")
ERRORS = ("#NAME?", "#REF!", "#VALUE!", "#DIV/0!", "#N/A", "#NUM!", "#NULL!", "Err:")


def load_jsonl(name):
    return [json.loads(line) for line in (GOLD / name).read_text().splitlines() if line.strip()]


@pytest.fixture(scope="module")
def withheld_text():
    rows = load_jsonl("quarterly_revenue.jsonl") + load_jsonl("annual_revenue.jsonl") + load_jsonl("companion_series.jsonl")
    carried = set()
    for row in rows:
        if not is_withheld(row):
            carried.add(row.get("source_url"))
            carried.add(row.get("source_quote"))
    text = set()
    for row in rows:
        if is_withheld(row):
            text.add(row.get("source_url"))
            text.add(row.get("source_quote"))
    text = {item for item in text - carried if item and len(item) > 20}
    assert text, "nothing is withheld, so this check cannot fail"
    return text


@pytest.fixture(scope="module", params=WORKBOOKS, ids=lambda path: path.name)
def cells(request):
    openpyxl = pytest.importorskip("openpyxl")
    assert request.param.exists(), f"{request.param.name} is missing; regenerate it"
    book = openpyxl.load_workbook(request.param, read_only=True, data_only=True)
    found = {}
    for ws in book.worksheets:
        found[ws.title] = [value for row in ws.iter_rows(values_only=True) for value in row
                           if isinstance(value, str)]
    book.close()
    return found


def test_no_cell_holds_a_withheld_url_or_quote(cells, withheld_text):
    """A withheld URL or quote, as a whole cell or as a URL inside prose."""
    leaks = {}
    for title, values in cells.items():
        if title in VERBATIM:
            continue
        tokens = set(values) | {url.rstrip(".") for value in values for url in URL.findall(value)}
        hits = sorted(tokens & withheld_text)
        if hits:
            leaks[title] = [hit[:80] for hit in hits[:3]]
    assert not leaks, f"withheld sources appear in: {leaks}"


def test_every_formula_evaluated(cells):
    broken = {title: [v for v in values if v.startswith(ERRORS)][:3] for title, values in cells.items()}
    assert not {title: v for title, v in broken.items() if v}, broken


def test_withheld_quarters_are_marked_not_dropped(cells):
    matrix = "Quarterly Matrix" if "Quarterly Matrix" in cells else "Revenue Matrix"
    assert "withheld" in cells[matrix], f"{matrix} shows no withheld quarter"


def test_the_two_workbooks_agree_on_every_peak_status():
    openpyxl = pytest.importorskip("openpyxl")
    statuses = []
    for path in WORKBOOKS:
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        rows = list(book["Peak Benchmarks"].iter_rows(values_only=True))
        book.close()
        header = rows[1]
        statuses.append({row[header.index("benchmark_identity")]: row[header.index("peak_status")]
                         for row in rows[2:] if row and row[0] is not None})
    gold_book, analog_book = statuses
    shared = set(gold_book) & set(analog_book)
    assert shared, "the workbooks share no series, so this check cannot fail"
    assert {k: gold_book[k] for k in shared} == {k: analog_book[k] for k in shared}
