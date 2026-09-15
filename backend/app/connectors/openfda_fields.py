from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

MIN_ALIAS_LENGTH = 4


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().casefold()


def openfda_brand_names(result: dict[str, Any]) -> list[str]:
    return [str(name) for name in (result.get("openfda", {}).get("brand_name") or []) if name]


def select_openfda_result(
    results: list[dict[str, Any]],
    *,
    product: str,
    generic: str | None = None,
    aliases: Iterable[str] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Pick the drugsFDA application that is actually this product.

    A search on brand OR generic name returns every application sharing the
    molecule, so the first result is frequently a different product from the same
    molecule - a query for one brand returns a competitor's brand ahead of it.
    Only a brand-name match is
    accepted; the generic name is deliberately excluded from matching because it
    is shared with competitor and ANDA products.

    Returns (result, matched_brand_name), or (None, None) when no application
    matches this product's brand.
    """
    generic_norm = _normalize(generic or "")
    candidates: list[str] = []
    for name in [product, *(aliases or [])]:
        norm = _normalize(name)
        if not norm or len(norm) < MIN_ALIAS_LENGTH or norm == generic_norm:
            continue
        if norm not in candidates:
            candidates.append(norm)
    if not candidates:
        return None, None

    # How good a match is, best first. The product's own name outranks an
    # alias: an alias list holds sibling formulations, and a brand that is one
    # of them exactly - Nebulized Calderon for a job asking about Calderon -
    # would otherwise be returned ahead of the application actually named,
    # taking its approval date, its route and its label sections with it.
    OWN_NAME, ALIAS, PARTIAL = 0, 1, 2

    ranked: list[tuple[int, int, dict[str, Any], str]] = []
    for order, result in enumerate(results):
        for brand in openfda_brand_names(result):
            brand_norm = _normalize(brand)
            if brand_norm == generic_norm:
                continue  # an ANDA marketed under the molecule name
            for index, candidate in enumerate(candidates):
                if brand_norm == candidate:
                    rank = OWN_NAME if index == 0 else ALIAS
                elif brand_norm in candidate or candidate in brand_norm:
                    rank = PARTIAL
                else:
                    continue
                ranked.append((rank, order, result, brand))
                break
    if not ranked:
        return None, None
    ranked.sort(key=lambda item: (item[0], item[1]))
    return ranked[0][2], ranked[0][3]


def select_openfda_applications(
    results: list[dict[str, Any]],
    *,
    product: str,
    generic: str | None = None,
    aliases: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Every application that is this same product, best match first.

    One product routinely holds more than one application - a capsule and a
    tablet of Calderon are two, filed years apart under the one brand - and the
    product's first approval is the earliest of them. Selecting a single
    application and reading the approval date off it returns whichever of them
    the search happened to put first, which for a product reformulated later is
    the reformulation's date.

    So this returns the applications matching as well as the best one does, and
    no worse ones: the sibling formulations of the product asked about, and not
    the different product that merely shares its molecule.
    """
    best, _ = select_openfda_result(
        results, product=product, generic=generic, aliases=aliases
    )
    if best is None:
        return []
    chosen = [
        result
        for result in results
        if select_openfda_result(
            [result], product=product, generic=generic, aliases=aliases
        )[0]
        is not None
    ]
    best_brands = {_normalize(name) for name in openfda_brand_names(best)}
    return [
        result
        for result in chosen
        if best_brands & {_normalize(name) for name in openfda_brand_names(result)}
    ] or [best]


def parse_openfda_date(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    try:
        datetime.fromisoformat(s)
        return s[:10]
    except ValueError:
        return None


def earliest_approval_date(results: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Return (iso_date, source_field) from OpenFDA drugsFDA submissions.

    Prefers earliest ORIG submission with AP status; falls back to earliest AP date.
    """
    dates: list[tuple[str, str]] = []
    for result in results:
        for sub in result.get("submissions") or []:
            status = (sub.get("submission_status") or "").upper()
            if status and status != "AP":
                continue
            parsed = parse_openfda_date(sub.get("submission_status_date"))
            if not parsed:
                continue
            stype = (sub.get("submission_type") or "").upper()
            field = f"submissions[type={stype or 'UNK'}].submission_status_date"
            dates.append((parsed, field if stype == "ORIG" else f"fallback:{field}"))

    orig = [(d, f) for d, f in dates if not f.startswith("fallback:")]
    pool = orig or [(d, f.removeprefix("fallback:")) for d, f in dates]
    if not pool:
        return None, None
    pool.sort(key=lambda x: x[0])
    return pool[0]
