from app.quality.candidate_filters import (
    filter_revenue_candidates,
    is_placeholder_period,
)
from app.quality.completeness import resolve_completeness_pct


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


def test_completeness_falls_back_when_model_echoes_zero():
    # The prompt skeleton ships "completeness_pct": 0, so a literal 0 is not an answer.
    assert resolve_completeness_pct(0, quarterly_count=6, unresolved_quarter_count=0) == 100.0
    assert resolve_completeness_pct(None, quarterly_count=3, unresolved_quarter_count=1) == 75.0
    assert resolve_completeness_pct("nonsense", quarterly_count=1, unresolved_quarter_count=1) == 50.0


def test_completeness_keeps_model_value_and_clamps_range():
    assert resolve_completeness_pct(62.5, quarterly_count=6, unresolved_quarter_count=0) == 62.5
    assert resolve_completeness_pct(140, quarterly_count=6, unresolved_quarter_count=0) == 100.0
    assert resolve_completeness_pct(-5, quarterly_count=0, unresolved_quarter_count=2) == 0.0


def test_completeness_zero_when_nothing_extracted():
    assert resolve_completeness_pct(0, quarterly_count=0, unresolved_quarter_count=4) == 0.0


def test_orchestrator_completeness_fills_lifecycle_not_extracted_minmax():
    import inspect

    from app.pipeline.orchestrator import PipelineOrchestrator

    source = inspect.getsource(PipelineOrchestrator._completeness)
    assert "lifecycle_gaps" in source
    assert "lifecycle_coverage" in source
    search = inspect.getsource(PipelineOrchestrator._search_revenue_fallback)
    assert "_lifecycle_search_context" in search
