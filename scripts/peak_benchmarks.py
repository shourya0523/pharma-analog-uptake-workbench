"""The peak benchmark an uptake curve divides by, shared by every export that draws one.

The benchmark is an annual peak, and the annual peak is the larger of two inputs:

  observed  - the highest total over four consecutive reported quarters.
              Four consecutive, not four calendar quarters of one year: a
              product peaking mid-year has its peak split across two
              calendar years by a January-to-December window.
  consensus - the median of the published peak-sales estimates that are
              comparable to the series, where any are.

Consensus wins only when it is the larger. An estimate below what the product
has already sold has been overtaken and says nothing; an estimate above it is
the part of the curve the reported quarters do not yet cover.

An estimate is usable when it is a peak (not a market-size projection for a
horizon year), states the geography the series' own rows state, and comes
from a free-to-read source (``source_access`` is ``free_to_read``) rather than
a paid report, subscription or licensed transcript. An
estimate failing any of the three is set aside on the row with the reason,
never dropped quietly.

Reads seed/gold and seed/consensus_peak_estimates.csv only. Like
export_gold_workbook.py it must never import from the gold builder or from
application code.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parents[1]
ESTIMATES = REPO / "seed" / "consensus_peak_estimates.csv"

# The one estimate_kind that can serve as a peak. Another kind may be worth
# recording - the file keeps what was checked, including what was rejected -
# but it cannot be the denominator of an uptake curve.
PEAK_ESTIMATE = "peak_annual_sales"
# The source_access value an estimate needs before it can be used. Anything
# else names the paid or licensed channel that keeps it off the sheet.
OPEN_ACCESS = "free_to_read"

CONSENSUS = "consensus median - above what the series has reported"
OBSERVED = "observed rolling four-quarter high - above the consensus median"
NO_ESTIMATE = "observed rolling four-quarter high - no comparable estimate on file"


def quarter_index(period):
    """A monotonic integer for a ``YYYYQn`` label, so quarters can be subtracted."""
    return int(period[:4]) * 4 + int(period[5]) - 1


def period_at(index):
    """The ``YYYYQn`` label for a quarter index, inverting ``quarter_index``."""
    return f"{index // 4}Q{index % 4 + 1}"


def load_consensus_estimates():
    """Published peak-sales estimates, grouped by the product each one names.

    A written snapshot of what has been published elsewhere - news, market
    research, analyst notes, litigation filings - transcribed one row per
    estimate with the URL it came from and what checking it against that URL
    showed. Nothing in this repository produces it and nothing at run time
    can, which is why it is a file rather than a derivation; it goes stale
    when an estimate is published or revised, or when a product enters the
    dataset that has none. Values are read as written: this reads the file,
    never the source behind it.
    """
    with ESTIMATES.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["product"].strip()].append({
            "value": float(row["estimate_usd_millions"]),
            "as_stated": row["estimate_as_stated"].strip(),
            "kind": row["estimate_kind"].strip(),
            "geography": row["estimate_geography"].strip(),
            "attribution": row["attribution"].strip(),
            "publisher": row["source_publisher"].strip(),
            "url": row["source_url"].strip(),
            "published_on": row["published_on"].strip(),
            "access": (row.get("source_access") or OPEN_ACCESS).strip(),
        })
    return grouped


def set_aside_reason(estimate, geography):
    """Why an estimate on file is not usable for a series, or None if it is."""
    if estimate["kind"] != PEAK_ESTIMATE:
        return f"not a peak estimate - {estimate['kind'].replace('_', ' ')}"
    if estimate["geography"] != geography:
        return f"estimate is {estimate['geography']}, the series reports {geography}"
    if estimate["access"] != OPEN_ACCESS:
        return f"paid or licensed source - {estimate['access'].replace('_', ' ')}"
    return None


def rolling_four_quarter_peak(values_by_period):
    """The highest four-consecutive-quarter total, and the window it spans.

    Only a window whose four quarters are all reported counts. Summing across
    a gap would present a three-quarter total as an annual one, and it would
    be indistinguishable from a real annual figure once written into a cell.
    Returns ``(total, first_period, last_period)``, or None where the series
    holds no complete window.
    """
    indexed = {quarter_index(period): value for period, value in values_by_period.items()}
    best = None
    for start in sorted(indexed):
        window = [indexed.get(start + step) for step in range(4)]
        if any(value is None for value in window):
            continue
        total = sum(window)
        if best is None or total > best[0]:
            best = (total, period_at(start), period_at(start + 3))
    return best


def select_peak(on_file, geography, values_by_period):
    """The benchmark for one series: what was observed, what was published, and which won.

    Returns a dict with ``observed`` (the rolling-peak tuple or None),
    ``usable`` and ``reasons`` (estimate id -> set-aside reason or None),
    ``consensus``, ``selected`` and ``basis``.
    """
    observed = rolling_four_quarter_peak(values_by_period)
    reasons = {id(item): set_aside_reason(item, geography) for item in on_file}
    usable = [item for item in on_file if reasons[id(item)] is None]
    consensus = median(item["value"] for item in usable) if usable else None
    if consensus is not None and observed is not None and consensus > observed[0]:
        selected, basis = consensus, CONSENSUS
    elif observed is not None:
        selected, basis = observed[0], OBSERVED if usable else NO_ESTIMATE
    else:
        selected, basis = consensus, CONSENSUS if consensus is not None else None
    return {"observed": observed, "usable": usable, "reasons": reasons,
            "consensus": consensus, "selected": selected, "basis": basis}
