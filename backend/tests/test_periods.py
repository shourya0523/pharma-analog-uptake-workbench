"""Period labels must be canonical regardless of how the model names them.

An earnings exhibit headed "Three Months Ended June 30," with 2024/2023 columns was
extracted with period "Three months ended July 31, 2024" — the press-release date, not
the reporting period. The quarter therefore comes from the document, the year from the
candidate's own column.
"""

from app.parsing.periods import (
    PeriodContext,
    detect_period_context,
    normalize_period,
    quarter_of_month,
)

# Verbatim from the stored exhibit uthrq22024-ex991.htm revenue table
UTHR_Q2_2024_EXHIBIT = """
Second Quarter 2024 Financial Results
Key financial highlights include (dollars in millions, except per share data):
Three Months Ended
June 30,
Dollar Change
Percentage Change
2024
2023
Net product sales:
Tyvaso DPI
$
258.3
$
193.6
Total Tyvaso
398.2
318.9
79.3
25
%
Research and development expense for the three months ended June 30, 2024 and 2023 was
$77.2 million. Revenues for the three months ended June 30, 2024 increased as compared to
the three months ended June 30, 2023 primarily due to a lower average selling price.
"""


def test_quarter_of_month_maps_calendar_quarters():
    assert [quarter_of_month(m) for m in (1, 3, 4, 6, 7, 9, 10, 12)] == [1, 1, 2, 2, 3, 3, 4, 4]


def test_detect_period_context_reads_the_documents_own_period():
    context = detect_period_context(UTHR_Q2_2024_EXHIBIT)
    assert context == PeriodContext(months=3, month=6, year=2024)
    assert context.quarter == 2


def test_period_context_describes_itself_and_its_comparative_column():
    context = detect_period_context(UTHR_Q2_2024_EXHIBIT)
    assert context.describe() == "three months ended June 2024"
    assert context.comparative_year == 2023


def test_detect_period_context_returns_none_without_a_stated_period():
    assert detect_period_context("Total Tyvaso 398.2 318.9 79.3 25 %") is None
    assert detect_period_context("") is None


def test_hallucinated_release_date_is_corrected_to_the_document_quarter():
    context = detect_period_context(UTHR_Q2_2024_EXHIBIT)
    # The model reported the press-release date; the document says June 30
    assert (
        normalize_period("Three months ended July 31, 2024", period_type="quarterly", context=context)
        == "2024Q2"
    )


def test_prior_year_comparative_column_keeps_its_own_year():
    context = detect_period_context(UTHR_Q2_2024_EXHIBIT)
    # Comparative column is labelled only by its year; the quarter comes from the document
    assert normalize_period("2023", period_type="quarterly", context=context) == "2023Q2"
    assert normalize_period("Three months ended June 30, 2023", context=context) == "2023Q2"


def test_canonical_labels_pass_through():
    assert normalize_period("2024Q1") == "2024Q1"
    assert normalize_period("2024 Q1") == "2024Q1"
    assert normalize_period("Q1 2024") == "2024Q1"
    assert normalize_period("FY2024Q3") == "2024Q3"
    assert normalize_period("2024") == "2024"


def test_non_quarterly_lengths_get_distinct_labels():
    context = PeriodContext(months=3, month=6, year=2024)
    assert normalize_period("Six months ended June 30, 2024", context=context) == "2024H1"
    assert normalize_period("Nine months ended September 30, 2024") == "2024M9"
    assert normalize_period("Twelve months ended December 31, 2024") == "2024"


def test_annual_year_is_not_forced_into_a_quarter():
    context = detect_period_context(UTHR_Q2_2024_EXHIBIT)
    assert normalize_period("2023", period_type="annual", context=context) == "2023"


def test_unusable_labels_return_none():
    assert normalize_period("YYYY") is None
    assert normalize_period("unknown") is None
    assert normalize_period("") is None
    assert normalize_period(None) is None


# Two headings taken verbatim from earnings exhibits, flattened the way the
# document parser flattens them. Both used to date the document wrongly.
SPLIT_HEADING = (
    "Three Months Ended\nJune 30,\nSix Months Ended\nJune 30,\n"
    "2007\n2006\n2007\n2006\nHIV products:\nTruvada - U.S.\n186,256\n207,738"
)

Q4_WITH_FOOTNOTES = (
    "Three months ended\nDecember 31,\nYear ended\nDecember 31,\n"
    "2005\n2004\n2005\n2004\nAmBisome\n55,596\n55,025\n"
    # The comparative year is named more often than the reporting year, which
    # is exactly how frequency-based selection went wrong.
    "Results for the three months ended December 31, 2004 include the effect of "
    "outstanding options. Shares used for the three months ended December 31, 2004 "
    "differ. The three months ended December 31, 2004 exclude the make-whole payment. "
    "Amounts for the three months ended December 31, 2004 are restated. "
    "The three months ended December 31, 2005 include the effect of options."
)


