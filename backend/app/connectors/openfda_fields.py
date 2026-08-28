from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

MIN_ALIAS_LENGTH = 4


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().casefold()


def openfda_brand_names(result: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for name in (result.get("openfda") or {}).get("brand_name") or []:
        if name and str(name) not in names:
            names.append(str(name))
    for product in result.get("products") or []:
        name = product.get("brand_name")
        if name and str(name) not in names:
            names.append(str(name))
    return names


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
    molecule (a Tyvaso query returns REMODULIN first). Only a brand-name match is
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

    fallback: tuple[dict[str, Any], str] | None = None
    for result in results:
        for brand in openfda_brand_names(result):
            brand_norm = _normalize(brand)
            if brand_norm == generic_norm:
                continue  # an ANDA marketed under the molecule name
            for candidate in candidates:
                if brand_norm == candidate:
                    return result, brand
                if fallback is None and (brand_norm in candidate or candidate in brand_norm):
                    fallback = (result, brand)
    if fallback:
        return fallback
    return None, None


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


def selected_approval_date(
    results: list[dict[str, Any]],
    *,
    product: str,
    generic: str | None = None,
    aliases: Iterable[str] | None = None,
) -> date | None:
    """Earliest approval among applications whose brand matches this product."""

    matching: list[dict[str, Any]] = []
    generic_norm = _normalize(generic or "")
    candidates: list[str] = []
    for name in [product, *(aliases or [])]:
        norm = _normalize(name)
        if not norm or len(norm) < MIN_ALIAS_LENGTH or norm == generic_norm:
            continue
        if norm not in candidates:
            candidates.append(norm)
    for result in results:
        for brand in openfda_brand_names(result):
            brand_norm = _normalize(brand)
            if brand_norm == generic_norm:
                continue
            if brand_norm in candidates:
                matching.append(result)
                break
    if not matching:
        selected, _ = select_openfda_result(
            results, product=product, generic=generic, aliases=aliases
        )
        matching = [selected] if selected else []
    iso, _ = earliest_approval_date(matching)
    if not iso:
        return None
    return date.fromisoformat(iso)
