"""Which fetched documents are worth a model call.

Reading tables is free, so every fetched document is parsed. A model call
is asked only of a document the sketch has something to show for: a grid
naming a product, or a passage naming one beside a money amount. This
fails open - a false positive costs one call, a false negative a series -
and it decides nothing about what a document means.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import ParsedDocument
from app.fingerprint.llm import sketch_document


@dataclass(frozen=True)
class Triage:
    fingerprint: bool
    reason: str
    parts: int
    grids: int
    chars: int


def triage(doc: ParsedDocument, *, aliases: list[str]) -> Triage:
    if doc is None or doc.parsing_status.value != "success":
        return Triage(False, "not_parsed", 0, 0, 0)
    parts = sketch_document(doc, aliases=aliases)
    if not parts:
        return Triage(False, "no_product_mention_near_a_figure", 0, 0, len(doc.full_text))
    grids = sum(len(p.grid_indexes) for p in parts)
    return Triage(True, "product_named", len(parts), grids, len(doc.full_text))
