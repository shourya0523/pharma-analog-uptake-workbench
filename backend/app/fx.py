"""U.S. dollars per unit of a currency, averaged over a calendar quarter or year.

Rates are the Federal Reserve Board's H.10 daily noon buying rates, read from
the Board's historical-rates pages at run time. A period's rate is the mean of
the days the page reports in it; days marked ND (no data) are skipped.

Each page states its own quotation - "U.S. dollars per EU euro" or "Japanese
yen per U.S. dollar". The period's mean is taken in that quotation and then,
if it is per dollar, inverted once, so every rate returned converts by
multiplication. Averaging the inverted daily rates instead gives a slightly
different number; the mean of the quotation is the rate H.10 users quote.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from functools import lru_cache

import httpx

URL = "https://www.federalreserve.gov/releases/h10/hist/dat00_{code}.htm"
# ISO currency code -> the H.10 historical page's country code. The Board's
# index names countries, not currencies, so the mapping cannot be read from
# it. A snapshot of the H.10 pages for currencies pharma issuers report in;
# a currency missing here raises rather than converting with nothing.
PAGES = {
    "AUD": "al", "BRL": "bz", "CAD": "ca", "CNY": "ch", "DKK": "dn", "EUR": "eu", "HKD": "hk", "INR": "in",
    "JPY": "ja", "KRW": "ko", "MXN": "mx", "NOK": "no", "SEK": "sd", "CHF": "sz", "TWD": "ta", "GBP": "uk",
    "SGD": "si", "ZAR": "sf", "NZD": "nz",
}
_ROW = re.compile(r'<th[^>]*headers="a1"[^>]*>\s*([0-9]{1,2}-[A-Z]{3}-[0-9]{2})\s*</th>\s*<td[^>]*>\s*([^<]*?)\s*</td>')
_HEADING = re.compile(r"\(Rates in ([^)]+)\)")


class UnknownCurrency(KeyError):
    """No H.10 page is known for this currency."""


@lru_cache(maxsize=None)
def daily_quotes(currency: str) -> tuple[dict[str, float], bool]:
    """({YYYY-MM-DD: rate as H.10 quotes it}, quoted_as_usd_per_unit)."""
    if currency == "USD":
        return {}, True
    try:
        code = PAGES[currency]
    except KeyError:
        raise UnknownCurrency(currency) from None
    agent = os.environ.get("SEC_USER_AGENT") or "pharma-analog-uptake-workbench research"
    html = httpx.get(URL.format(code=code), headers={"User-Agent": agent}, timeout=60).text
    usd_per_unit = _HEADING.search(html).group(1).startswith("U.S. dollars per")
    days = {}
    for date, value in _ROW.findall(html):
        if re.fullmatch(r"[0-9.]+", value) and float(value) > 0:
            days[datetime.strptime(date, "%d-%b-%y").date().isoformat()] = float(value)
    return days, usd_per_unit


def usd_per_unit(currency: str, year: int, quarter: int | None = None) -> float:
    """Mean USD per unit over calendar `year`, or its `quarter` (1-4) when given."""
    if currency == "USD":
        return 1.0
    months = range(3 * quarter - 2, 3 * quarter + 1) if quarter else range(1, 13)
    quotes, per_unit = daily_quotes(currency)
    rates = [r for day, r in quotes.items() if int(day[:4]) == year and int(day[5:7]) in months]
    if not rates:
        raise LookupError(f"H.10 has no {currency} rates for {year}{f'Q{quarter}' if quarter else ''}")
    mean = sum(rates) / len(rates)
    return mean if per_unit else 1 / mean


def to_usd(value: float, currency: str, year: int, quarter: int | None = None) -> float:
    """`value` in `currency` converted at the period's average H.10 rate."""
    return value * usd_per_unit(currency, year, quarter)
