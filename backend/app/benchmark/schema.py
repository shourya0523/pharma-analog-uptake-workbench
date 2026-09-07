"""One row shape for gold rows and pipeline rows, so they can be compared.

The gold dataset and the pipeline describe the same fact - a product's
revenue in a period, in a currency, over a geography - with different field
names, different units and different vocabularies. Rather than compare them
field by field with ad hoc translation, both are reduced to this one shape
and compared under rules stated here, once:

* ``period`` is the calendar quarter or year (``2019Q3``, ``2019``);
* ``value_millions`` in the issuer's reporting currency is the comparable number
  (``value_usd_millions`` beside it where a rate is on file); ``value_as_reported`` and
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

import re
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
    "rest of world": "international",
    "outside the united states": "international",
    "other international": "international",
    "europe": "europe",
    "japan": "japan",
    "other": "other",
}

# Words a column header prints after the region to state its unit or
# currency; they say nothing about where.
_UNIT_WORDS = {"usd", "eur", "dkk", "chf", "gbp", "jpy", "$", "€", "£", "¥", "m", "mm", "bn", "k", "in", "million",
               "millions", "billion", "billions", "thousand", "thousands", "units"}

# Comparison tolerances. Gold values are stated to at most three decimals of
# a million (thousands-based filings); anything closer than half a thousand
# dollars is the same number.
ABSOLUTE_TOLERANCE = 0.0006
RELATIVE_TOLERANCE = 1e-6


def canonical_geography(label: str | None) -> str:
    """The canonical geography a printed label names.

    A column header carries more than the region ("Rest of world USD m",
    "U.S. (in millions)"); the longest alias the label starts with, as a
    whole word, is the region it names.
    """
    if not label:
        return "unspecified"
    key = " ".join(re.sub(r"\([^)]*\)", " ", label.strip().lower()).split())
    words = key.split()
    while words and words[-1] in _UNIT_WORDS:
        words.pop()
    key = " ".join(words)
    if key in _GEOGRAPHY_ALIASES:
        return _GEOGRAPHY_ALIASES[key]
    return "other"


@dataclass(frozen=True)
class ComparableRevenueRow:
    product: str
    period: str
    period_type: str
    geography: str
    value_millions: float                        # in ``currency``, the issuer's reporting currency
    value_as_reported: float | None
    unit: str | None
    currency: str
    route: str
    derivation: str
    source_urls: tuple[str, ...]
    source_quote: str
    origin: str                                  # "gold" | "pipeline"
    value_usd_millions: float | None = None      # at the year's average rate, when a rate is on file
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
            "value_millions": self.value_millions,
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
    scale = {"units": 1e-6, "thousands": 1e-3, "millions": 1.0, "billions": 1e3}.get((row.get("unit") or "millions").lower(), 1.0)
    usd = row.get("value_normalized_usd_millions")
    return ComparableRevenueRow(
        product=row["drug_name"],
        period=str(row["period"]),
        period_type=row.get("period_type", "quarterly"),
        geography=canonical_geography(row.get("geography")),
        value_millions=float(row["value_reported"]) * scale,
        value_usd_millions=float(usd) if usd is not None else None,
        value_as_reported=row.get("source_value_reported", row.get("value_reported")),
        unit=row.get("source_unit") or row.get("unit"),
        currency=row.get("currency", "USD"),
        route=_GOLD_ROUTES.get(row.get("derivation", ""), "read"),
        derivation=row.get("derivation", ""),
        source_urls=urls,
        source_quote=row.get("source_quote", ""),
        origin="gold",
        extras={"revenue_scope": row.get("revenue_scope"), "gold_id": row.get("gold_id"),
                "geography_label": row.get("geography")},
    )


def _pipeline_geography(value: SeriesValue) -> str:
    """The canonical geography of a series value; an "Other" region is named by its printed label.

    The contract keeps "International" for a line covering everything outside
    the United States and files named regions under Europe, Japan or Other
    with the printed label beside them. The reference is keyed the same way
    from its printed labels, so "Rest of World" read as Other compares as the
    reference's international row and "Europe and rest of world" as other.
    """
    if (value.geography or "").lower().startswith("other") and value.geography_label:
        return canonical_geography(value.geography_label)
    return canonical_geography(value.geography)


def from_series(value: SeriesValue) -> ComparableRevenueRow:
    """A pipeline series value in the common shape."""
    return ComparableRevenueRow(
        product=value.product,
        period=value.period,
        period_type=value.period_type,
        geography=_pipeline_geography(value),
        value_millions=float(value.value_millions),
        value_usd_millions=value.value_usd_millions,
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
        extras={"normalization": value.normalization, "inputs": list(value.inputs), "alternates": list(value.alternates),
                "provisional": value.provisional, "geography_label": value.geography_label},
    )


def geographies_compatible(gold: str, pipeline: str) -> bool:
    return gold == pipeline or pipeline == "unspecified"


def _label_key(label: Any) -> str:
    """"Europe & Rest of World" and "Europe and rest of world" name one region."""
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\band\b", "", str(label or "").lower()))


def _same_printed_region(gold: ComparableRevenueRow, pipeline: ComparableRevenueRow) -> bool:
    """Both rows carry a printed region label and it is the same region."""
    a, b = _label_key(gold.extras.get("geography_label")), _label_key(pipeline.extras.get("geography_label"))
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def _labels_agree(gold: ComparableRevenueRow, pipeline: ComparableRevenueRow) -> bool:
    """Two "other" geographies are the same only when the document printed the same label."""
    if gold.geography != "other" or pipeline.geography != "other":
        return True
    a, b = _label_key(gold.extras.get("geography_label")), _label_key(pipeline.extras.get("geography_label"))
    return not a or not b or a == b or a in b or b in a


def stated_step(row: ComparableRevenueRow) -> float | None:
    """The last digit a row states, in USD millions: 92.8 million -> 0.1; 1,514 thousand -> 0.001."""
    if row.value_as_reported is None:
        return None
    text = f"{float(row.value_as_reported):g}"
    if "e" in text:
        return None
    decimals = len(text.split(".")[1]) if "." in text else 0
    scale = {"units": 1e-6, "thousands": 1e-3, "millions": 1.0, "billions": 1e3}.get((row.unit or "millions").lower())
    if scale is None:
        return None
    return (10 ** (-decimals)) * scale


def values_match(gold: float, pipeline: float, *, reference: ComparableRevenueRow | None = None) -> bool:
    """Equal to the precision the reference states.

    A reference that prints $92.8 million is matched by 92.823 read from a
    grid in thousands; one that prints 1,514 thousand is not matched by
    1.515. A derived reference (a year less nine months) carries the
    rounding of two stated figures.
    """
    tolerance = max(ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE * abs(gold))
    if reference is not None:
        step = stated_step(reference)
        if step:
            inputs = 2 if reference.route in {"derived", "bridged"} else 1
            tolerance = max(tolerance, 0.5 * step * inputs + 1e-9)
    return abs(gold - pipeline) <= tolerance


@dataclass(frozen=True)
class Comparison:
    gold: ComparableRevenueRow
    pipeline: ComparableRevenueRow | None
    outcome: str          # match | value_mismatch | geography_mismatch | currency_mismatch | needs_review | missing
    detail: str = ""


def comparable_values(gold: ComparableRevenueRow, pipeline: ComparableRevenueRow) -> tuple[float, float] | None:
    """Both figures in one currency: the reference's when the pipeline states it, else USD when both have a rate."""
    if gold.currency == pipeline.currency:
        return gold.value_millions, pipeline.value_millions
    if gold.value_usd_millions is not None and pipeline.value_usd_millions is not None:
        return gold.value_usd_millions, pipeline.value_usd_millions
    return None


