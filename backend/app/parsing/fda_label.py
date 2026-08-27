from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# FDA label sections often arrive as "12.1 Mechanism of Action <prose>".
_MOA_SECTION_HEADER = re.compile(
    r"^\s*\d+(?:\.\d+)*\s+(?:Mechanism of Action|CLINICAL PHARMACOLOGY)\b[:\s]*",
    re.IGNORECASE,
)


def _strings(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in values if item is not None and str(item).strip()]


def _first_section(record: dict[str, Any], key: str) -> str | None:
    values = _strings(record.get(key))
    return "\n".join(values) if values else None


def clean_moa_summary(text: str | None) -> str | None:
    """Strip label section numbering/headers from mechanism prose."""
    if not text:
        return None
    cleaned = _MOA_SECTION_HEADER.sub("", str(text).strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.lstrip(" :.-")
    return cleaned or None


def format_moa_profile_value(moa_terms: list[str], moa_summary: str | None) -> str | None:
    """Prefer cleaned descriptive MoA prose; fall back to structured MoA class terms."""
    summary = clean_moa_summary(moa_summary)
    if summary:
        return summary
    terms = [term.strip() for term in moa_terms if term and str(term).strip()]
    if terms:
        return "; ".join(terms)
    return None


@dataclass(frozen=True)
class ParsedFDALabel:
    brand_names: list[str] = field(default_factory=list)
    generic_names: list[str] = field(default_factory=list)
    active_ingredients: list[str] = field(default_factory=list)
    application_numbers: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    dosage_forms: list[str] = field(default_factory=list)
    epc_terms: list[str] = field(default_factory=list)
    moa_terms: list[str] = field(default_factory=list)
    moa_summary: str | None = None
    indications_text: str | None = None


def parse_label_record(record: dict[str, Any]) -> ParsedFDALabel:
    """Parse structured label fields without conflating EPC and MoA."""

    openfda = record.get("openfda") or {}
    return ParsedFDALabel(
        brand_names=_strings(openfda.get("brand_name")),
        generic_names=_strings(openfda.get("generic_name")),
        active_ingredients=_strings(openfda.get("substance_name")),
        application_numbers=_strings(openfda.get("application_number")),
        routes=_strings(openfda.get("route")),
        dosage_forms=_strings(openfda.get("dosage_form")),
        epc_terms=_strings(openfda.get("pharm_class_epc")),
        moa_terms=_strings(openfda.get("pharm_class_moa")),
        moa_summary=clean_moa_summary(_first_section(record, "mechanism_of_action")),
        indications_text=_first_section(record, "indications_and_usage"),
    )
