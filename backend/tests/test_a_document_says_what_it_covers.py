"""What a fetched document turns out to hold, asked of the document.

Retrieval picks filings by form code and item code and nothing downstream ever
asks what the document it got covers, so a run that fetched documents and one
that fetched useful documents look the same. `coverage` is that question. It
changes nothing about what is fetched.

The one thing it must not be is a name test. A product is named in a risk
factor, in a collaboration note, in a table of contents, beside no number at
all; a claim that a document holds a product's revenue, made from the product's
name being in it, is the error this exists to stop. So every case below that
expects `carries` prints a figure, and the case that expects `names_only`
prints the product's name and no figure the reader can reach.

The shapes are spelled with invented names: Calderon and Calderon XR are one
filer's products, NuVessa is another's.
"""

from __future__ import annotations

from app.connectors.coverage import (
    ABSENT,
    ANSWERS,
    CARRIES,
    NAMES_ONLY,
    PARTIAL,
    REFUTES,
    SILENT,
    UNREADABLE,
    coverage,
    record_coverage,
)
from app.domain.models import (
    ParsedDocument,
    ParsingStatus,
    RetrievalStatus,
    RetrievedSource,
    SourceType,
)

# One filer's quarterly schedule, as a grid: a heading row spanning the period,
# a row of years under it, then one row per product.
SCHEDULE = [
    [None, "Three Months Ended March 31,", "Three Months Ended March 31,"],
    [None, "2025", "2024"],
    ["Calderon", "55,881", "19,834"],
    ["Calderon XR", "4,120", "3,880"],
    ["NuVessa", "9,001", "8,600"],
    ["Total product revenue", "68,002", "32,314"],
]

# The same filer a quarter later, so the document's own period moves.
LATER = [
    [None, "Three Months Ended June 30,", "Three Months Ended June 30,"],
    [None, "2025", "2024"],
    ["Calderon", "61,240", "24,110"],
]


def _document(grids, text="For the three months ended March 31, 2025") -> ParsedDocument:
    return ParsedDocument(
        source_id="s1",
        text_blocks=[text],
        table_grids=grids,
        parsing_status=ParsingStatus.SUCCESS,
    )


def test_a_figure_under_a_period_column_is_what_carries():
    result = coverage(_document([SCHEDULE]), aliases=["Calderon"], periods=["2025Q1"])
    assert result.verdict == ANSWERS
    assert result.per_period["2025Q1"] == CARRIES
    assert result.figures == {"2025Q1": 55881.0}


def test_the_year_a_quarter_falls_in_is_asked_about_too():
    """A caller asks in quarters; a 10-K reports in fiscal years.

    The year each asked quarter ends in is asked about as a period of its own,
    so a document that states the year is a document the predicate can read.
    It is not one of the quarters, though, so it cannot make the verdict
    `answers` and it cannot keep the verdict from being `answers` either.
    """
    annual = [
        [None, "Year Ended December 31,", "Year Ended December 31,"],
        [None, "2025", "2024"],
        ["Calderon", "220,140", "180,220"],
    ]
    result = coverage(
        _document([annual], "For the year ended December 31, 2025"),
        aliases=["Calderon"],
        periods=["2025Q1", "2025Q2"],
    )
    assert set(result.per_period) == {"2025Q1", "2025Q2", "2025"}
    assert result.carried == ["2025"]
    assert result.verdict == PARTIAL, "a year is not a quarter that was asked for"

    quarterly = coverage(
        _document([SCHEDULE]), aliases=["Calderon"], periods=["2025Q1", "2024Q1"]
    )
    assert quarterly.verdict == ANSWERS, (
        "both quarters carried is answered, whatever the years do"
    )


def test_the_comparative_column_is_a_period_too():
    result = coverage(
        _document([SCHEDULE]), aliases=["Calderon"], periods=["2025Q1", "2024Q1"]
    )
    assert result.verdict == ANSWERS
    assert result.figures == {"2025Q1": 55881.0, "2024Q1": 19834.0}


