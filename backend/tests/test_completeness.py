from app.quality.candidate_filters import (
    filter_revenue_candidates,
    is_placeholder_period,
)
from app.quality.completeness import quarter_labels


def test_placeholder_period_detects_prompt_skeleton_echo():
    assert is_placeholder_period("YYYY")
    assert is_placeholder_period("YYYYQn")
    assert not is_placeholder_period("2024Q1")
    assert not is_placeholder_period("2023")
    # "unknown" is the orchestrator's own marker for an undetermined period
    assert not is_placeholder_period("unknown")
    assert not is_placeholder_period(None)


def test_filter_drops_placeholder_period_candidate():
    candidates = [
        {
            "period": "YYYY",
            "value_reported": 1233.7,
            "revenue_scope": "Product family",
            "source_quote": "$1,233.7 million in combined Tyvaso DPI and nebulized Tyvaso net product sales",
        },
        {
            "period": "2024Q1",
            "value_reported": 372.5,
            "revenue_scope": "Product family",
            "source_quote": "Total Tyvaso 372.5 238.4 134.1 56 %",
        },
    ]
    kept, dropped = filter_revenue_candidates(candidates, product="Tyvaso", generic="treprostinil")
    assert [c["period"] for c in kept] == ["2024Q1"]
    assert [d["_drop_reason"] for d in dropped] == ["placeholder_period"]


def test_quarter_labels_collapse_spellings_of_one_quarter():
    # Two readings of one quarter are one quarter, however each was labelled.
    assert quarter_labels(["2024Q2", "FY2024 Q2", "Q2 2024"]) == {"2024Q2"}
    # A span longer than a quarter is not a quarter.
    assert quarter_labels(["2024", "2024Q1"]) == {"2024Q1"}
    assert quarter_labels([]) == set()
    assert quarter_labels(None) == set()
