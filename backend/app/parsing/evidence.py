from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.domain.models import ParsedDocument, RetrievedSource, SourceType

MONEY_RE = re.compile(
    r"("
    r"\$\s*[\d,]+(?:\.\d+)?"
    r"|\b[\d,]+\.\d+\s*(?:million|billion)"
    r"|\b[\d,]+\s*(?:million|billion)"
    r"|\b\d{1,3}(?:,\d{3})+\.\d+"
    r"|\b\d{2,4}\.\d\b"
    r")",
    re.IGNORECASE,
)
REVENUE_HINT_RE = re.compile(
    r"(net\s+product\s+sales|product\s+sales|net\s+sales|revenues?|sales)",
    re.IGNORECASE,
)
TOTAL_REVENUE_RE = re.compile(r"\btotal\s+revenues?\b", re.IGNORECASE)

FILING_PRIORITY = {
    "10-K": 0,
    "20-F": 0,
    "40-F": 0,
    "10-Q": 1,
    "6-K": 2,
    "8-K": 3,
}


def product_aliases(product: str, generic: str | None = None, extra: Iterable[str] | None = None) -> list[str]:
    names: list[str] = []
    for raw in [product, generic, *(extra or [])]:
        if not raw:
            continue
        cleaned = raw.strip()
        if cleaned and cleaned not in names:
            names.append(cleaned)
        # Split franchise-style names: "OPSUMIT (macitentan)/OPSYNVI"
        for part in re.split(r"[/|,;]+", cleaned):
            part = re.sub(r"\(.*?\)", "", part).strip()
            if part and part not in names:
                names.append(part)
    # Deduplicate case-insensitively while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        key = n.lower()
        if key in seen or len(n) < 3:
            continue
        seen.add(key)
        out.append(n)
    return out


def _alias_pattern(aliases: list[str]) -> re.Pattern[str] | None:
    if not aliases:
        return None
    parts = [re.escape(a) for a in sorted(aliases, key=len, reverse=True)]
    return re.compile(r"(" + "|".join(parts) + r")", re.IGNORECASE)


def select_product_evidence_text(
    text: str,
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
    max_chars: int = 45000,
    window: int = 900,
    max_windows: int = 28,
) -> tuple[str, dict[str, Any]]:
    """Build LLM context from product-anchored windows instead of file head.

    Returns (selected_text, meta) where meta.had_product_money_hits indicates
    whether any window contained both product alias and money language.
    """
    aliases = product_aliases(product, generic, extra=extra_aliases)
    alias_re = _alias_pattern(aliases)
    meta: dict[str, Any] = {
        "aliases": aliases,
        "had_product_money_hits": False,
        "window_count": 0,
        "strategy": "product_windows",
    }
    if not text.strip():
        meta["strategy"] = "empty"
        return "", meta

    if not alias_re:
        meta["strategy"] = "head_fallback"
        return text[:max_chars], meta

    scored: list[tuple[int, int, int, str]] = []  # score, start, end, snippet
    for match in alias_re.finditer(text):
        start = max(0, match.start() - window)
        end = min(len(text), match.end() + window)
        snippet = text[start:end]
        score = 1
        if MONEY_RE.search(snippet):
            score += 5
            meta["had_product_money_hits"] = True
        if REVENUE_HINT_RE.search(snippet):
            score += 3
        if TOTAL_REVENUE_RE.search(snippet) and not MONEY_RE.search(snippet[max(0, match.start() - start - 40) : match.end() - start + 80]):
            score -= 1
        scored.append((score, start, end, snippet))

    if not scored:
        # No product mention — still allow limited head for company context, but flag
        meta["strategy"] = "no_product_mention"
        return text[: min(8000, max_chars)], meta

    scored.sort(key=lambda x: (-x[0], x[1]))
    selected: list[tuple[int, int, str]] = []
    used_spans: list[tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        for s, e in used_spans:
            if a < e and b > s and min(b, e) - max(a, s) > window // 2:
                return True
        return False

    for score, start, end, snippet in scored:
        if len(selected) >= max_windows:
            break
        if overlaps(start, end):
            continue
        selected.append((start, end, snippet))
        used_spans.append((start, end))

    selected.sort(key=lambda x: x[0])
    parts = [f"[excerpt @{s}-{e}]\n{snip}" for s, e, snip in selected]
    # Prefer money-bearing revenue tables when present elsewhere near aliases already covered
    joined = "\n\n---\n\n".join(parts)
    meta["window_count"] = len(selected)
    if len(joined) > max_chars:
        joined = joined[:max_chars]
    return joined, meta


def format_tables_for_llm(
    tables: list[list[list[str]]],
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
    max_chars: int = 12000,
) -> str:
    aliases = [a.lower() for a in product_aliases(product, generic, extra=extra_aliases)]
    blocks: list[str] = []
    for i, table in enumerate(tables):
        flat = " | ".join(" ".join(cell for cell in row) for row in table)
        # Keep short money tables anyway (often product rows nearby).
        if aliases and not any(a in flat.lower() for a in aliases) and not MONEY_RE.search(flat):
            continue
        rendered = "\n".join("\t".join(cell for cell in row) for row in table[:40])
        blocks.append(f"[table {i}]\n{rendered}")
    out = "\n\n".join(blocks)
    return out[:max_chars]


def prioritize_sources_for_revenue(
    sources: list[RetrievedSource],
    parsed: dict[str, Any],
    *,
    max_sources: int = 4,
) -> list[RetrievedSource]:
    """Prefer 10-K/10-Q with parse success; deprioritize empty 8-Ks."""

    def score(src: RetrievedSource) -> tuple:
        doc = parsed.get(src.source_id)
        parse_ok = bool(doc and getattr(doc, "parsing_status", None) and doc.parsing_status.value == "success")
        text_len = len(doc.full_text) if doc and parse_ok else 0
        if src.source_type == SourceType.OPENFDA:
            return (99, 0, 0)  # skip for revenue
        if src.source_type == SourceType.LLM_SEARCH:
            return (2 if parse_ok else 3, 4, -text_len)
        filing = (src.filing_type or "").upper()
        pri = FILING_PRIORITY.get(filing, 5)
        # Prefer longer narrative filings
        return (0 if parse_ok else 1, pri, -text_len)

    eligible = [
        s
        for s in sources
        if s.source_type != SourceType.OPENFDA
        and s.retrieval_status.value in {"success", "partial"}
    ]
    eligible.sort(key=score)
    # Drop 8-Ks if we already have enough 10-K/10-Q
    primary = [s for s in eligible if (s.filing_type or "").upper() in {"10-K", "10-Q", "20-F", "40-F"}]
    if len(primary) >= 2:
        return primary[:max_sources]
    return eligible[:max_sources]


def build_revenue_llm_text(
    doc: ParsedDocument,
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
    max_chars: int = 48000,
) -> tuple[str, dict[str, Any]]:
    body, meta = select_product_evidence_text(
        doc.full_text,
        product=product,
        generic=generic,
        extra_aliases=extra_aliases,
        max_chars=max_chars - 2000,
    )
    tables = format_tables_for_llm(
        doc.tables,
        product=product,
        generic=generic,
        extra_aliases=extra_aliases,
    )
    if tables:
        combined = f"{body}\n\n=== TABLES ===\n{tables}"
        return combined[:max_chars], meta
    return body, meta
