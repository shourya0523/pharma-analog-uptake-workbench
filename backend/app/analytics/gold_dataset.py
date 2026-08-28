"""Assemble lifecycle gold records from production lifecycle and peak helpers."""

from __future__ import annotations

import re
from datetime import date

from app.analytics.lifecycle import (
    coverage_pct,
    expected_quarters_for_job,
    latest_completed_quarter,
    lifecycle_start_quarter,
    missing_expected_quarters,
)
from app.analytics.peak_sales import (
    complete_comparable_years,
    sales_observation_from_payload,
    select_peak_from_observations,
)
from app.quality.checks import quote_contains_value


def slug(*parts: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", "-".join(str(p).lower() for p in parts if p is not None)).strip("-")


def reported_periods(rows: list[dict], drug_name: str) -> set[str]:
    return {row["period"] for row in rows if row["drug_name"] == drug_name}


def lifecycle_record(
    *,
    drug_name: str,
    approval_date: date | None,
    as_of: date,
    reported: set[str],
    unresolved: set[str],
    approval_source_url: str | None = None,
) -> dict:
    expected = expected_quarters_for_job(
        approval_date=approval_date,
        known_periods=sorted(reported | unresolved),
        as_of=as_of,
        lifecycle_coverage=True,
    )
    in_lifecycle_reported = {p for p in reported if p in set(expected)}
    in_lifecycle_unresolved = {p for p in unresolved if p in set(expected)}
    start = expected[0] if expected else (lifecycle_start_quarter(approval_date) if approval_date else None)
    end = expected[-1] if expected else latest_completed_quarter(as_of)
    return {
        "drug_name": drug_name,
        "fda_approval_date": approval_date.isoformat() if approval_date else None,
        "approval_source_url": approval_source_url,
        "lifecycle_start_quarter": start,
        "lifecycle_end_quarter": end,
        "expected_quarter_count": len(expected),
        "reported_quarter_count": len(in_lifecycle_reported),
        "unresolved_quarter_count": len(in_lifecycle_unresolved),
        "coverage_pct": coverage_pct(len(in_lifecycle_reported), len(expected)),
        "peak_eligible": False,
    }


def peak_record(
    *,
    drug_name: str,
    rows: list[dict],
    as_of: date,
    expected_count: int,
) -> dict:
    observations = [sales_observation_from_payload(row) for row in rows]
    years = complete_comparable_years(observations)
    selected = select_peak_from_observations(observations, as_of_date=as_of)
    payload = {
        "gold_id": slug(drug_name, "selected-peak"),
        "drug_name": drug_name,
        "as_of_date": as_of.isoformat(),
        "expected_quarters": expected_count,
        "reported_quarters": len({row["period"] for row in rows}),
        "complete_comparable_years": years,
        "estimate_type": selected.estimate_type if selected else None,
        "value": selected.value if selected else None,
        "currency": selected.currency if selected else None,
        "geography": selected.geography if selected else None,
        "revenue_scope": selected.revenue_scope if selected else None,
        "selection_method": selected.selection_method if selected else "insufficient_lifecycle_history",
        "input_ids": selected.input_ids if selected else [],
        "peak_eligible": bool(selected and selected.estimate_type == "observed"),
    }
    return payload


def default_unresolved_sources(existing_rows: list[dict], drug_name: str) -> list[dict]:
    for row in existing_rows:
        if row.get("drug_name") != drug_name:
            continue
        sources = row.get("sources_checked")
        if sources:
            return sources
        url = row.get("source_url")
        if isinstance(url, str) and url.startswith("https://"):
            return [
                {
                    "source_url": url,
                    "source_title": row.get("source_title") or "Issuer disclosure",
                    "observation": "Checked during lifecycle gold rebuild; product-quarter still missing.",
                }
            ]
    return [
        {
            "source_url": "https://www.sec.gov/edgar/searchedgar/companysearch",
            "source_title": "SEC EDGAR company search",
            "observation": "Lifecycle quarter has no retrieved product-level disclosure yet.",
        }
    ]


def unresolved_row(
    *,
    drug_name: str,
    period: str,
    sources_checked: list[dict],
    reason_unresolved: str,
    recommended_next_step: str | None = None,
    confidence_that_unavailable: float = 0.4,
    gold_notes: str = "",
) -> dict:
    notes = gold_notes or ""
    if "not a zero-revenue label" not in notes:
        notes = (notes + " This is a non-disclosure label, not a zero-revenue label.").strip()
    return {
        "gold_id": slug(drug_name, period, "not-separately-disclosed"),
        "drug_name": drug_name,
        "period": period,
        "reason_unresolved": reason_unresolved,
        "sources_checked": sources_checked,
        "recommended_next_step": recommended_next_step
        or "Retrieve historical earnings exhibits / IR sales schedules for this lifecycle quarter.",
        "confidence_that_unavailable": confidence_that_unavailable,
        "gold_notes": notes,
    }


def fill_lifecycle_unresolved(
    *,
    drug_name: str,
    approval_date: date | None,
    as_of: date,
    reported: set[str],
    existing_unresolved: list[dict],
    source_rows: list[dict],
) -> list[dict]:
    expected = expected_quarters_for_job(
        approval_date=approval_date,
        known_periods=sorted(reported | {row["period"] for row in existing_unresolved}),
        as_of=as_of,
        lifecycle_coverage=True,
    )
    keep = {
        row["period"]: row
        for row in existing_unresolved
        if row["drug_name"] == drug_name and row["period"] in set(expected) and row["period"] not in reported
    }
    sources = default_unresolved_sources(existing_unresolved + source_rows, drug_name)
    for period in missing_expected_quarters(expected, reported | set(keep)):
        keep[period] = unresolved_row(
            drug_name=drug_name,
            period=period,
            sources_checked=sources,
            reason_unresolved="Lifecycle quarter has no cited product-level quarterly value yet.",
            gold_notes="Lifecycle coverage gap pending historical exhibit/IR retrieval.",
        )
    return sorted(keep.values(), key=lambda row: row["period"])


def promote_lifecycle_history(edge: dict) -> dict | None:
    """Turn a previously window-excluded USD issuer row into gold revenue."""

    if edge.get("case_type") != "old_record":
        return None
    candidate = dict(edge.get("candidate") or {})
    if (candidate.get("currency") or "USD").upper() != "USD":
        return None
    quote = candidate.get("source_quote") or ""
    value = candidate.get("value_reported")
    if not quote_contains_value(quote, value):
        return None
    row = {
        **candidate,
        "drug_name": edge["target_drug"],
        "generic_name": edge.get("generic_name"),
        "source_url": edge["source_url"],
        "source_title": edge.get("source_title"),
        "source_type": candidate.get("source_type") or "company_ir",
        "extraction_method": "manual_verified_lifecycle_search",
        "confidence_score": 1.0,
        "validation_status": "confirmed",
        "metric": candidate.get("metric") or "revenue",
        "gold_notes": (
            (edge.get("edge_notes") or "")
            + " Promoted from window-excluded history; analog peak sales needs the full commercial life."
        ).strip(),
    }
    row["gold_id"] = slug(
        row["drug_name"],
        row["period"],
        row.get("revenue_scope"),
        row.get("geography"),
        row.get("formulation"),
    )
    return row