def test_a_period_the_document_does_not_reach_is_partial_not_answered():
    result = coverage(
        _document([SCHEDULE]),
        aliases=["Calderon"],
        periods=["2025Q1", "2024Q1", "2023Q1"],
    )
    assert result.verdict == PARTIAL
    assert result.per_period["2023Q1"] == SILENT


def test_a_sibling_product_is_not_this_product():
    """The reader is given the other products the run tracks, so a row that
    names one of them is that one's figure."""
    result = coverage(
        _document([SCHEDULE]),
        aliases=["NuVessa"],
        periods=["2025Q1"],
        products=["Calderon", "Calderon XR"],
    )
    assert result.figures == {"2025Q1": 9001.0}
    absent = coverage(
        _document([SCHEDULE]),
        aliases=["Ventoril"],
        periods=["2025Q1"],
        products=["Calderon", "NuVessa"],
    )
    assert absent.verdict == ABSENT and not absent.names_product


def test_a_row_grouped_schedule_is_read_through_its_heading():
    """The product is a group heading and the figures sit on rows labelled by
    geography, which `read_label` reads the scope of and finds no product on.

    A label with no figures beside it heads the rows below rather than stating
    anything itself, so the rows under it are read as the heading's - the same
    reading `extraction/extract.py` gives them.
    """
    grouped = [
        [None, "Three Months Ended March 31,", "Three Months Ended March 31,"],
        [None, "2025", "2024"],
        ["Calderon", None, None],
        ["U.S.", "299", "244"],
        ["Int'l", "159", "148"],
        ["Worldwide", "458", "392"],
    ]
    result = coverage(_document([grouped]), aliases=["Calderon"], periods=["2025Q1"])
    assert result.per_period["2025Q1"] == CARRIES
    assert result.names_product


def test_a_product_named_with_no_reachable_figure_is_names_only():
    """A table that names the product beside no figure anyone can reach.

    Saying `names_only` here is the point. A predicate that said `carries` from
    the name alone would hide exactly the gap it exists to show.
    """
    note = [
        ["Collaboration", "Territory"],
        ["Calderon", "United States"],
    ]
    document = _document(
        [note],
        "Calderon is licensed to the collaboration described in Note 12. "
        "For the three months ended March 31, 2025",
    )
    result = coverage(document, aliases=["Calderon"], periods=["2025Q1"])
    assert result.verdict == NAMES_ONLY
    assert result.names_product and not result.figures
    assert result.per_period["2025Q1"] == SILENT


def test_a_document_with_no_table_is_unreadable_not_names_only():
    """An XBRL instance, a JSON payload, a release written entirely in prose:
    the figure test never ran on any of them.

    Reporting `names_only` would say a document nobody read was read and held
    nothing, which is the one claim the predicate exists to stop being made.
    """
    prose = _document(
        [],
        "Calderon was approved in the quarter and is described in Note 12. "
        "See the discussion of the three months ended March 31, 2025.",
    )
    result = coverage(prose, aliases=["Calderon"], periods=["2025Q1"])
    assert result.verdict == UNREADABLE and not result.figures
    assert result.per_period["2025Q1"] == SILENT


def test_a_stated_nothing_refutes_the_period():
    nil = [
        [None, "Three Months Ended March 31,", "Three Months Ended March 31,"],
        [None, "2025", "2024"],
        ["Calderon", "55,881", "—"],
    ]
    result = coverage(
        _document([nil]), aliases=["Calderon"], periods=["2025Q1", "2024Q1"]
    )
    assert result.per_period["2025Q1"] == CARRIES
    assert result.per_period["2024Q1"] == REFUTES
    assert result.verdict == PARTIAL


def test_a_period_the_document_predates_is_refuted_not_silent():
    """A document reports a period that had ended when it was written, so a
    later period is one it cannot state - which is a different answer from
    being silent about it, and the difference is what a second reading of the
    same document would be spent on."""
    result = coverage(
        _document([LATER], "For the three months ended June 30, 2025"),
        aliases=["Calderon"],
        periods=["2025Q2", "2025Q3", "2024Q1"],
    )
    assert result.per_period["2025Q2"] == CARRIES
    assert result.per_period["2025Q3"] == REFUTES
    assert result.per_period["2024Q1"] == SILENT, (
        "an earlier period this document does not print is silent, not refuted"
    )


