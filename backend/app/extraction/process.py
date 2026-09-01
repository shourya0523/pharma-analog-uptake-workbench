"""Stage 3: normalize extracted values onto one comparable scale.

Everything downstream - peak detection, uptake curves, cross-product ranking -
compares numbers to each other, so they have to arrive in the same unit and the
same currency. Values keep their as-reported form alongside the normalized one
so a number can always be traced back to what the issuer actually printed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.extraction.extract import ExtractedValue
from app.extraction.fingerprint import UNIT_SCALE_TO_MILLIONS

# USD per 1 unit of foreign currency, annual average of the New York noon
# buying rate certified by the Federal Reserve Bank of New York. Swiss and UK
# issuers report PAH products in their home currency (Actelion's Tracleer in
# CHF, GSK's Flolan in GBP) and never restate them in USD, so a comparable
# figure has to be derived. Sourced from UBS Group AG's "Selected Financial
# Data" SEC filings, which publish this table annually for exactly this use.
FX_USD_PER_UNIT: dict[str, dict[int, float]] = {
    "CHF": {
        2001: 0.5910, 2002: 0.6453, 2003: 0.7493, 2004: 0.8059, 2005: 0.8039,
        2006: 0.8034, 2007: 0.8381, 2008: 0.9298, 2009: 0.9260, 2010: 0.9670,
        2011: 1.1398, 2012: 1.0724, 2013: 1.0826, 2014: 1.0893, 2015: 1.0368,
        2016: 1.0128,
    },
    "GBP": {2010: 1.5458, 2011: 1.6043, 2012: 1.5853, 2013: 1.5642},
}


@dataclass(frozen=True)
class Datapoint:
    """A normalized observation, with its as-reported provenance intact."""

    product_label: str
    period: str
    period_type: str
    value_normalized_usd_millions: float | None
    value_as_reported: float
    source_unit: str
    source_currency: str
    fx_rate_to_usd: float | None
    source_quote: str
    fingerprint_signature: str
    normalization_status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_label": self.product_label,
            "period": self.period,
            "period_type": self.period_type,
            "value_normalized_usd_millions": self.value_normalized_usd_millions,
            "value_as_reported": self.value_as_reported,
            "source_unit": self.source_unit,
            "source_currency": self.source_currency,
            "fx_rate_to_usd": self.fx_rate_to_usd,
            "source_quote": self.source_quote,
            "fingerprint_signature": self.fingerprint_signature,
            "normalization_status": self.normalization_status,
        }


def _year_of(period: str) -> int | None:
    try:
        return int(period[:4])
    except (TypeError, ValueError):
        return None


def normalize(value: ExtractedValue) -> Datapoint:
    """Scale one extracted value to USD millions.

    A value whose currency has no rate for its year is returned unnormalized
    with a status saying so, rather than passed through as if it were USD -
    silently treating CHF as USD is a 30-70% error depending on the year.
    """
    scale = UNIT_SCALE_TO_MILLIONS.get(value.unit_label)
    if scale is None:
        return Datapoint(
            product_label=value.product_label,
            period=value.period,
            period_type=value.period_type,
            value_normalized_usd_millions=None,
            value_as_reported=value.value_as_reported,
            source_unit=value.unit_label,
            source_currency=value.currency,
            fx_rate_to_usd=None,
            source_quote=value.source_quote,
            fingerprint_signature=value.fingerprint_signature,
            normalization_status="unknown_unit",
        )

    in_millions = value.value_as_reported * scale
    if value.currency == "USD":
        return Datapoint(
            product_label=value.product_label,
            period=value.period,
            period_type=value.period_type,
            value_normalized_usd_millions=round(in_millions, 6),
            value_as_reported=value.value_as_reported,
            source_unit=value.unit_label,
            source_currency=value.currency,
            fx_rate_to_usd=None,
            source_quote=value.source_quote,
            fingerprint_signature=value.fingerprint_signature,
            normalization_status="ok",
        )

    year = _year_of(value.period)
    rate = FX_USD_PER_UNIT.get(value.currency, {}).get(year) if year else None
    if rate is None:
        return Datapoint(
            product_label=value.product_label,
            period=value.period,
            period_type=value.period_type,
            value_normalized_usd_millions=None,
            value_as_reported=value.value_as_reported,
            source_unit=value.unit_label,
            source_currency=value.currency,
            fx_rate_to_usd=None,
            source_quote=value.source_quote,
            fingerprint_signature=value.fingerprint_signature,
            normalization_status=f"no_fx_rate_for_{value.currency}_{year}",
        )

    return Datapoint(
        product_label=value.product_label,
        period=value.period,
        period_type=value.period_type,
        value_normalized_usd_millions=round(in_millions * rate, 6),
        value_as_reported=value.value_as_reported,
        source_unit=value.unit_label,
        source_currency=value.currency,
        fx_rate_to_usd=rate,
        source_quote=value.source_quote,
        fingerprint_signature=value.fingerprint_signature,
        normalization_status="ok",
    )


def normalize_all(values: list[ExtractedValue]) -> list[Datapoint]:
    return [normalize(value) for value in values]
