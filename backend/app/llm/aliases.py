from __future__ import annotations

from collections.abc import Iterable

from app.parsing.evidence import product_aliases


def merge_aliases(
    product: str,
    generic: str | None = None,
    *,
    llm_aliases: Iterable[str] | None = None,
    formulations: Iterable[str] | None = None,
    parent_companies: Iterable[str] | None = None,
) -> list[str]:
    """Spellings of the product for row/quote matching.

    Formulations and parent companies are accepted so callers can keep passing
    the expander's full payload, but they are not match aliases: a parent in
    the list makes every corporate loss line read as product revenue, and a
    formulation spelling makes a foam/cream slice read as the brand total.
    Those groups stay on the stored expansion for retrieval and search.
    """
    del formulations, parent_companies
    extra = [
        str(x).strip() for x in (llm_aliases or ()) if x and str(x).strip()
    ]
    return product_aliases(product, generic, extra=extra or None)