def test_a_parse_that_failed_is_unreadable_rather_than_absent():
    failed = ParsedDocument(source_id="s1", parsing_status=ParsingStatus.FAILED)
    result = coverage(failed, aliases=["Calderon"], periods=["2025Q1"])
    assert result.verdict == UNREADABLE
    assert coverage(None, aliases=["Calderon"], periods=["2025Q1"]).verdict == UNREADABLE


def test_the_verdict_is_recorded_on_the_source_and_logged(caplog):
    source = RetrievedSource(
        source_id="s1",
        source_type=SourceType.EARNINGS_RELEASE,
        url="https://www.sec.gov/Archives/edgar/data/1/2/ex991.htm",
        retrieval_status=RetrievalStatus.SUCCESS,
        metadata={"cik": "0000000001"},
    )
    with caplog.at_level("INFO", logger="app.connectors.coverage"):
        result = record_coverage(
            source, _document([SCHEDULE]), aliases=["Calderon"], periods=["2025Q1"]
        )
    assert result.verdict == ANSWERS
    assert source.metadata["cik"] == "0000000001", "what was there is kept"
    assert source.metadata["coverage"] == {
        "verdict": ANSWERS,
        "names_product": True,
        "carries": ["2025Q1"],
        # The document is written in the first quarter, so the year it belongs
        # to is a period it cannot yet state.
        "refutes": ["2025"],
        "figures": {"2025Q1": 55881.0},
    }
    assert "document_coverage" in caplog.text and "verdict=answers" in caplog.text


def test_carrying_nothing_is_the_thing_being_counted():
    """12d's measurement is "how many of the documents we fetched carried
    nothing", so that has to be one question of the verdict. It is the carried
    list being empty - the same list the verdict itself is computed from, so
    the count and the verdict cannot disagree."""
    carried = coverage(_document([SCHEDULE]), aliases=["Calderon"], periods=["2025Q1"])
    nothing = coverage(_document([SCHEDULE]), aliases=["Calderon"], periods=["2019Q1"])
    assert carried.carried == ["2025Q1"]
    assert not nothing.carried and nothing.verdict == NAMES_ONLY


def test_a_line_that_names_two_products_carries_for_neither():
    """A figure on a line naming this product and another is the pair's.

    Nothing here can split it, and the product is named - so the document is
    `names_only`, which is the answer that says a reader is still needed. A
    predicate that took the pair's figure would publish one product's revenue
    as another's, twice.
    """
    combined = [
        [None, "Three Months Ended March 31,", "Three Months Ended March 31,"],
        [None, "2025", "2024"],
        ["Calderon and NuVessa", "64,882", "28,434"],
    ]
    result = coverage(
        _document([combined]),
        aliases=["Calderon"],
        periods=["2025Q1"],
        products=["NuVessa"],
    )
    assert result.verdict == NAMES_ONLY
    assert result.names_product and not result.figures


def test_the_currency_mark_in_a_cell_of_its_own_does_not_hide_the_figure():
    """A filer that puts `$` in a column of its own puts the figure two cells
    right of the label rather than one.

    The figure is read from the column its period resolves to, so where the
    label sits and how many marks stand between them costs nothing.
    """
    dollars = [
        [None, None, "Three Months Ended March 31,", None, "Three Months Ended March 31,"],
        [None, None, "2025", None, "2024"],
        ["Calderon", "$", "55,881", "$", "19,834"],
    ]
    result = coverage(
        _document([dollars]), aliases=["Calderon"], periods=["2025Q1", "2024Q1"]
    )
    assert result.verdict == ANSWERS
    assert result.figures == {"2025Q1": 55881.0, "2024Q1": 19834.0}


