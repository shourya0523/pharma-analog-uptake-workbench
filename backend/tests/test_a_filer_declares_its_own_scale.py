"""A table's unit, where the filer tagged it on the figures themselves.

A schedule states its unit in the sentence above it - "(in thousands)" - and
the fingerprint refuses a table that declares none, because a missing unit is
the condition that produced figures wrong by a factor of a thousand.

Some filings leave nothing for a reader of text to find: the caption is a run
of zero-width spaces, the header cells carry no unit word, and the table is
refused - while every figure in it is wrapped in an inline-XBRL tag whose
`scale` says exactly what the numbers mean. That is the filer's own
declaration, attached to the number rather than to the prose near it.

Invented names: Calderon, NuVessa.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from app.extraction.fingerprint import build_fingerprint, detect_unit
from app.parsing.documents import table_declared_unit

HEADER = [["Three Months Ended March 31,"], ["2025", "2024"]]
ROWS = HEADER + [["Calderon", "$", "159,721", "$", "129,923"]]


def _table(html: str):
    return BeautifulSoup(html, "lxml").find("table")


def test_the_scale_on_a_figure_is_a_declaration_of_its_unit():
    table = _table(
        "<table><tr><td>Calderon</td>"
        "<td><ix:nonFraction scale='3' unitRef='U_USD'>159,721</ix:nonFraction></td>"
        "<td><ix:nonFraction scale='3' unitRef='U_USD'>129,923</ix:nonFraction></td>"
        "</tr></table>"
    )
    assert table_declared_unit(table) == "thousands"


def test_a_table_whose_figures_disagree_has_declared_nothing():
    """Two scales in one table is not one unit, and picking between them is
    the error the fingerprint exists to refuse."""
    table = _table(
        "<table><tr><td>Calderon</td>"
        "<td><ix:nonFraction scale='3'>159,721</ix:nonFraction></td>"
        "<td><ix:nonFraction scale='6'>160</ix:nonFraction></td>"
        "</tr></table>"
    )
    assert table_declared_unit(table) is None
    assert table_declared_unit(_table("<table><tr><td>Calderon</td><td>159,721</td></tr></table>")) is None


def test_the_words_still_win_where_a_table_states_its_unit():
    """The tags answer last. A filing whose caption and whose tags disagree is
    not one to settle here, so the reading that has always been made stands."""
    assert detect_unit([["(in millions)"]], "", "thousands") == ("millions", True)
    assert detect_unit([["Calderon"]], "revenues by product (in millions):", "thousands") == (
        "millions", True
    )


def test_the_tags_answer_where_the_words_say_nothing():
    silent = build_fingerprint(ROWS, "")
    assert not silent.usable and "unit_not_declared" in silent.notes

    tagged = build_fingerprint(ROWS, "", declared_unit="thousands")
    assert tagged.usable, "the filer said what its numbers mean"
    assert tagged.unit_label == "thousands"
    assert "unit_not_declared" not in tagged.notes


def test_a_scale_nobody_recognises_declares_nothing():
    """The refusal is the point: an unknown scale is not an excuse to guess."""
    unknown = build_fingerprint(ROWS, "", declared_unit="furlongs")
    assert not unknown.usable and "unit_not_declared" in unknown.notes
