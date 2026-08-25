from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class QualityIssue:
    issue_type: str
    severity: str
    affected_datapoint: str | None
    explanation: str
    recommended_action: str
    status: str = "open"


def moa_epc_contamination_issue(moa: str | None, epc_terms: list[str]) -> QualityIssue | None:
    normalized_moa = " ".join((moa or "").lower().split())
    normalized_epc = {" ".join(term.lower().split()) for term in epc_terms}
    if normalized_moa and normalized_moa in normalized_epc:
        return QualityIssue(
            "epc_used_as_moa",
            "high",
            None,
            "Mechanism of action exactly matches an FDA Established Pharmacologic Class value.",
            "Leave MoA unresolved until a distinct cited MoA field or label mechanism section is available.",
        )
    return None


def _normalize_number_str(value: float | None) -> list[str]:
    if value is None:
        return []
    forms = {
        str(value),
        f"{value:,}",
        f"{value:.1f}",
        f"{value:.0f}",
        str(int(value)) if float(value).is_integer() else str(value),
    }
    return list(forms)


def quote_contains_value(quote: str, value: float | None) -> bool:
    if value is None:
        return False
    q = quote.replace(",", "").lower()
    for form in _normalize_number_str(value):
        if form.replace(",", "").lower() in q:
            return True
    # millions / billions wording
    if float(value).is_integer():
        iv = int(value)
        if re.search(rf"{iv}\s*(million|billion)", q):
            return True
    # $1,878.2 style already covered via comma strip; also try without trailing zeros
    compact = f"{float(value):.10f}".rstrip("0").rstrip(".")
    return bool(compact and compact in q)


def run_quality_checks(datapoints: list[dict[str, Any]], profile: dict[str, Any] | None = None) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    seen_keys: set[tuple] = set()

    for dp in datapoints:
        dp_id = dp.get("id")
        url = (dp.get("source_url") or "").strip()
        quote = (dp.get("source_quote") or "").strip()
        period_type = (dp.get("period_type") or "").lower()
        scope = (dp.get("revenue_scope") or "")
        status = dp.get("validation_status")

        if not url:
            issues.append(
                QualityIssue(
                    "missing_source_url",
                    "high",
                    dp_id,
                    "Datapoint has no source URL citation.",
                    "Reject auto-pass; attach source URL before validation.",
                )
            )
        if not quote:
            issues.append(
                QualityIssue(
                    "missing_source_quote",
                    "high",
                    dp_id,
                    "Datapoint has no source quote.",
                    "Reject auto-pass; require quote containing the reported value.",
                )
            )
        elif not quote_contains_value(quote, dp.get("value_reported")):
            issues.append(
                QualityIssue(
                    "source_evidence_missing_value",
                    "high",
                    dp_id,
                    "Source quote does not contain the extracted value.",
                    "Mark needs_review; do not auto-validate.",
                )
            )

        if period_type == "ytd" and status == "auto_pass":
            issues.append(
                QualityIssue(
                    "ytd_classified_as_quarterly",
                    "high",
                    dp_id,
                    "YTD value must not auto-pass as quarterly product revenue.",
                    "Reclassify or mark unresolved/needs_review.",
                )
            )
        if period_type == "annual" and "Q" in str(dp.get("period", "")) and status == "auto_pass":
            issues.append(
                QualityIssue(
                    "annual_classified_as_quarterly",
                    "high",
                    dp_id,
                    "Annual value appears classified as a quarter.",
                    "Correct period_type or reject.",
                )
            )
        if scope == "Company total" and status == "auto_pass":
            issues.append(
                QualityIssue(
                    "company_total_as_product",
                    "high",
                    dp_id,
                    "Company-total revenue cannot auto-pass as product revenue.",
                    "Keep as company total scope and send to review.",
                )
            )

        if dp.get("value_reported") is not None and dp["value_reported"] < 0:
            issues.append(
                QualityIssue(
                    "negative_revenue",
                    "medium",
                    dp_id,
                    "Negative revenue value.",
                    "Verify sign; likely needs review.",
                )
            )
        if not dp.get("currency"):
            issues.append(
                QualityIssue(
                    "unclear_currency",
                    "medium",
                    dp_id,
                    "Currency missing.",
                    "Confirm currency from source.",
                )
            )
        if not dp.get("unit"):
            issues.append(
                QualityIssue(
                    "unclear_unit",
                    "medium",
                    dp_id,
                    "Unit missing.",
                    "Confirm unit (millions/thousands) from source.",
                )
            )

        key = (dp.get("period"), dp.get("revenue_scope"), dp.get("formulation"), dp.get("geography"))
        if key in seen_keys:
            issues.append(
                QualityIssue(
                    "duplicate_period_scope_formulation",
                    "medium",
                    dp_id,
                    f"Duplicate period/scope/formulation key {key}.",
                    "Reconcile duplicates; preserve separate scopes if truly different.",
                )
            )
        seen_keys.add(key)

    if profile is not None and not profile.get("fda_approval_date"):
        issues.append(
            QualityIssue(
                "missing_fda_approval_date",
                "medium",
                None,
                "FDA approval date missing; timeline may be incomplete.",
                "Enrich via OpenFDA or mark unresolved timeline start.",
            )
        )

    # Gap detection: unresolved between resolved quarters handled by completeness step
    return issues


def apply_auto_pass_gate(datapoint: dict[str, Any], issues: list[QualityIssue]) -> str:
    high = [i for i in issues if i.severity == "high" and i.affected_datapoint == datapoint.get("id")]
    if high:
        return "needs_review"
    if not datapoint.get("source_url") or not datapoint.get("source_quote"):
        return "needs_review"
    if datapoint.get("period_type") in {"ytd", "guidance", "six_month", "nine_month"}:
        return "needs_review"
    if datapoint.get("revenue_scope") == "Company total":
        return "needs_review"
    if (datapoint.get("confidence_score") or 0) < 0.7:
        return "needs_review"
    # Preserve judge decision when already needs_review/rejected; allow auto_pass through
    status = datapoint.get("validation_status") or "auto_pass"
    if status in {"needs_review", "rejected", "follow_up", "unresolved"}:
        return status
    if status == "auto_pass":
        return "auto_pass"
    # pending + clean → auto_pass for quarterly/annual product lines
    if datapoint.get("period_type") in {"quarterly", "annual"}:
        return "auto_pass"
    return status
