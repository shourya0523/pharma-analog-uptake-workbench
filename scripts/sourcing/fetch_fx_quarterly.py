"""Write the quarterly USD conversion table gold uses for non-USD issuers.

Each rate is the quarterly average of the Federal Reserve Board's H.10 daily
noon buying rate for that currency, computed here from the Board's own
historical-rates page (federalreserve.gov/releases/h10/hist/). Days the page
marks ND (no data) are skipped, so a quarter averages the days it holds.

The page states its own quotation, "Rates in U.S. dollars per EU euro" or
"Rates in Japanese yen per U.S. dollar"; the table always stores USD per one
unit of the currency, so a row converts by multiplication and the inversion
is done once, here, from that heading.

    python scripts/sourcing/fetch_fx_quarterly.py

Re-run it to extend the table when the as-of quarter moves.
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "seed" / "gold" / "source_manifests" / "fx_quarterly_usd.csv"
AS_OF_QUARTER = "2026Q2"
# currency -> the H.10 historical page's country code. A snapshot of the
# currencies gold's non-USD issuers report in; a new reporting currency needs
# its code added here, and quarterly_usd in the builder fails loudly until it is.
PAGES = {
    "EUR": "eu",
    "GBP": "uk",
    "AUD": "al",
    "CHF": "sz",
    "DKK": "dn",
    "JPY": "ja",
    "SEK": "sd",
    "INR": "in",
}
URL = "https://www.federalreserve.gov/releases/h10/hist/dat00_{code}.htm"
ROW = re.compile(r'<th[^>]*headers="a1"[^>]*>\s*([0-9]{1,2}-[A-Z]{3}-[0-9]{2})\s*</th>\s*<td[^>]*>\s*([^<]*?)\s*</td>')
HEADING = re.compile(r"\(Rates in ([^)]+)\)")


def quarterly_averages(html: str) -> tuple[dict[str, tuple[float, int]], bool, str]:
    """({quarter: (mean, days)}, quoted_as_usd_per_unit, heading) from one H.10 page."""
    heading = HEADING.search(html).group(1)
    usd_per_unit = heading.startswith("U.S. dollars per")
    days: dict[str, list[float]] = defaultdict(list)
    for date, value in ROW.findall(html):
        if not re.fullmatch(r"[0-9.]+", value):
            continue
        day = datetime.strptime(date, "%d-%b-%y")
        days[f"{day.year}Q{(day.month - 1) // 3 + 1}"].append(float(value))
    return {q: (sum(v) / len(v), len(v)) for q, v in days.items()}, usd_per_unit, heading


def main() -> int:
    rows = []
    with httpx.Client(timeout=60, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for currency, code in PAGES.items():
            url = URL.format(code=code)
            response = client.get(url)
            response.raise_for_status()
            averages, usd_per_unit, heading = quarterly_averages(response.text)
            for quarter, (mean, count) in averages.items():
                if quarter > AS_OF_QUARTER:
                    continue
                rows.append({
                    "quarter": quarter,
                    "currency": currency,
                    "usd_per_unit": round(mean if usd_per_unit else 1 / mean, 8),
                    "h10_quarterly_mean": round(mean, 6),
                    "h10_quotation": heading,
                    "daily_observations": count,
                    "source_url": url,
                })
    with OUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["currency"], r["quarter"])))
    print(f"wrote {len(rows)} rates to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
