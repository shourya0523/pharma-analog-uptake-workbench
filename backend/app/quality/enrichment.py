"""Apply LLM enrichment suggestions onto datapoints with low confidence + needs_review."""

from __future__ import annotations

from typing import Any

from app.domain.formulations import AGGREGATE_FORMULATION, coerce_formulation_value
from app.domain.models import ValidationStatus

# Datapoint ORM / dict columns enrichment may fill when blank.
DATAPOINT_ENRICH_FIELDS = (
    "period",
    "period_type",
    "revenue_scope",
    "value_reported",
    "value_normalized_usd_millions",
    "currency",
    "unit",
    "geography",
    "formulation",
    "route_of_administration",
    "fiscal_year",
    "fiscal_quarter",
    "calendar_year",
    "calendar_quarter",
)

# Citation / provenance fields stored on citation_json.
CITATION_ENRICH_FIELDS = (
    "filing_type",
    "accession_number",
    "page_or_section",
    "source_title",
)

ENRICHMENT_CONFIDENCE_CAP = 0.55

# Prompt-friendly aliases → datapoint / citation field names.
ENRICHMENT_ALIASES = {
    "suggested_scope": "suggested_revenue_scope",
    "suggested_value": "suggested_value_reported",
    "scope": "revenue_scope",
    "value": "value_reported",
}


def _normalize_enrichment_keys(enrichment: dict[str, Any]) -> dict[str, Any]:
    out = dict(enrichment)
    for alias, canonical in ENRICHMENT_ALIASES.items():
        if alias in out and canonical not in out:
            out[canonical] = out[alias]
    return out


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and not value:
        return True
    return False


def _suggestion_key(field: str) -> str:
    return f"suggested_{field}"


def deterministic_formulation_fill(datapoint: dict[str, Any]) -> dict[str, Any] | None:
    """If product-family scope has no formulation, use the aggregate sentinel."""
    scope = (datapoint.get("revenue_scope") or "").strip()
    if scope != "Product family":
        return None
    if not _is_blank(datapoint.get("formulation")):
        return None
    return { _suggestion_key("formulation"): AGGREGATE_FORMULATION, "notes": "product_family_aggregate_default" }


def apply_field_enrichment(
    datapoint: dict[str, Any],
    enrichment: dict[str, Any] | None,
    *,
    confidence_cap: float = ENRICHMENT_CONFIDENCE_CAP,
) -> tuple[dict[str, Any], list[str]]:
    """Fill blank fields from enrichment suggestions.

    Returns (updated_datapoint, applied_field_names).
    Any applied fill forces needs_review and caps confidence.
    """
    if not enrichment:
        return datapoint, []

    enrichment = _normalize_enrichment_keys(enrichment)

    updated = dict(datapoint)
    citation = dict(updated.get("citation_json") or {})
    applied: list[str] = []
    applied_detail: dict[str, Any] = dict(citation.get("enrichment_applied") or {})

    for field in DATAPOINT_ENRICH_FIELDS:
        suggested = enrichment.get(_suggestion_key(field), enrichment.get(field))
        if suggested is None:
            continue
        if not _is_blank(updated.get(field)):
            continue
        value = suggested
        if field == "formulation":
            value = coerce_formulation_value(suggested)
            if value is None:
                continue
        updated[field] = value
        applied.append(field)
        applied_detail[field] = value

    for field in CITATION_ENRICH_FIELDS:
        suggested = enrichment.get(_suggestion_key(field), enrichment.get(field))
        if suggested is None:
            continue
        if not _is_blank(citation.get(field)):
            continue
        citation[field] = suggested
        applied.append(f"citation.{field}")
        applied_detail[f"citation.{field}"] = suggested

    if not applied:
        return datapoint, []

    notes = enrichment.get("notes")
    if notes:
        applied_detail["notes"] = notes

    prior = float(updated.get("confidence_score") or 0.0)
    updated["confidence_score"] = min(prior if prior > 0 else confidence_cap, confidence_cap)
    updated["validation_status"] = ValidationStatus.NEEDS_REVIEW.value

    flags = list(updated.get("issue_flags") or [])
    flags.append("field_enrichment_applied")
    for field in applied:
        flags.append(f"enriched:{field}")
    updated["issue_flags"] = sorted(set(flags))

    citation["enrichment_applied"] = applied_detail
    citation["validation_status"] = ValidationStatus.NEEDS_REVIEW.value
    updated["citation_json"] = citation
    return updated, applied


def merge_enrichment_dicts(*parts: dict[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    notes: list[str] = []
    for part in parts:
        if not part:
            continue
        for key, value in part.items():
            if key == "notes" and value:
                notes.append(str(value))
            elif value is not None and key not in merged:
                merged[key] = value
    if notes:
        merged["notes"] = "; ".join(notes)
    return merged
