from __future__ import annotations

from typing import Any

from app.domain.claims import stated_labels, stated_text
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
    period_type = stated_text(candidate.get("period_type")).lower()
    scope = stated_text(candidate.get("revenue_scope"))
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

    flags = set(stated_labels(candidate.get("label_flags")))
    if "partial_period" in flags:
        # The footnote says the figure covers less than the quarter - the
        # product changed hands inside it. No reading of the quote changes
        # that, so no model is asked.
        return {
            "validation_status": "needs_review",
            "support_classification": "partial",
            "issues": ["deterministic:partial_period"],
            "explanation": "The label's footnote says the figure is for part of the period",
        }
    if "combined_line" in flags:
        # The line combines this product with others, so the figure is the
        # family's. Which part is this product's is not in the quote.
        return {
            "validation_status": "needs_review",
            "support_classification": "partial",
            "issues": ["deterministic:combined_line"],
            "explanation": "The row combines this product with others; the figure is the family's",
        }
    if "label_not_understood" in flags:
        # The reader could not account for every word of the label; the judge
        # is asked, with the residue, and a person decides.
        return None

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