def test_a_window_of_years_is_asked_and_answered_like_any_other():
    """`periods` are whatever keys the run's window produced, and a year is
    one of them - so an annual report answers a year the way an exhibit
    answers a quarter, and the year it predates is refuted the same way."""
    annual = [
        [None, "Year Ended December 31,", "Year Ended December 31,"],
        [None, "2024", "2023"],
        ["Calderon", "220,140", "180,220"],
    ]
    result = coverage(
        _document([annual], "For the year ended December 31, 2024"),
        aliases=["Calderon"],
        periods=["2024", "2023", "2025"],
    )
    assert result.per_period == {"2024": CARRIES, "2023": CARRIES, "2025": REFUTES}
    assert result.figures == {"2024": 220140.0, "2023": 180220.0}
    assert result.verdict == PARTIAL


def test_a_document_that_dates_itself_nowhere_refutes_nothing():
    """The refutation is "this document was written before that period ended".

    A document whose own reporting period cannot be read supports no such
    reasoning, so every period it does not print is silent rather than
    refuted - a period still worth asking another document for.
    """
    result = coverage(
        _document([SCHEDULE], "Calderon is described in Note 12."),
        aliases=["Calderon"],
        periods=["2025Q1", "2031Q4"],
    )
    assert result.per_period["2025Q1"] == CARRIES
    assert result.per_period["2031Q4"] == SILENT
    assert set(result.refuted) == set()


def test_a_row_is_not_its_own_sibling():
    """A label that carries a noun phrase reads as a combined line over itself
    if its own label is among the siblings it is compared against.

    The filer prints "Calderon net product sales" and "NuVessa net product
    sales"; each row's siblings are the other rows, so each reads as its own
    product. `extraction/extract.py` reads a row the same way and says the same
    thing; this is the second implementation of one idea, and it had the guard
    missing.
    """
    spelled_out = [
        [None, "Three Months Ended March 31,", "Three Months Ended March 31,"],
        [None, "2025", "2024"],
        ["Calderon net product sales", "55,881", "19,834"],
        ["NuVessa net product sales", "9,001", "8,600"],
    ]
    result = coverage(
        _document([spelled_out]), aliases=["Calderon"], periods=["2025Q1"]
    )
    assert result.figures == {"2025Q1": 55881.0}
    assert result.verdict == ANSWERS


def test_a_form_of_registration_is_not_a_name():
    """A sponsor's name split at its comma leaves `Inc.` behind as an alias.

    Every registrant of that form carries one, so a document about anybody
    prints it, and a name test given it says every document names the product.
    Both answers: the document about somebody else is `absent`, and the same
    alias list still finds the product where the product is named.
    """
    somebody_else = _document(
        [[["Product", "Territory"], ["NuVessa", "United States"]]],
        "NuVessa is sold by Beta Pharma, Inc. For the three months ended "
        "March 31, 2025",
    )
    aliases = ["Calderon", "Acme Therapeutics, Inc.", "Inc.", "S.A."]
    assert coverage(somebody_else, aliases=aliases, periods=["2025Q1"]).verdict == ABSENT

    ours = _document(
        [[["Product", "Territory"], ["Calderon", "United States"]]],
        "Calderon is sold by Acme Therapeutics, Inc. For the three months "
        "ended March 31, 2025",
    )
    assert coverage(ours, aliases=aliases, periods=["2025Q1"]).verdict == NAMES_ONLY


def test_a_row_of_figures_under_no_name_is_nobody_s_figure():
    """A filer prints spacer and continuation rows whose first cell is a
    number or nothing at all.

    Such a row states no subject, so the figures on it belong to no product -
    and the figure taken for the product is the one on the row that names it,
    not whichever row happens to be nearest.
    """
    with_spacers = [
        [None, "Three Months Ended March 31,", "Three Months Ended March 31,"],
        [None, "2025", "2024"],
        ["", "", ""],
        ["Calderon", "55,881", "19,834"],
        ["", "1,234", "5,678"],
    ]
    result = coverage(
        _document([with_spacers]), aliases=["Calderon"], periods=["2025Q1"]
    )
    assert result.figures == {"2025Q1": 55881.0}
