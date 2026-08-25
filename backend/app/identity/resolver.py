from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from app.db.models import EvidenceAssertionORM


def _key(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


@dataclass(frozen=True)
class ResolvedProductIdentity:
    canonical_name: str
    identity_key: str
    analog_family_key: str
    active_ingredients: list[str]
    route_terms: list[str]
    dosage_form: str | None
    delivery_device: str | None


def resolve_product_identity(
    *,
    brand_name: str,
    active_ingredients: Iterable[str],
    dosage_form: str | None,
    route_terms: Iterable[str],
    delivery_device: str | None = None,
) -> ResolvedProductIdentity:
    ingredients = sorted({_key(item) for item in active_ingredients if _key(item)})
    routes = sorted({_key(item) for item in route_terms if _key(item)})
    canonical_name = " ".join(brand_name.strip().split())
    parts = [_key(brand_name), "|".join(ingredients), _key(dosage_form), "|".join(routes), _key(delivery_device)]
    digest = hashlib.sha256("::".join(parts).encode()).hexdigest()[:24]
    return ResolvedProductIdentity(
        canonical_name=canonical_name,
        identity_key=f"{_key(brand_name)}:{digest}",
        analog_family_key="+".join(ingredients) or _key(brand_name),
        active_ingredients=ingredients,
        route_terms=routes,
        dosage_form=dosage_form,
        delivery_device=delivery_device,
    )


def select_assertion(assertions: Iterable[EvidenceAssertionORM]) -> EvidenceAssertionORM | None:
    """Select an assertion while making reviewer confirmation immutable to backfill."""

    method_rank = {"reviewer": 0, "structured_fda": 1, "deterministic_label_rule": 1, "bounded_llm": 2}

    def rank(assertion: EvidenceAssertionORM) -> tuple[int, int, float, str]:
        confirmed_rank = 0 if assertion.validation_status == "confirmed" else 1
        return (
            confirmed_rank,
            method_rank.get(assertion.extraction_method, 3),
            -float(assertion.confidence or 0),
            assertion.id,
        )

    values = list(assertions)
    return min(values, key=rank) if values else None

