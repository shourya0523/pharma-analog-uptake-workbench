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
