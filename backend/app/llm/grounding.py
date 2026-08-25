from __future__ import annotations

import re
from typing import Any


def normalize_for_match(text: str) -> str:
    """Collapse whitespace for forgiving verbatim checks across HTML→text extraction."""
    return re.sub(r"\s+", " ", (text or "")).strip()


def quote_is_verbatim(quote: str, source_text: str, *, min_len: int = 8) -> bool:
    """True if quote appears in source_text (exact or whitespace-normalized)."""
    q = (quote or "").strip()
    if len(q) < min_len:
        # Very short quotes like "Adcirca 6.7" — still require exact or normalized containment
        if not q:
            return False
        if q in (source_text or ""):
            return True
        return normalize_for_match(q) in normalize_for_match(source_text)
    if q in (source_text or ""):
        return True
    return normalize_for_match(q) in normalize_for_match(source_text)


def find_span_for_quote(quote: str, spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    q = (quote or "").strip()
    if not q:
        return None
    for span in spans:
        st = span.get("span_text") or ""
        if quote_is_verbatim(q, st, min_len=1):
            return span
    return None


def enforce_verbatim_on_candidates(
    candidates: list[dict[str, Any]],
    *,
    source_text: str,
    spans: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep only candidates whose source_quote is verbatim in source or their span."""
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    spans = spans or []
    span_by_id = {s.get("span_id"): s for s in spans if s.get("span_id")}

    for cand in candidates:
        quote = (cand.get("source_quote") or "").strip()
        span = None
        sid = cand.get("span_id")
        if sid and sid in span_by_id:
            span = span_by_id[sid]
        if span is None:
            span = find_span_for_quote(quote, spans)

        corpus = (span.get("span_text") if span else None) or source_text
        if not quote_is_verbatim(quote, corpus, min_len=1):
            dropped.append({**cand, "_drop_reason": "quote_not_verbatim"})
            continue
        # Prefer grounding to span text when available
        if span and span.get("span_text"):
            cand = {**cand, "_grounded_span_id": span.get("span_id")}
        kept.append(cand)
    return kept, dropped


def apply_structured_field_gates(
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Honor model-declared product_mentioned_in_quote / is_company_total when present."""
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for cand in candidates:
        if cand.get("is_company_total") is True:
            dropped.append({**cand, "_drop_reason": "model_marked_company_total"})
            continue
        if cand.get("product_mentioned_in_quote") is False:
            scope = (cand.get("revenue_scope") or "").strip()
            if scope != "Company total":
                dropped.append({**cand, "_drop_reason": "model_product_not_in_quote"})
                continue
        kept.append(cand)
    return kept, dropped
