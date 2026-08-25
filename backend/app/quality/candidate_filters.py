from __future__ import annotations

import re
from typing import Any

from app.parsing.evidence import TOTAL_REVENUE_RE, product_aliases


KNOWN_PEER_BRANDS = {
    "tyvaso",
    "remodulin",
    "orenitram",
    "unituxin",
    "adcirca",
    "opsumit",
    "opsynvi",
    "letairis",
    "tracleer",
    "uptravi",
    "veletri",
    "ventavis",
    "winrevair",
    "adempas",
    "yutrepia",
    "revatio",
    "flolan",
    "alyq",
    "tadliq",
    "liqrev",
}


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def quote_mentions_product(
    quote: str,
    product: str,
    generic: str | None = None,
    extra_aliases: list[str] | None = None,
) -> bool:
    q = _normalize(quote)
    for alias in product_aliases(product, generic, extra=extra_aliases):
        if alias.lower() in q:
            return True
    return False


def quote_mentions_generic_only(
    quote: str,
    product: str,
    generic: str | None,
    extra_aliases: list[str] | None = None,
) -> bool:
    if not generic or product.casefold() == generic.casefold():
        return False
    return quote_mentions_product(quote, generic, extra_aliases=extra_aliases) and not quote_mentions_product(
        quote, product, extra_aliases=extra_aliases
    )


def value_is_dosage_not_revenue(quote: str, value: float) -> bool:
    money_context = re.search(r"[$€£]|\b(?:usd|chf|eur|sales?|revenue|million|billion)\b", quote, re.I)
    value_text = re.escape(f"{value:g}")
    dose_context = re.search(rf"(?<![\d.]){value_text}\s*(?:mcg|mg|g|ml|%)\b", quote, re.I)
    return bool(dose_context and not money_context)


def quote_mentions_other_brand(
    quote: str,
    product: str,
    generic: str | None = None,
    extra_aliases: list[str] | None = None,
) -> str | None:
    q = _normalize(quote)
    own = {a.lower() for a in product_aliases(product, generic, extra=extra_aliases)}
    for brand in KNOWN_PEER_BRANDS:
        if brand in own:
            continue
        if re.search(rf"\b{re.escape(brand)}\b", q):
            # Allow if own product also present (combined sentence) — caller decides
            return brand
    return None


def is_xbrl_noise_quote(quote: str) -> bool:
    q = quote or ""
    if re.search(r"\b\w+:\w+Member\b", q):
        return True
    if re.fullmatch(r"[\w:.\-]+", q.strip()) and ":" in q and len(q) < 80:
        return True
    return False


def filter_revenue_candidates(
    candidates: list[dict[str, Any]],
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: list[str] | None = None,
    source_text: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (kept, dropped) with drop reasons.

    Hard rules:
    - Drop XBRL taxonomy noise quotes
    - Drop value==0 unless quote clearly supports zero for the product
    - Company-total language must be scoped Company total (else reclassify or drop if claiming product)
    - Drop other-brand-only quotes
    - Product-family / formulation scopes require product mention in quote
    - Optional: quote must be verbatim in source_text
    """
    from app.llm.grounding import quote_is_verbatim

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    for cand in candidates:
        quote = (cand.get("source_quote") or "").strip()
        scope = (cand.get("revenue_scope") or "Unknown").strip()
        value = cand.get("value_reported")

        if not quote:
            dropped.append({**cand, "_drop_reason": "missing_quote"})
            continue

        if source_text is not None and not quote_is_verbatim(quote, source_text, min_len=1):
            dropped.append({**cand, "_drop_reason": "quote_not_verbatim"})
            continue

        if is_xbrl_noise_quote(quote):
            dropped.append({**cand, "_drop_reason": "xbrl_taxonomy_noise"})
            continue

        if cand.get("is_company_total") is True:
            dropped.append({**cand, "_drop_reason": "model_marked_company_total"})
            continue

        mentions_product = quote_mentions_product(quote, product, generic, extra_aliases=extra_aliases)
        if cand.get("product_mentioned_in_quote") is False and not mentions_product:
            dropped.append({**cand, "_drop_reason": "model_product_not_in_quote"})
            continue

        if quote_mentions_generic_only(quote, product, generic, extra_aliases=extra_aliases):
            dropped.append({**cand, "_drop_reason": "generic_only_not_brand"})
            continue

        other = quote_mentions_other_brand(quote, product, generic, extra_aliases=extra_aliases)

        if other and not mentions_product:
            dropped.append({**cand, "_drop_reason": f"other_brand:{other}"})
            continue

        if TOTAL_REVENUE_RE.search(quote) and not mentions_product:
            dropped.append({**cand, "_drop_reason": "company_total_not_product"})
            continue

        product_scopes = {
            "Product family",
            "Formulation-specific",
            "Franchise",
            "U.S.",
            "ex-U.S.",
            "Worldwide",
            "International",
            "Regional",
            "Unknown",
        }
        if scope in product_scopes and not mentions_product:
            dropped.append({**cand, "_drop_reason": "product_scope_without_product_in_quote"})
            continue

        if scope == "Company total" and not mentions_product:
            dropped.append({**cand, "_drop_reason": "company_total_not_product"})
            continue

        if value is None:
            dropped.append({**cand, "_drop_reason": "missing_value"})
            continue

        try:
            num = float(value)
        except (TypeError, ValueError):
            dropped.append({**cand, "_drop_reason": "non_numeric_value"})
            continue

        if value_is_dosage_not_revenue(quote, num):
            dropped.append({**cand, "_drop_reason": "dose_not_revenue"})
            continue

        if num == 0.0:
            if not mentions_product or not re.search(r"\b(zero|nil|no\s+sales|\$0)\b", quote, re.I):
                dropped.append({**cand, "_drop_reason": "zero_value_unsupported"})
                continue

        kept.append(cand)

    return kept, dropped
