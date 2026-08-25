from __future__ import annotations

from typing import Any

from app.llm.client import apply_judge_hard_vetoes, re_ytd_language
from app.quality.candidate_filters import quote_mentions_product
from app.quality.checks import quote_contains_value


def try_deterministic_judgment(
    *,
    product: str,
    generic: str | None,
    candidate: dict[str, Any],
    quote: str,
    extra_aliases: list[str] | None = None,
) -> dict[str, Any] | None:
    """Return a judgment without calling the LLM when evidence is clearly good or clearly bad.

    Returns None when the case is ambiguous and an LLM judge should run.
    """
    period_type = (candidate.get("period_type") or "").lower()
    scope = (candidate.get("revenue_scope") or "").strip()
    value = candidate.get("value_reported")

    # Clear vetoes — no need for LLM
    vetoed = apply_judge_hard_vetoes(
        product=product,
        candidate=candidate,
        quote=quote,
        judgment={"support_classification": "supported", "validation_status": "auto_pass", "issues": []},
    )
    if vetoed.get("support_classification") == "misclassified":
        return vetoed

    if period_type in {"ytd", "six_month", "nine_month", "guidance"}:
        return {
            "validation_status": "needs_review",
            "support_classification": "partial",
            "issues": ["deterministic:non_quarterly_period_type"],
            "explanation": "YTD/guidance kept for review without LLM judge",
        }

    if scope == "Company total":
        return {
            "validation_status": "needs_review",
            "support_classification": "misclassified",
            "issues": ["deterministic:company_total_scope"],
            "explanation": "Company total scope is not product revenue",
        }

    mentions = quote_mentions_product(quote, product, generic, extra_aliases=extra_aliases)
    has_value = quote_contains_value(quote, value if value is not None else None)
    if (
        mentions
        and has_value
        and period_type in {"quarterly", "annual"}
        and not re_ytd_language(quote)
        and scope not in {"", "Unknown", "Company total"}
    ):
        return {
            "validation_status": "auto_pass",
            "support_classification": "supported",
            "issues": ["deterministic:product_quote_value_ok"],
            "explanation": "Skipped LLM judge; product+value+period_type look clean",
        }

    return None
