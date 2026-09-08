"""Score the reader on issuers absent from gold, with arithmetic as the oracle.

The generalisation gates in this repo measure parts of the reader - which
period a heading names, whether a unit is declared. This one asks the question
the pipeline exists to answer, on filers it was never built against, and checks
the answers without an answer key.

Two properties have to hold of any correct reading, and neither needs gold:

* a product's four quarters sum to the year it reported, within the rounding
  the issuer printed;
* the same product and period, read from two different documents, gives one
  figure.

A reader that guesses will violate these on unseen filers even where it happens
to match gold on the six issuers it was built against. A reader that refuses
will simply have less to check, which is why the count of figures read is
reported beside the violations rather than hidden behind a pass rate.

    HOLDOUT_DIR=/tmp/holdout uv run --project backend python scripts/eval_unseen_arithmetic.py
"""

from __future__ import annotations

import collections
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from bs4 import BeautifulSoup  # noqa: E402

from app.extraction.candidates import extract_revenue_candidates  # noqa: E402
from app.parsing.documents import _selected_tables, flatten_grid, table_caption  # noqa: E402

WORK = pathlib.Path(os.environ.get("HOLDOUT_DIR", "/tmp/holdout"))
TOLERANCE = 0.51

# The eval's own fixture, not a pipeline input. ``load_products`` knows only the
# products the pipeline tracks, and every one of them belongs to an issuer in
# gold - so asking it about Pfizer reads nothing at all, which is the opposite
# of a generalisation test. These are each issuer's largest products, named here
# for the same reason the corpus names the four issuers: the held-out set is
# chosen, and what is asked of it has to be chosen with it.
PRODUCTS = {
    "Pfizer": ["Eliquis", "Ibrance", "Prevnar", "Xeljanz", "Comirnaty", "Paxlovid", "Vyndaqel", "Xtandi"],
    "AbbVie": ["Humira", "Skyrizi", "Rinvoq", "Imbruvica", "Botox", "Venclexta", "Vraylar"],
    "Amgen": ["Enbrel", "Prolia", "Otezla", "Repatha", "Xgeva", "Neulasta", "Nplate", "Tezspire"],
    "EliLilly": ["Trulicity", "Mounjaro", "Verzenio", "Taltz", "Jardiance", "Zepbound", "Humalog", "Emgality"],
}


def main() -> int:
    manifest = json.loads((WORK / "manifest.json").read_text())
    entries = manifest if isinstance(manifest, list) else manifest.get("documents", [])

    # (issuer, product, period) -> {value: [documents that said it]}
    seen: dict[tuple[str, str, str], dict[float, list[str]]] = collections.defaultdict(
        lambda: collections.defaultdict(list)
    )
    read = 0
    named = answered = 0
    unread: list[tuple[str, str]] = []
    for entry in entries:
        path = pathlib.Path(entry["path"])
        soup = BeautifulSoup(path.read_bytes(), "lxml")
        selected = _selected_tables(soup)
        grids = [grid for _element, grid in selected]
        captions = [table_caption(element) for element, _grid in selected]
        tables = [rows for grid in grids if (rows := flatten_grid(grid))]
        text = soup.get_text(" ", strip=True)
        lowered = text.lower()
        for product in PRODUCTS.get(entry["issuer"], []):
            # Only products the document actually names can be read out of it,
            # so they are the denominator. Counting the rest as misses would
            # score the reader on documents that never mentioned them.
            mentions = product.lower() in lowered
            named += mentions
            found, _findings, _skipped = extract_revenue_candidates(
                tables, product=product, context=text[:4000], grids=grids,
                captions=captions, prose=text, quarterly_only=False,
            )
            quarterly = [
                c for c in found
                if c.get("period_type") == "quarterly"
                and c.get("value_normalized_usd_millions") is not None
            ]
            if mentions:
                answered += bool(quarterly)
                if not quarterly:
                    unread.append((entry["issuer"], product))
            for candidate in found:
                value = candidate.get("value_normalized_usd_millions")
                if value is None:
                    continue
                read += 1
                key = (entry["issuer"], product, str(candidate["period"]))
                seen[key][round(float(value), 3)].append(path.name)

    disagreements = [
        (key, values) for key, values in seen.items()
        if max(values) - min(values) > TOLERANCE
    ]

    # quarters against the year they belong to
    by_series: dict[tuple[str, str], dict[str, float]] = collections.defaultdict(dict)
    for (issuer, product, period), values in seen.items():
        by_series[(issuer, product)][period] = min(values)
    residuals = []
    for (issuer, product), periods in by_series.items():
        for period, annual in periods.items():
            if not period.isdigit():
                continue
            quarters = [periods.get(f"{period}Q{q}") for q in (1, 2, 3, 4)]
            if any(value is None for value in quarters):
                continue
            residual = abs(sum(quarters) - annual)
            if residual > 2.0:  # each quarter is printed rounded
                residuals.append((issuer, product, period, sum(quarters), annual, residual))

    print(f"issuers absent from gold: {sorted({e['issuer'] for e in entries})}")
    print(f"documents: {len(entries)}   figures read: {read}   series: {len(by_series)}")
    print(f"\nproducts the document names, and a quarter was read for: "
          f"{answered}/{named} = {answered/named:.1%}" if named else "nothing named")
    missed = collections.Counter(unread)
    if missed:
        print("   most often named and not read:",
              ", ".join(f"{i} {p}" for (i, p), _n in missed.most_common(6)))
    print(f"\none product and period, two documents, two answers: {len(disagreements)}")
    for key, values in disagreements[:6]:
        print(f"   {key}  ->  {sorted(values)}")
    print(f"\nfour quarters that do not sum to their year: {len(residuals)}")
    for issuer, product, year, total, annual, residual in residuals[:6]:
        print(f"   {issuer} {product} {year}: quarters {total:.1f} vs year {annual:.1f} (off by {residual:.1f})")
    return 1 if disagreements or residuals else 0


if __name__ == "__main__":
    raise SystemExit(main())
