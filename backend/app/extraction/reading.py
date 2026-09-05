"""Reading a document in either mode.

``mode="model"``: the fingerprint description is the only interpreter
(``app.extraction.described``). ``mode="degraded"``: the header grammar and
the regex prose reader, for runs with no model; never scored.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from app.domain.models import ParsedDocument
from app.extraction.columns import ColumnLayout
from app.extraction.degraded.grid_reader import _as_of_coverage, read_grids
from app.extraction.degraded.prose_reader import read_prose
from app.extraction.readers import Observation, ReadReport, _same_amount
from app.parsing.evidence import product_aliases


def read_document(
    doc: ParsedDocument,
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: Iterable[str] | None = None,
    source_url: str = "",
    fingerprint: Any = None,
    issuer_products: Iterable[str] | None = None,
    mode: str = "degraded",
) -> ReadReport:
    """Grid observations plus sentence observations, in one list.

    ``mode="model"``: only the fingerprint's descriptions interpret the
    document; the header grammar and the regex prose reader do not run.
    ``mode="degraded"``: the grammar and regex readers, for runs with no
    model; never scored.

    ``fingerprint`` is an optional ``app.fingerprint.llm.Fingerprint``: its
    grid descriptions become candidate layouts (verified per row) and its
    prose statements become observations only when the quoted sentence is
    in the document and the value is in the sentence.
    """
    if mode == "model":
        from app.extraction.described import read_described_document

        return read_described_document(
            doc, fingerprint, product=product, generic=generic, extra_aliases=extra_aliases, source_url=source_url,
        )
    aliases = product_aliases(product, generic, extra=extra_aliases)
    described: dict[int, list[ColumnLayout]] = defaultdict(list)
    if fingerprint is not None:
        for region in fingerprint.grids_for(product, aliases):
            described[region.grid_index].append(_column_geographies_only(region.layout))
    report = read_grids(
        doc, product=product, generic=generic, extra_aliases=extra_aliases, source_url=source_url,
        described_layouts=dict(described) or None, issuer_products=issuer_products, mode=mode,
    )
    if fingerprint is not None:
        from app.llm.grounding import quote_is_verbatim

        names = {a.lower() for a in aliases}
        for region in fingerprint.prose:
            if (region.product or "").lower() not in names:
                continue
            if not quote_is_verbatim(region.quote, doc.full_text):
                report.skipped.append(f"prose:{region.period}:quote_not_in_document")
                continue
            digits = region.quote.replace(",", "")
            if not re.search(rf"(?<![\d.]){re.escape(f'{region.value:g}')}(?![\d])", digits):
                report.skipped.append(f"prose:{region.period}:value_not_in_quote")
                continue
            report.observations.append(
                Observation(
                    product_label=product,
                    period=region.period,
                    period_type=region.period_type,
                    value_as_reported=region.value,
                    unit_label=region.unit,
                    currency=region.currency,
                    unit_declared=True,
                    geography=region.geography,
                    covers=_as_of_coverage(region.quote, region.period, region.period_type),
                    source_quote=region.quote,
                    method="prose",
                    layout_signature="llm_prose",
                    verified=("quote_in_document", "value_in_quote"),
                    specificity=1 if region.period_from_context else 0,
                    source_url=source_url,
                    source_id=doc.source_id,
                    notes=("llm_fingerprint",),
                )
            )
    for value in [] if mode == "model" else read_prose(doc.full_text, product=product, generic=generic, extra_aliases=extra_aliases):
        covers = _as_of_coverage(value.source_quote, value.period, value.period_type)
        report.observations.append(
            Observation(
                product_label=value.product_label,
                period=value.period,
                period_type=value.period_type,
                value_as_reported=value.value_as_reported,
                unit_label=value.unit_label,
                currency=value.currency,
                unit_declared=True,
                geography=None,
                covers=covers,
                source_quote=value.source_quote,
                method="prose",
                layout_signature="prose",
                verified=("stated_in_sentence",) if value.fingerprint_signature == "prose" else ("period_from_paragraph",),
                specificity=value.specificity,
                source_url=source_url,
                source_id=doc.source_id,
            )
        )
    # A generic "product sales" line stands in for the product only in a
    # document that never names the product on a revenue line of its own,
    # and never contradicts it: a sentence stating the product's own figure
    # for a period the generic line covers at a different value shows the
    # line to be the issuer's whole product revenue, not this product's.
    generic_lines = [o for o in report.observations if "generic_product_line" in o.notes]
    if generic_lines:
        named = [o for o in report.observations if "generic_product_line" not in o.notes and o.line_item == "exact"]
        named_grid = any(o.method == "grid" for o in named)
        contradicted = any(
            o.period == g.period and o.period_type == g.period_type and not o.covers
            and not _same_amount(o, g)
            for g in generic_lines
            for o in named
        )
        if named_grid or contradicted:
            report.observations = [o for o in report.observations if "generic_product_line" not in o.notes]
    return report


def _column_geographies_only(layout: ColumnLayout) -> ColumnLayout:
    """Keep a described geography only where it tells the columns apart.

    A description that puts the same geography on every value column is
    saying what the whole grid covers, which is the row's or the document's
    to state, not a column's; the columns of such a grid have no geography.
    """
    from dataclasses import replace

    value_geographies = {c.geography for c in layout.columns if c.kind == "value"}
    if len(value_geographies) != 1 or None in value_geographies:
        return layout
    columns = tuple(replace(c, geography=None) if c.kind == "value" else c for c in layout.columns)
    return replace(layout, columns=columns)


