"""Guards for product profile fields.

Extraction models answer "not specified" rather than omitting a field, and those
strings were being stored as though they were sourced values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

MISSING_VALUES = {
    "",
    "-",
    "--",
    "n/a",
    "na",
    "none",
    "null",
    "nil",
    "tbd",
    "unknown",
    "not specified",
    "unspecified",
    "not available",
    "not applicable",
    "not disclosed",
    "not reported",
    "not stated",
    "not found",
    "no data",
    "not provided",
    "not mentioned",
}


def is_missing_value(value: object) -> bool:
    """True when a field value carries no information and should not be stored."""
    if value is None:
        return True
    text = re.sub(r"\s+", " ", str(value)).strip().strip(".").casefold()
    return text in MISSING_VALUES


def normalize_value(value: object) -> str:
    return re.sub(r"[\s.]+$", "", re.sub(r"\s+", " ", str(value or ""))).strip().casefold()


def values_conflict(left: object, right: object) -> bool:
    """True when two source values for one field genuinely disagree.

    Casing and whitespace differences ("TREPROSTINIL" vs "treprostinil") are the
    same answer from two sources, not a conflict worth adjudicating.
    """
    if is_missing_value(left) or is_missing_value(right):
        return False
    a, b = normalize_value(left), normalize_value(right)
    if not a or not b or a == b:
        return False
    return not (a in b or b in a)


# Judged first when present; every other content field is still judged after these.
PRIORITY_JUDGE_FIELDS = (
    "roa",
    "dosage_form",
    "fda_approval_date",
    "moa",
    "pharmacologic_class",
    "formulation",
    "indication",
    "therapeutic_area",
)

# Internal / non-clinical payload fields — not product claims to challenge.
SKIP_JUDGE_FIELDS = frozenset({"llm_aliases"})


def select_profile_fields_for_judgment(
    rows: list[Any],
    *,
    max_fields: int | None = None,
) -> list[Any]:
    """Return every judgable profile field, conflicts and priority fields first.

    ``max_fields`` <= 0 or None means no cap: judge the full profile.
    """
    eligible = [
        row
        for row in rows
        if getattr(row, "field", None)
        and row.field not in SKIP_JUDGE_FIELDS
        and not is_missing_value(getattr(row, "value", None))
    ]
    ordered = sorted(
        eligible,
        key=lambda r: (
            0 if (getattr(r, "citation_json", None) or {}).get("conflicting_source") else 1,
            PRIORITY_JUDGE_FIELDS.index(r.field) if r.field in PRIORITY_JUDGE_FIELDS else 99,
            r.field,
        ),
    )
    if max_fields is None or max_fields <= 0:
        return ordered
    return ordered[:max_fields]


@dataclass
class ProfileJudgment:
    """Outcome of judging one profile field against independent search."""

    value: str
    verdict: str
    corrected: bool = False
    flags: list[str] = dataclass_field(default_factory=list)
    correction: dict[str, Any] | None = None


def apply_profile_judgment(
    field: str,
    current_value: str,
    judgment: dict[str, Any] | None,
    *,
    min_confidence: float = 0.6,
) -> ProfileJudgment:
    """Fold a search judgment into a field value.

    A correction is accepted only when the judge contradicts the stated value AND
    cites a URL with a verbatim quote, matching the rule that every source-derived
    field carries a citation. Anything weaker leaves the value alone and is
    recorded for review.
    """
    if not judgment:
        return ProfileJudgment(value=current_value, verdict="unjudged")

    verdict = str(judgment.get("verdict") or "inconclusive").strip().lower()
    if verdict not in {"supported", "contradicted", "inconclusive"}:
        verdict = "inconclusive"

    if verdict == "supported":
        return ProfileJudgment(value=current_value, verdict=verdict, flags=["search_corroborated"])

    if verdict != "contradicted":
        return ProfileJudgment(value=current_value, verdict=verdict, flags=["search_inconclusive"])

    corrected_value = judgment.get("corrected_value")
    url = str(judgment.get("source_url") or "").strip()
    quote = str(judgment.get("source_quote") or "").strip()
    try:
        confidence = float(judgment.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0

    if is_missing_value(corrected_value):
        return ProfileJudgment(
            value=current_value, verdict=verdict, flags=["contradicted_without_replacement"]
        )
    if not url.startswith("http") or not quote:
        # A correction without a citation cannot be stored as a sourced value
        return ProfileJudgment(
            value=current_value, verdict=verdict, flags=["correction_missing_citation"]
        )
    if confidence < min_confidence:
        return ProfileJudgment(
            value=current_value, verdict=verdict, flags=["correction_low_confidence"]
        )
    if not values_conflict(current_value, corrected_value):
        return ProfileJudgment(value=current_value, verdict="supported", flags=["search_corroborated"])

    return ProfileJudgment(
        value=str(corrected_value).strip(),
        verdict=verdict,
        corrected=True,
        flags=["corrected_by_search"],
        correction={
            "superseded_value": current_value,
            "source_url": url,
            "source_quote": quote,
            "confidence": confidence,
            "explanation": judgment.get("explanation"),
        },
    )
