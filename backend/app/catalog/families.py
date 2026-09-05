"""Product family relations from the catalog.

A formulation ("Nebulized Tyvaso") is a product of its own in the catalog
and a line of its parent's ("Tyvaso") in an issuer's filings until the
issuer starts splitting the family. The catalog records the relation as the
attribute ``peer_universe_role = formulation_of:<parent>``; nothing here
knows any product by name.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCT_ATTRIBUTES = REPO_ROOT / "seed" / "product_attributes.csv"

_ROLE_PREFIX = "formulation_of:"


@lru_cache(maxsize=1)
def family_parents(path: Path = PRODUCT_ATTRIBUTES) -> dict[str, str]:
    """product -> parent product, for every catalog product that is a formulation."""
    relations: dict[str, str] = {}
    if not path.exists():
        return relations
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            role = (row.get("peer_universe_role") or "").strip()
            name = (row.get("drug_name") or "").strip()
            if name and role.startswith(_ROLE_PREFIX):
                relations[name] = role[len(_ROLE_PREFIX):].strip()
    return relations


def family_parent(product: str) -> str | None:
    """The family line ``product`` is reported under before its own split, if any."""
    wanted = product.strip().lower()
    for child, parent in family_parents().items():
        if child.lower() == wanted:
            return parent
    return None
