"""What an earnings-release revenue table says about its own rows.

The rows below are the parsed form of the revenue table in United Therapeutics'
uthrq12024-ex991.htm exhibit, cited so the shapes here can be checked against
the document itself.
"""

from app.parsing.tables import cell_figure, clean_label, sibling_row_labels

# Verbatim output of DocumentParser for the Q1 2024 earnings exhibit revenue table
Q1_2024_TABLE = [
    ["", "", "", "", "", "", "", ""],
    ["", "Three Months Ended March 31,", "", "Dollar Change", "", "Percentage Change"],
    ["", "2024", "", "2023", "", ""],
    ["Net product sales:", "", "", "", "", "", "", ""],
    ["Tyvaso DPI ®(1)", "$", "227.5", "", "", "$", "118.7", "", "", "$", "108.8", "", "", "92", "%"],
    ["Nebulized Tyvaso ®(1)", "145.0", "", "", "119.7", "", "", "25.3", "", "", "21", "%"],
    ["Total Tyvaso", "372.5", "", "", "238.4", "", "", "134.1", "", "", "56", "%"],
    ["Remodulin ®(2)", "128.0", "", "", "121.4", "", "", "6.6", "", "", "5", "%"],
    ["Adcirca ®", "6.4", "", "", "7.3", "", "", "(0.9)", "", "", "(12)", "%"],
    ["Total revenues", "$", "677.7", "", "", "$", "506.9", "", "", "$", "170.8", "", "", "34", "%"],
]


def test_clean_label_strips_trademarks_and_footnotes():
    assert clean_label("Tyvaso DPI ®(1)") == "Tyvaso DPI"
    assert clean_label("Nebulized Tyvaso ®(1)") == "Nebulized Tyvaso"
    assert clean_label("Total Tyvaso") == "Total Tyvaso"
    assert clean_label("Net product sales:") == "Net product sales"


def test_a_cell_reports_a_number_a_nothing_or_neither():
    """Both answers, and the third: a dash is a reported nothing, a year is a
    heading, and a figure comes back as its value rather than as a yes."""
    assert cell_figure("1,234") == 1234.0
    assert cell_figure("$ (2.5)") == -2.5
    assert cell_figure("\u2014") == 0.0
    assert cell_figure("2024") is None
    assert cell_figure("Calderon") is None
    assert cell_figure(None) is None


def test_a_schedules_own_rows_are_what_it_reports_beside_the_product():
    """The surviving half: which tables are schedules, and what they list.

    A table with a period heading and year columns is a schedule of figures by
    period; the rows of the one our product appears in are the filer's own
    list of what it reports beside it. A table with neither is a list of
    something else and says nothing about which products the issuer sells.
    """
    labels = sibling_row_labels([Q1_2024_TABLE], product="Tyvaso", generic="treprostinil")
    assert "Remodulin" in labels and "Adcirca" in labels
    assert "Total revenues" in labels

    orphan = [["Total Tyvaso", "372.5", "238.4"]]
    assert sibling_row_labels([orphan], product="Tyvaso") == []
    assert sibling_row_labels(None, product="Tyvaso") == []
