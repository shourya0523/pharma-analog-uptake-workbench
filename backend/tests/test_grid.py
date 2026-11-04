"""Tables as labelled grids, and the earnings reader choosing a cell."""

from __future__ import annotations

import datetime as dt

from app.retrieval.earnings import check_cell, read_exhibit
from app.retrieval.grid import cells

RELEASE = b"""<html><body>
<div>ACME PHARMA - REVENUES</div><div>SECOND-QUARTER 2025 and 2024</div><div>(millions of dollars)</div>
<table>
<tr><td></td><td colspan="3">WORLDWIDE</td><td colspan="3">UNITED STATES</td></tr>
<tr><td></td><td>2025</td><td>2024</td><td>% Change</td><td>2025</td><td>2024</td><td>% Change</td></tr>
<tr><td>Calderon</td><td>411</td><td>441</td><td>(7%)</td><td>42</td><td>39</td><td>6%</td></tr>
<tr><td>NuVessa</td><td>90</td><td>70</td><td>29%</td><td>9</td><td>7</td><td>29%</td></tr>
</table>
<div>ACME PHARMA - REVENUES</div><div>SIX MONTHS 2025 and 2024</div>
<table>
<tr><td></td><td colspan="2">WORLDWIDE</td></tr>
<tr><td></td><td>2025</td><td>2024</td></tr>
<tr><td>Calderon</td><td>820</td><td>870</td></tr>
</table>
<table>
<tr><td></td><td>Q2 2025</td></tr>
<tr><td>Calderon</td><td></td></tr>
<tr><td>U.S.</td><td>30</td></tr>
<tr><td>International</td><td>12</td></tr>
</table>
</body></html>"""
END = dt.date(2025, 6, 30)


def _by_label(found):
    return {(c.row, c.column): c for c in found}


def test_each_figure_keeps_its_heading_row_and_column_path():
    found = cells(RELEASE, "Calderon")
    quarter = [c for c in found if c.table == 0]
    assert {(c.column, c.text) for c in quarter} >= {(("WORLDWIDE", "2025"), "411"), (("UNITED STATES", "2025"), "42")}
    assert all("SECOND-QUARTER 2025 and 2024" in c.context for c in quarter)
    half = [c for c in found if c.table == 1]
    assert half[0].context[-1] == "SIX MONTHS 2025 and 2024" and half[0].text == "820"
    subrows = [c for c in found if c.table == 2]
    assert [(c.row, c.text) for c in subrows] == [(("Calderon", "U.S."), "30"), (("Calderon", "International"), "12")]
    assert not any(c.row == ("NuVessa",) for c in found)


def test_check_cell_reads_the_period_from_the_cells_own_labels():
    found = cells(RELEASE, "Calderon")
    choice = {"kind": "product_sales", "unit": "millions", "currency": "USD"}
    ww_2025 = next(c for c in found if c.table == 0 and c.column == ("WORLDWIDE", "2025"))
    ww_2024 = next(c for c in found if c.table == 0 and c.column == ("WORLDWIDE", "2024"))
    six = next(c for c in found if c.table == 1 and c.column == ("WORLDWIDE", "2025"))
    assert check_cell(choice, ww_2025, "", END)[0] == "accepted"
    assert check_cell(choice, ww_2024, "", END)[0] == "wrong_period"
    assert check_cell(choice, six, "", END)[0] == "wrong_period"
    assert check_cell({**choice, "kind": "royalty"}, ww_2025, "", END)[0] == "not_own_sales"
    assert check_cell({**choice, "unit": "thousands"}, ww_2025, "", END)[0] == "unit_conflict"


def test_read_exhibit_takes_the_chosen_cell_and_falls_back_to_text_when_none():
    from sec_fixtures import Response

    def get(url, **params):
        return Response(RELEASE)

    def pick(prompt):
        cid = next(line.split(":")[0] for line in prompt.splitlines()
                   if "SECOND-QUARTER" in line and "WORLDWIDE > 2025 = 411" in line)
        return {"cell": cid, "kind": "product_sales", "unit": "millions", "currency": "USD"}

    exhibit = {"filer": "ACME PHARMA (CIK 1)", "cik": "1", "url": "https://example.com/ex99.htm"}
    reading = read_exhibit(exhibit, "Calderon", END, "Q2 2025", get=get, ask=pick)
    assert (reading.status, reading.method, reading.value) == ("accepted", "grid", 411e6)
    assert "SECOND-QUARTER 2025" in reading.cell

    none = read_exhibit(exhibit, "Calderon", END, "Q2 2025", get=get, ask=lambda prompt: {"cell": None, "found": False})
    assert none.method == "text" and none.status == "not_found"
