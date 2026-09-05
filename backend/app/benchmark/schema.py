"""One row shape for gold rows and pipeline rows, so they can be compared.

The gold dataset and the pipeline describe the same fact - a product's
revenue in a period, in a currency, over a geography - with different field
names, different units and different vocabularies. Rather than compare them
field by field with ad hoc translation, both are reduced to this one shape
and compared under rules stated here, once:

* ``period`` is the calendar quarter or year (``2019Q3``, ``2019``);
* ``value_usd_millions`` is the comparable number; ``value_as_reported`` and
  ``unit`` keep what the document printed;
* ``geography`` is one of ``worldwide``, ``united_states``, ``international``,
  ``other`` or ``unspecified``. Gold always states one. The pipeline states
  one when the document did; ``unspecified`` means the document printed the
  product's figure with no geography at all, which matches whatever
  geography gold assigned to that product's reported line;
* ``route`` says how the number was reached - read from a page, derived by
  arithmetic, assembled from dated parts, or attributed from a family line -
  and ``derivation`` names the specific step, so the two sides can be
  compared on provenance as well as on value.

Two rows match when product and period agree, geographies are compatible,
and the values agree to the precision the gold row carries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.extraction.series import SeriesValue

GEOGRAPHIES = ("worldwide", "united_states", "international", "other", "unspecified")

_GEOGRAPHY_ALIASES = {
    "worldwide": "worldwide",
    "ww": "worldwide",
    "global": "worldwide",
    "total": "worldwide",
    "united states": "united_states",
    "u.s.": "united_states",
    "us": "united_states",
    "international": "international",
    "intl": "international",
    "ex-u.s.": "international",
}

# Comparison tolerances. Gold values are stated to at most three decimals of
# a million (thousands-based filings); anything closer than half a thousand
# dollars is the same number.
ABSOLUTE_TOLERANCE = 0.0006
RELATIVE_TOLERANCE = 1e-6


def canonical_geography(label: str | None) -> str:
    if not label:
        return "unspecified"
    key = label.strip().lower()
    if key in _GEOGRAPHY_ALIASES:
        return _GEOGRAPHY_ALIASES[key]
    return "other"


@dataclass(frozen=True)
class ComparableRevenueRow:
    product: str
    period: str
    period_type: str
    geography: str
    value_usd_millions: float
    value_as_reported: float | None
    unit: str | None
    currency: str
    route: str
    derivation: str
    source_urls: tuple[str, ...]
    source_quote: str
    origin: str                                  # "gold" | "pipeline"
    status: str = "resolved"
    detail: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return self.product, self.period

    def as_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "period": self.period,
            "period_type": self.period_type,
            "geography": self.geography,
            "value_usd_millions": self.value_usd_millions,
            "value_as_reported": self.value_as_reported,
            "unit": self.unit,
            "currency": self.currency,
            "route": self.route,
            "derivation": self.derivation,
            "source_urls": list(self.source_urls),
            "source_quote": self.source_quote,
            "origin": self.origin,
            "status": self.status,
            "detail": self.detail,
        }


_GOLD_ROUTES = {
    "direct_reported": "read",
    "direct_reported_rounded": "read",
    "direct_prior_year_column": "read",
    "direct_prior_year_schedule": "read",
    "direct_retrospective_table": "read",
    "direct_jnj_retrospective_table": "read",
    "annual_less_reported_first_nine_months": "derived",
    "full_year_less_other_reported_quarters": "derived",
    "year_to_date_less_reported_quarters": "derived",
    "identity_normalization_pre_dpi": "propagated",
    "acquisition_bridge_sum": "bridged",
}


def from_gold(row: dict[str, Any]) -> ComparableRevenueRow:
    """A gold quarterly or annual row in the common shape."""
    urls = tuple(s["source_url"] for s in row.get("sources") or [{"source_url": row["source_url"]}])
    value = row.get("value_normalized_usd_millions")
    if value is None:
        value = row["value_reported"]
    return ComparableRevenueRow(
        product=row["drug_name"],
        period=str(row["period"]),
        period_type=row.get("period_type", "quarterly"),
        geography=canonical_geography(row.get("geography")),
        value_usd_millions=float(value),
        value_as_reported=row.get("source_value_reported", row.get("value_reported")),
        unit=row.get("source_unit") or row.get("unit"),
        currency=row.get("currency", "USD"),
        route=_GOLD_ROUTES.get(row.get("derivation", ""), "read"),
        derivation=row.get("derivation", ""),
        source_urls=urls,
        source_quote=row.get("source_quote", ""),
        origin="gold",
        extras={"revenue_scope": row.get("revenue_scope"), "gold_id": row.get("gold_id")},
    )


def from_series(value: SeriesValue) -> ComparableRevenueRow:
    """A pipeline series value in the common shape."""
    return ComparableRevenueRow(
        product=value.product,
        period=value.period,
        period_type=value.period_type,
        geography=canonical_geography(value.geography),
        value_usd_millions=float(value.value_usd_millions),
        value_as_reported=value.value_as_reported,
        unit=value.unit_label,
        currency=value.currency,
        route=value.route,
        derivation=value.derivation,
        source_urls=value.source_urls,
        source_quote=value.source_quote,
        origin="pipeline",
        status=value.status,
        detail=value.detail,
        extras={"normalization": value.normalization, "inputs": list(value.inputs)},
    )


def geographies_compatible(gold: str, pipeline: str) -> bool:
    return gold == pipeline or pipeline == "unspecified"


def values_match(gold: float, pipeline: float) -> bool:
    return abs(gold - pipeline) <= max(ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE * abs(gold))


@dataclass(frozen=True)
class Comparison:
    gold: ComparableRevenueRow
    pipeline: ComparableRevenueRow | None
    outcome: str          # match | value_mismatch | geography_mismatch | needs_review | missing
    detail: str = ""


def compare(gold: ComparableRevenueRow, candidates: list[ComparableRevenueRow]) -> Comparison:
    """The gold row against every pipeline row for the same product and period."""
    same = [c for c in candidates if c.key == gold.key and c.period_type == gold.period_type]
    if not same:
        return Comparison(gold, None, "missing", "no pipeline row for this period")
    compatible = [c for c in same if geographies_compatible(gold.geography, c.geography)]
    if not compatible:
        offered = ", ".join(sorted({c.geography for c in same}))
        return Comparison(gold, same[0], "geography_mismatch", f"gold {gold.geography}; pipeline {offered}")
    # Prefer the exact geography, then the resolved rows.
    compatible.sort(key=lambda c: (c.geography != gold.geography, c.status != "resolved"))
    resolved = [c for c in compatible if c.status == "resolved"]
    if not resolved:
        return Comparison(gold, compatible[0], "needs_review", compatible[0].detail)
    for candidate in resolved:
        if values_match(gold.value_usd_millions, candidate.value_usd_millions):
            return Comparison(gold, candidate, "match")
    best = resolved[0]
    return Comparison(
        gold, best, "value_mismatch",
        f"gold {gold.value_usd_millions:g} vs pipeline {best.value_usd_millions:g} ({best.route}:{best.derivation})",
    )