def test_a_heading_split_across_lines_still_dates_the_document():
    """The year sits a few tokens below the heading, not beside it.

    Requiring adjacency dropped every three-month reading and left the document
    to be dated by its year-to-date heading instead, which is how a quarterly
    exhibit came to describe itself as a half-year one.
    """
    context = detect_period_context(SPLIT_HEADING)
    assert context is not None
    assert context.months == 3
    assert (context.year, context.quarter) == (2007, 2)
    assert context.describe() == "three months ended June 2007"


def test_the_reporting_year_wins_over_a_more_repeated_comparative():
    """A comparative year is earlier, however often the footnotes name it."""
    context = detect_period_context(Q4_WITH_FOOTNOTES)
    assert context is not None
    assert (context.months, context.year, context.quarter) == (3, 2005, 4)


def test_a_filing_covering_two_spans_is_dated_by_its_quarter():
    """"three and six months ended" names two spans, and the quarter is the one.

    Matching a single span word here read only the second of them, so a
    second-quarter exhibit was dated 2026H1 and a third-quarter one 2026M9. The
    preference for the quarterly framing could not fire, because the quarterly
    framing was never counted, and that wrong period was handed to the model as
    the filing's `reporting_period`.
    """
    context = detect_period_context(
        "Results for the three and six months ended June 28, 2026 are summarized below."
    )
    assert context is not None
    assert (context.months, context.month, context.year) == (3, 6, 2026)
    assert context.describe().endswith("2026") or "three" in context.describe().lower()


def test_a_filing_naming_only_the_longer_span_keeps_it():
    """The fix must not turn every year-to-date document into a quarter."""
    context = detect_period_context("For the six months ended June 28, 2026, revenues were")
    assert context is not None
    assert context.months == 6


def test_three_and_nine_is_read_as_the_third_quarter():
    context = detect_period_context(
        "the three and nine months ended September 30, 2025 reflect"
    )
    assert context is not None
    assert (context.months, context.month, context.year) == (3, 9, 2025)


def test_a_period_ending_in_the_first_days_of_a_month_belongs_to_the_month_before():
    """A filer on a 52/53-week calendar states its first quarter as ending on
    April 1 or 2 and its year on January 3; read by the month alone, the first
    quarter becomes the second and the year the next one."""
    from app.parsing.periods import (
        detect_period_context,
        fiscal_period_end,
        normalize_period,
    )

    assert fiscal_period_end(4, 1) == (3, None)
    assert fiscal_period_end(1, 3, 2021) == (12, 2020)
    assert fiscal_period_end(4, 30, 2018) == (4, 2018)
    assert fiscal_period_end(3, None, 2018) == (3, 2018)

    first_quarter = ("Fiscal first quarter ended April 1, 2018. Sales for the three months "
                     "ended April 1, 2018 rose against the three months ended April 2, 2017.")
    context = detect_period_context(first_quarter)
    assert (context.months, context.month, context.year) == (3, 3, 2018)
    assert normalize_period("three months ended January 3, 2021") == "2020Q4"


def test_a_filing_that_names_a_year_throughout_is_dated_as_a_year():
    """An annual report names "fiscal year ended" on every statement and the
    fourth quarter only in passing; a quarterly release names its quarter and
    its year-to-date span about equally. The first is a year, the second a
    quarter."""
    from app.parsing.periods import detect_period_context

    annual = ("Annual report for the fiscal year ended December 31, 2017. " * 3
              + "Sales in the fourth quarter 2017 and fourth quarter of 2017 grew; "
              + "the year ended December 31, 2017 compared with the year ended December 31, 2016.")
    context = detect_period_context(annual)
    assert (context.months, context.year) == (12, 2017)

    release = ("Three months ended December 31, 2017 and year ended December 31, 2017. " * 3
               + "Three months ended December 31, 2016 and year ended December 31, 2016.")
    context = detect_period_context(release)
    assert (context.months, context.month, context.year) == (3, 12, 2017)


def test_a_table_headed_by_a_fiscal_quarter_end_is_read_into_that_quarter():
    from app.extraction.fingerprint import _periods_named_in
    from app.parsing.tables import extract_revenue_rows

    rows = [
        ["", "Fiscal First Quarter Ended"],
        ["", "Three Months Ended April 1, 2018", "Three Months Ended April 2, 2017"],
        ["", "2018", "2017"],
        ["Calderon", "1,389", "1,672"],
    ]
    found = extract_revenue_rows([rows], product="Calderon")
    assert {(c["period"], c["value_reported"]) for c in found} == {("2018Q1", 1389.0), ("2017Q1", 1672.0)}
    assert _periods_named_in("Three Months Ended April 1,") == [(3, 3)]
    assert _periods_named_in("Year Ended January 3,") == [(12, 12)]
