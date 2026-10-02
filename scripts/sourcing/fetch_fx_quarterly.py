"""Write the quarterly USD conversion table gold uses for non-USD issuers.

Each rate is FRED's quarterly average of the Federal Reserve Board's H.10
noon buying rate for that currency, fetched from FRED's own CSV endpoint. FRED
quotes some pairs as USD per unit and others as units per USD; the table
always stores USD per one unit of the currency, so a row converts by
multiplication and the inversion is done once, here.

    python scripts/sourcing/fetch_fx_quarterly.py

Re-run it to extend the table when the as-of quarter moves.
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "seed" / "gold" / "source_manifests" / "fx_quarterly_usd.csv"
AS_OF_QUARTER = "2026Q2"
# currency -> (FRED series, True when FRED quotes it as USD per unit)
SERIES = {
    "EUR": ("EXUSEU", True),
    "GBP": ("EXUSUK", True),
    "AUD": ("EXUSAL", True),
    "CHF": ("EXSZUS", False),
    "DKK": ("EXDNUS", False),
    "JPY": ("EXJPUS", False),
    "SEK": ("EXSDUS", False),
    "INR": ("EXINUS", False),
}
URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&fq=Quarterly&fam=avg&cosd=1995-01-01"


def main() -> int:
    rows = []
    with httpx.Client(timeout=60) as client:
        for currency, (series, usd_per_unit) in SERIES.items():
            url = URL.format(series=series)
            response = client.get(url)
            response.raise_for_status()
            for record in csv.reader(io.StringIO(response.text)):
                if not record or not record[0][:4].isdigit() or record[1] in ("", "."):
                    continue
                year, month = int(record[0][:4]), int(record[0][5:7])
                quarter = f"{year}Q{(month - 1) // 3 + 1}"
                if quarter > AS_OF_QUARTER:
                    continue
                rate = float(record[1])
                rows.append({
                    "quarter": quarter,
                    "currency": currency,
                    "usd_per_unit": round(rate if usd_per_unit else 1 / rate, 8),
                    "fred_series": series,
                    "fred_value": record[1],
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
