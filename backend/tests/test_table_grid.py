"""A table read as a rectangle keeps what its layout states.

The headings in an SEC exhibit are not decoration: "Three Months Ended" spanning
seven columns and "2016" spanning three of them is the document saying which
figures belong to which period. Reading rows as the cells they happen to contain
throws that away, and the period then has to be guessed from prose.
"""

from bs4 import BeautifulSoup

from app.parsing.documents import html_table_grid, html_tables

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
