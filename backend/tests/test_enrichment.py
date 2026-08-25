from app.domain.formulations import (
    AGGREGATE_FORMULATION,
    coerce_formulation_value,
    format_formulations,
    is_aggregate_formulation,
    parse_formulations,
)
from app.domain.models import RevenueCandidate, ValidationStatus
from app.quality.enrichment import (
    ENRICHMENT_CONFIDENCE_CAP,
    apply_field_enrichment,
    deterministic_formulation_fill,
)


def test_parse_and_format_multi_formulation():
    assert parse_formulations("DPI; nebulized") == ["DPI", "nebulized"]
    assert format_formulations(["DPI", "nebulized", "DPI"]) == "DPI; nebulized"
    assert coerce_formulation_value(["DPI", "nebulized"]) == "DPI; nebulized"
    assert coerce_formulation_value("aggregate") == AGGREGATE_FORMULATION
    assert is_aggregate_formulation("DPI; nebulized")
    assert is_aggregate_formulation("aggregate")
    assert not is_aggregate_formulation("tablet")


def test_revenue_candidate_accepts_formulation_list():
    cand = RevenueCandidate.model_validate(
        {
            "period": "2024Q1",
            "value_reported": 1.0,
            "source_quote": "Tyvaso 1",
            "formulation": ["DPI", "nebulized"],
            "revenue_scope": "Product family",
            "period_type": "quarterly",
        }
    )
    assert cand.formulation == "DPI; nebulized"


def test_deterministic_product_family_gets_aggregate():
    fill = deterministic_formulation_fill(
        {"revenue_scope": "Product family", "formulation": None}
    )
    assert fill["suggested_formulation"] == AGGREGATE_FORMULATION


def test_apply_enrichment_fills_blank_fields_and_flags_review():
    dp = {
        "period": "2024Q1",
        "revenue_scope": "Product family",
        "formulation": None,
        "geography": None,
        "confidence_score": 0.9,
        "validation_status": "auto_pass",
        "issue_flags": [],
        "citation_json": {"source_url": "https://example.com", "accession_number": None},
    }
    enriched, applied = apply_field_enrichment(
        dp,
        {
            "suggested_formulation": ["DPI", "nebulized"],
            "suggested_geography": "Worldwide",
            "suggested_accession_number": "0001082554-24-000027",
            "notes": "family total",
        },
    )
    assert "formulation" in applied
    assert "geography" in applied
    assert "citation.accession_number" in applied
    assert enriched["formulation"] == "DPI; nebulized"
    assert enriched["geography"] == "Worldwide"
    assert enriched["citation_json"]["accession_number"] == "0001082554-24-000027"
    assert enriched["validation_status"] == ValidationStatus.NEEDS_REVIEW.value
    assert enriched["confidence_score"] == ENRICHMENT_CONFIDENCE_CAP
    assert "field_enrichment_applied" in enriched["issue_flags"]


def test_apply_enrichment_does_not_overwrite_existing():
    dp = {
        "formulation": "tablet",
        "confidence_score": 0.9,
        "validation_status": "auto_pass",
        "issue_flags": [],
        "citation_json": {},
    }
    enriched, applied = apply_field_enrichment(
        dp, {"suggested_formulation": "aggregate"}
    )
    assert applied == []
    assert enriched["formulation"] == "tablet"
    assert enriched["validation_status"] == "auto_pass"
