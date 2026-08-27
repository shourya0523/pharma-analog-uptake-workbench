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
    extra: list[str] = []
    for group in (llm_aliases, formulations, parent_companies):
        if group:
            extra.extend(str(x).strip() for x in group if x and str(x).strip())
    return product_aliases(product, generic, extra=extra or None)
