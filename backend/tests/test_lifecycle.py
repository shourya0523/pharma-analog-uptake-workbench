from datetime import date

from app.analytics.lifecycle import (
    coverage_pct,
    expected_quarters_for_job,
    iter_quarters,
    latest_completed_quarter,
    lifecycle_quarters,
    missing_expected_quarters,
    parse_quarter_label,
    quarter_label,
    quarter_of_date,
)
from app.analytics.peak_sales import (
    SalesObservation,
    complete_comparable_years,
    sales_observation_from_payload,
    select_peak_from_observations,
)
from app.connectors.openfda_fields import parse_openfda_date, selected_approval_date
from app.quality.completeness import lifecycle_gaps, resolve_completeness_pct
from tests.test_openfda_enrichment import LIVE_RESULTS, TYVASO
from tests.test_peak_sales import _annual


def test_quarter_label_round_trips_through_parse():
    label = quarter_label(2009, 3)
    assert parse_quarter_label(label) == (2009, 3)
    assert parse_quarter_label("not-a-quarter") is None


def test_latest_completed_quarter_is_the_elapsed_calendar_quarter():
    assert latest_completed_quarter(date(2026, 8, 28)) == quarter_label(2026, 2)
    assert latest_completed_quarter(date(2026, 1, 5)) == quarter_label(2025, 4)


def test_lifecycle_quarters_run_from_approval_through_as_of():
    approval = date(2024, 3, 26)
    as_of = date(2026, 8, 28)
    expected = lifecycle_quarters(approval_date=approval, as_of=as_of)
    assert expected[0] == quarter_of_date(approval)
    assert expected[-1] == latest_completed_quarter(as_of)
    assert expected == iter_quarters(expected[0], expected[-1])


def test_expected_quarters_use_approval_when_lifecycle_coverage_is_on():
    known = ["2025Q1", "2025Q2"]
    approval = date(2024, 3, 26)
    as_of = date(2026, 8, 28)
    expected = expected_quarters_for_job(
        approval_date=approval,
        known_periods=known,
        as_of=as_of,
        lifecycle_coverage=True,
    )
    assert expected[0] == quarter_of_date(approval)
    assert "2025Q1" in expected
    assert expected[-1] == latest_completed_quarter(as_of)


def test_expected_quarters_without_approval_extend_extracted_span_to_as_of():
    known = ["2023Q1", "2023Q4"]
    expected = expected_quarters_for_job(
        approval_date=None,
        known_periods=known,
        as_of=date(2024, 5, 1),
        lifecycle_coverage=True,
    )
    assert expected[0] == "2023Q1"
    assert expected[-1] == latest_completed_quarter(date(2024, 5, 1))


def test_lifecycle_gaps_lists_every_unaccounted_quarter():
    expected, missing = lifecycle_gaps(
        approval_date=date(2024, 3, 26),
        known_periods=["2024Q2", "2024Q3"],
        as_of=date(2025, 1, 15),
    )
    assert expected[0] == "2024Q1"
    assert expected[-1] == latest_completed_quarter(date(2025, 1, 15))
    assert "2024Q2" not in missing
    assert "2024Q1" in missing
    assert expected[-1] in missing
    reported = len(expected) - len(missing)
    assert coverage_pct(reported_count=reported, expected_count=len(expected)) == resolve_completeness_pct(
        0, quarterly_count=reported, unresolved_quarter_count=len(missing)
    )


def test_missing_expected_quarters_preserves_lifecycle_order():
    expected = iter_quarters("2024Q1", "2024Q4")
    assert missing_expected_quarters(expected, {"2024Q2"}) == ["2024Q1", "2024Q3", "2024Q4"]


def test_selected_approval_date_uses_the_product_application():
    expected = date.fromisoformat(parse_openfda_date(TYVASO["submissions"][0]["submission_status_date"]))
    assert selected_approval_date(LIVE_RESULTS, product="Tyvaso", generic="treprostinil") == expected
    assert selected_approval_date(LIVE_RESULTS, product="Winrevair", generic="sotatercept") is None


def test_selected_approval_date_is_the_earliest_matching_application():
    late = {
        "openfda": {"brand_name": ["UPTRAVI"]},
        "submissions": [
            {"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20210729"}
        ],
    }
    early = {
        "openfda": {"brand_name": ["UPTRAVI"]},
        "submissions": [
            {"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20151221"}
        ],
    }
    assert selected_approval_date([late, early], product="Uptravi") == date.fromisoformat(
        parse_openfda_date(early["submissions"][0]["submission_status_date"])
    )


def test_sales_observation_from_payload_feeds_peak_selection():
    annual_control = [
        _annual(year, value)
        for year, value in ((2020, 100), (2021, 200), (2022, 300), (2023, 265), (2024, 250))
    ]
    payloads = [
        {
            "gold_id": row.id,
            "period": row.period,
            "value_reported": row.value,
            "currency": row.currency,
            "geography": row.geography,
            "revenue_scope": row.revenue_scope,
            "period_type": row.period_type,
            "period_basis": row.period_basis,
        }
        for row in annual_control
    ]
    observations = [sales_observation_from_payload(row) for row in payloads]
    selected = select_peak_from_observations(observations, as_of_date=date(2025, 1, 1))
    from app.analytics.peak_sales import (
        aggregate_comparable_sales,
        select_peak_estimate,
    )

    control = select_peak_estimate(
        annual_sales=aggregate_comparable_sales(annual_control),
        estimates=[],
        as_of_date=date(2025, 1, 1),
    )
    assert selected is not None and control is not None
    assert selected.estimate_type == control.estimate_type
    assert selected.value == control.value
    assert complete_comparable_years(observations) == len(annual_control)


def test_incomplete_quarter_years_are_not_peak_eligible():
    observations = [
        SalesObservation(
            id="q1",
            period="2024Q1",
            value=10,
            currency="USD",
            geography="Worldwide",
            revenue_scope="Worldwide",
            period_type="quarterly",
            period_basis="calendar",
        ),
        SalesObservation(
            id="q2",
            period="2024Q2",
            value=12,
            currency="USD",
            geography="Worldwide",
            revenue_scope="Worldwide",
            period_type="quarterly",
            period_basis="calendar",
        ),
    ]
    assert complete_comparable_years(observations) == 0
    assert select_peak_from_observations(observations, as_of_date=date(2026, 8, 28)) is None