def compare(gold: ComparableRevenueRow, candidates: list[ComparableRevenueRow]) -> Comparison:
    """The gold row against every pipeline row for the same product and period."""
    same = [c for c in candidates if c.key == gold.key and c.period_type == gold.period_type]
    if not same:
        return Comparison(gold, None, "missing", "no pipeline row for this period")
    # The same printed region is the same geography whatever canonical name
    # each side filed it under (EUCAN as Europe on one side, as other on the other).
    compatible = [
        c for c in same
        if (geographies_compatible(gold.geography, c.geography) and _labels_agree(gold, c)) or _same_printed_region(gold, c)
    ]
    if not compatible:
        offered = ", ".join(sorted({c.geography for c in same}))
        return Comparison(gold, same[0], "geography_mismatch", f"gold {gold.geography}; pipeline {offered}")
    # Prefer the exact geography, then the resolved rows.
    compatible.sort(key=lambda c: (not _same_printed_region(gold, c) and c.geography != gold.geography,
                                   c.status != "resolved", bool(c.extras.get("provisional"))))
    resolved = [c for c in compatible if c.status == "resolved"]
    if not resolved:
        return Comparison(gold, compatible[0], "needs_review", compatible[0].detail)
    for candidate in resolved:
        pair = comparable_values(gold, candidate)
        if pair is not None and values_match(pair[0], pair[1], reference=gold):
            detail = "via provisional" if candidate.extras.get("provisional") else ""
            return Comparison(gold, candidate, "match", detail)
    # A derived figure the issuer's own rounding leaves ambiguous: the
    # pipeline states the primary and carries the other result. Gold chose
    # one of them; the pipeline reports both, which is the honest answer.
    for candidate in resolved:
        for alternate in candidate.extras.get("alternates") or []:
            if candidate.currency == gold.currency and values_match(gold.value_millions, float(alternate)):
                return Comparison(gold, candidate, "match", f"via alternate derivation {alternate:g} (issuer rounding)")
    best = resolved[0]
    if all(comparable_values(gold, c) is None for c in resolved):
        offered = ", ".join(sorted({c.currency for c in resolved}))
        return Comparison(gold, best, "currency_mismatch", f"gold in {gold.currency}; pipeline in {offered} with no rate to compare")
    return Comparison(
        gold, best, "value_mismatch",
        f"gold {gold.value_millions:g} {gold.currency} vs pipeline {best.value_millions:g} {best.currency} ({best.route}:{best.derivation})",
    )
