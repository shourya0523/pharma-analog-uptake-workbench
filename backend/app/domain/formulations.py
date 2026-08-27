"""Formulation field helpers: single, multi-value, or aggregate."""

from __future__ import annotations

import re
from typing import Any

FORMULATION_SEP = "; "
AGGREGATE_FORMULATION = "aggregate"


def parse_formulations(raw: Any) -> list[str]:
    """Split a formulation field into ordered unique parts.

    Accepts:
    - None / "" → []
    - "aggregate" → ["aggregate"]
    - "DPI; nebulized" → ["DPI", "nebulized"]
    - ["DPI", "nebulized"] → same
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        parts = [str(p).strip() for p in raw if str(p).strip()]
    else:
        text = str(raw).strip()
        if not text:
            return []
        if text.casefold() == AGGREGATE_FORMULATION:
            return [AGGREGATE_FORMULATION]
        parts = [p.strip() for p in re.split(r"\s*;\s*", text) if p.strip()]

    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = part.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(AGGREGATE_FORMULATION if key == AGGREGATE_FORMULATION else part)
    return out


def format_formulations(parts: list[str] | None) -> str | None:
    """Serialize formulation parts for storage on datapoint.formulation."""
    cleaned = parse_formulations(parts)
    if not cleaned:
        return None
    if cleaned == [AGGREGATE_FORMULATION]:
        return AGGREGATE_FORMULATION
    return FORMULATION_SEP.join(cleaned)


def is_aggregate_formulation(raw: Any) -> bool:
    parts = parse_formulations(raw)
    return parts == [AGGREGATE_FORMULATION] or len(parts) > 1


def coerce_formulation_value(raw: Any) -> str | None:
    """Normalize extractor/enrichment input to a stored formulation string."""
    if raw is None:
        return None
    if isinstance(raw, list):
        return format_formulations(raw)
    text = str(raw).strip()
    if not text:
        return None
    if text.casefold() == AGGREGATE_FORMULATION:
        return AGGREGATE_FORMULATION
    return format_formulations(parse_formulations(text))
