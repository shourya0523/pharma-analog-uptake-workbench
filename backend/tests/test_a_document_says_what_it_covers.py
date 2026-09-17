"""What a fetched document turns out to hold, asked of the document.

Retrieval picks filings by form code and item code and nothing downstream ever
asks what the document it got covers, so a run that fetched 28 documents and
one that fetched 28 useful documents look the same. `coverage` is that
question. It changes nothing about what is fetched.

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
    assert result.per_period == {"2025Q1": CARRIES}
    assert result.figures == {"2025Q1": 55881.0}


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


def test_a_product_named_with_no_reachable_figure_is_names_only():
    """The row-grouped schedule: the product is a group heading and the
    figures sit on rows labelled by geography, which `read_label` reads the
    scope of and finds no product on.

    Saying `names_only` here is the point. The document does hold the figure;
    the reader cannot reach it; a predicate that said `carries` would hide
    exactly the gap it exists to show.
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
    assert result.verdict == NAMES_ONLY
    assert result.names_product and not result.figures
    assert result.per_period == {"2025Q1": SILENT}


def test_a_name_in_prose_and_no_table_is_names_only_not_carries():
    prose = _document(
        [],
        "Calderon was approved in the quarter and is described in Note 12. "
        "See the discussion of the three months ended March 31, 2025.",
    )
    result = coverage(prose, aliases=["Calderon"], periods=["2025Q1"])
    assert result.verdict == NAMES_ONLY and not result.figures


def test_a_stated_nothing_refutes_the_period():
    nil = [
        [None, "Three Months Ended March 31,", "Three Months Ended March 31,"],
        [None, "2025", "2024"],
        ["Calderon", "55,881", "—"],
    ]
    result = coverage(
        _document([nil]), aliases=["Calderon"], periods=["2025Q1", "2024Q1"]
    )
    assert result.per_period == {"2025Q1": CARRIES, "2024Q1": REFUTES}
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
        "refutes": [],
        "figures": {"2025Q1": 55881.0},
    }
    assert "document_coverage" in caplog.text and "verdict=answers" in caplog.text


def test_carrying_nothing_is_the_thing_being_counted():
    """12d's measurement is "how many of the documents we fetched carried
    nothing", so that has to be one question of the verdict."""
    carried = coverage(_document([SCHEDULE]), aliases=["Calderon"], periods=["2025Q1"])
    nothing = coverage(_document([SCHEDULE]), aliases=["Calderon"], periods=["2019Q1"])
    assert not carried.carries_nothing
    assert nothing.carries_nothing and nothing.verdict == NAMES_ONLY
