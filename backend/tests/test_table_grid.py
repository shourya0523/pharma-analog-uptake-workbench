"""A table read as a rectangle keeps what its layout states.

The headings in an SEC exhibit are not decoration: "Three Months Ended" spanning
seven columns and "2016" spanning three of them is the document saying which
figures belong to which period. Reading rows as the cells they happen to contain
throws that away, and the period then has to be guessed from prose.
"""

from app.extraction.fingerprint import column_periods, stated_periods
from app.parsing.documents import html_table_grid, html_tables
from bs4 import BeautifulSoup

# The shape Gilead files: the heading spans every value column, each year spans
# its own three, and the figures themselves are spanned too.
GILEAD_SHAPE = """
<table>
  <tr><td></td><td></td><td colspan="7">Three Months Ended</td></tr>
  <tr><td></td><td></td><td colspan="7">March 31,</td></tr>
  <tr><td></td><td></td><td colspan="3">2016</td><td></td><td colspan="3">2015</td></tr>
  <tr><td>Genvoya - U.S.</td><td></td><td colspan="2">141</td><td></td><td></td>
      <td colspan="2">&#8212;</td><td></td></tr>
  <tr><td>Harvoni - U.S.</td><td></td><td colspan="2">1,407</td><td></td><td></td>
      <td colspan="2">3,016</td><td></td></tr>
</table>
"""


def grid_of(markup: str) -> list[list[str | None]]:
    return html_table_grid(BeautifulSoup(markup, "lxml").find("table"))


def test_ragged_rows_are_why_a_column_index_means_nothing():
    """The flat reading, for contrast: the rows are not the same width."""
    rows = html_tables(BeautifulSoup(GILEAD_SHAPE, "lxml"))[0]
    widths = {len(row) for row in rows}
    assert len(widths) > 1, "expected the flat reading to produce ragged rows"


def test_every_row_is_the_same_width():
    grid = grid_of(GILEAD_SHAPE)
    assert len({len(row) for row in grid}) == 1


def test_a_heading_covers_the_columns_it_spans():
    grid = grid_of(GILEAD_SHAPE)
    # "Three Months Ended" starts at column 2 and continues to the end.
    assert grid[0][2] == "Three Months Ended"
    assert all(cell is None for cell in grid[0][3:9])
    # Each year starts its own run, and the gap between them is a real cell.
    assert grid[2][2] == "2016" and grid[2][3] is None and grid[2][4] is None
    assert grid[2][5] == ""
    assert grid[2][6] == "2015" and grid[2][7] is None


def test_a_spanned_figure_is_reported_once_not_twice():
    """Values span too. Repeating text into covered columns would double them."""
    grid = grid_of(GILEAD_SHAPE)
    harvoni = next(row for row in grid if row[0] == "Harvoni - U.S.")
    assert [cell for cell in harvoni if cell not in (None, "")] == [
        "Harvoni - U.S.", "1,407", "3,016",
    ]


def test_a_figure_sits_under_the_year_that_spans_its_column():
    """The whole point: the column index now carries the same meaning in both rows."""
    grid = grid_of(GILEAD_SHAPE)
    years = grid[2]
    harvoni = next(row for row in grid if row[0] == "Harvoni - U.S.")

    def year_over(column: int) -> str | None:
        while column >= 0:
            if years[column] is not None:
                return years[column] or None
            column -= 1
        return None

    current = harvoni.index("1,407")
    prior = harvoni.index("3,016")
    assert year_over(current) == "2016"
    assert year_over(prior) == "2015"


# A 10-Q states two lengths side by side: the quarter and the year to date. The
# only thing that says which is which is how far each heading reaches.
TEN_Q_SHAPE = """
<table>
  <tr><td></td><td colspan="5">Three Months Ended June 30,</td>
      <td colspan="5">Six Months Ended June 30,</td></tr>
  <tr><td></td><td colspan="2">2024</td><td colspan="2">2023</td><td>% Chg</td>
      <td colspan="2">2024</td><td colspan="2">2023</td><td>% Chg</td></tr>
  <tr><td>Tyvaso</td><td colspan="2">352.0</td><td colspan="2">276.5</td><td>27</td>
      <td colspan="2">679.4</td><td colspan="2">521.9</td><td>30</td></tr>
</table>
"""


def test_each_column_carries_the_period_stated_above_it():
    periods = column_periods(grid_of(GILEAD_SHAPE))
    # Three months ended March 31, under 2016 and under 2015 respectively.
    assert periods[2] == (3, 3, 2016)
    assert periods[3] == (3, 3, 2016)
    assert periods[6] == (3, 3, 2015)
    assert periods[7] == (3, 3, 2015)


def test_a_quarter_and_the_year_to_date_are_told_apart_by_how_far_each_reaches():
    periods = column_periods(grid_of(TEN_Q_SHAPE))
    assert periods[1] == (3, 6, 2024)
    assert periods[3] == (3, 6, 2023)
    assert periods[6] == (6, 6, 2024)
    assert periods[8] == (6, 6, 2023)


def test_a_change_column_names_no_year_so_it_gets_no_period():
    """The guard against booking a percentage as a quarter's revenue."""
    periods = column_periods(grid_of(TEN_Q_SHAPE))
    assert 5 not in periods and 10 not in periods


def test_a_year_standing_alone_is_a_heading_not_a_figure():
    """If the year row read as data the headings would stop one row short."""
    grid = grid_of(GILEAD_SHAPE)
    assert column_periods(grid), "expected the year row to be read as a heading"


def test_a_table_that_opens_with_figures_declares_no_periods():
    grid = grid_of("<table><tr><td>Tyvaso</td><td>352.0</td></tr></table>")
    assert column_periods(grid) == {}


# The same issuer's press release, where the headings span and the body does
# not. On screen the columns line up; in the markup they do not, and the
# heading row's columns are not the columns the figures are written in.
GILEAD_PRESS_RELEASE = """
<table>
  <tr><td colspan="13">PRODUCT SALES SUMMARY</td></tr>
  <tr><td colspan="13">(in millions)</td></tr>
  <tr><td colspan="5">Three Months Ended</td><td colspan="5">Six Months Ended</td></tr>
  <tr><td colspan="5">June 30,</td><td colspan="5">June 30,</td></tr>
  <tr><td colspan="2">2016</td><td colspan="2">2015</td>
      <td colspan="2">2016</td><td colspan="2">2015</td></tr>
  <tr><td>Harvoni &#8211; Japan</td><td>448</td><td>&#8212;</td><td>1,335</td><td>&#8212;</td></tr>
</table>
"""


def test_headings_that_span_over_a_body_that_does_not_state_nothing():
    """Read as geometry this says the label column is 2016 - so it is not geometry.

    Taken at face value it also dates the six-month figure 1,335 as the prior
    year's quarter, which is the kind of wrong number that looks right.
    """
    grid = grid_of(GILEAD_PRESS_RELEASE)
    label_column = next(
        index for index, cell in enumerate(grid[-1]) if cell and "Harvoni" in cell
    )
    _depth, stated = stated_periods(grid)
    assert label_column in stated, "expected the raw geometry to cover the label"
    assert column_periods(grid) == {}
