"""Does a tagged datapoint's citation stand up in the instance it names?

Every datapoint the pipeline publishes carries a receipt. For a figure read off
a table that is a quote, verbatim in the document, and scripts/eval_provenance.py
audits it. A tagged fact has no prose to quote, so its receipt is different in
kind - the element, the context, the period and the value - and that audit does
not know how to check it. Which means the tagged path publishes unaudited
citations until this exists.

The check is the same in spirit: go back to the instance the citation names,
find the fact it points at, and confirm it says what the datapoint says.

A context is not a fact. It is a period and a set of dimensions, and many facts
share one - Johnson & Johnson tags a product's revenue and its percentage change
against the same context, differing only by element. Looking a citation up by
context alone finds whichever fact came last and reports a revenue figure as
disagreeing with a change of -0.215. That is what this check did on its first
run, on 105 of 367 datapoints, and the citation was right every time.

Uses no gold. It audits the pipeline's own claim against the pipeline's own
source, which is what makes it a provenance check rather than an accuracy one.

    SEC_CONTACT='project you@example.com' python scripts/eval_tagged_provenance.py
"""

from __future__ import annotations

# ruff: noqa: BLE001 - an issuer that fails to fetch is skipped, not fatal
import argparse
import asyncio
import collections
import csv
import datetime as dt
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "backend"))

import eval_extraction_documents as E
from app.connectors.sources import SECConnector
from app.extraction.members import load_register
from app.extraction.tagged import candidates_from_instance
from app.parsing.xbrl import parse_facts

ISSUERS = {
    "Gilead": "GILD",
    "Johnson & Johnson": "JNJ",
    "United Therapeutics": "UTHR",
    "Merck": "MRK",
    "Liquidia": "LQDA",
}
QUARTERS = [(2024, 3, 31), (2024, 6, 30), (2024, 9, 30), (2025, 3, 31), (2025, 6, 30)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if not os.environ.get("SEC_CONTACT"):
        raise SystemExit("Set SEC_CONTACT, e.g. 'project you@example.com'")

    store = E.LocalCacheStore(pathlib.Path(os.environ.get("DISCOVER_CACHE", "/tmp/discovered")))
    register = load_register()
    with (REPO / "seed" / "product_attributes.csv").open(newline="") as handle:
        products = sorted({row["drug_name"].strip() for row in csv.DictReader(handle)
                           if row.get("drug_name")})

    tally: collections.Counter[str] = collections.Counter()
    offenders: list[str] = []

    async def go() -> None:
        pairs = [(issuer, q) for issuer in ISSUERS for q in QUARTERS]
        if args.limit:
            del pairs[args.limit:]
        for issuer, (year, month, day) in pairs:
            end = dt.date(year, month, day)
            try:
                sources = await SECConnector(store).retrieve(
                    run_id="prov", job_id="prov", cik=None, ticker=ISSUERS[issuer],
                    company_name=None, include_primary=False, include_earnings=False,
                    include_xbrl=True,
                    earnings_since=end + dt.timedelta(days=5),
                    earnings_until=end + dt.timedelta(days=120))
            except Exception as exc:
                tally[f"retrieve failed: {type(exc).__name__}"] += 1
                continue
            for source in sources:
                if not source.metadata.get("xbrl_instance") or not source.storage_key:
                    continue
                raw = await store.get(source.storage_key)
                tally["instances audited"] += 1
                # Keyed by context and element together: a context is a period
                # and its dimensions, and several facts share one.
                by_fact = {(f.context_id, f.element): f for f in parse_facts(raw)}
                for product in products:
                    found, _notes = candidates_from_instance(
                        raw, product=product, issuer=issuer,
                        products=products, register=register)
                    for candidate in found:
                        tally["datapoints"] += 1
                        context = candidate["xbrl_context"]
                        element = candidate["source_quote"].split(" ", 1)[0]
                        fact = by_fact.get((context, element))
                        if fact is None:
                            tally["citation names no fact in the instance"] += 1
                            offenders.append(
                                f"{issuer} {product}: {element} @ {context} absent")
                            continue
                        tally["citation resolves to a fact"] += 1
                        if abs(fact.value - candidate["value_reported"]) > 0.005:
                            tally["value disagrees with the fact"] += 1
                            offenders.append(
                                f"{issuer} {product}: cited {candidate['value_reported']:g}"
                                f" but fact holds {fact.value:g}")
                            continue
                        tally["value matches the fact"] += 1
                        member = (fact.product_member or "").split(":")[-1]
                        if member not in candidate["source_quote"]:
                            tally["citation omits the member"] += 1
                            offenders.append(f"{issuer} {product}: {candidate['source_quote'][:70]}")
                            continue
                        tally["citation stands"] += 1

    asyncio.run(go())
    total = tally["datapoints"]
    print(f"instances audited: {tally['instances audited']}")
    print(f"tagged datapoints published: {total}")
    if total:
        for key in ("citation resolves to a fact", "value matches the fact",
                    "citation stands"):
            print(f"  {key:<34}{tally[key]:>6}/{total}  {tally[key] / total:6.1%}")
    for key in ("citation names no fact in the instance",
                "value disagrees with the fact", "citation omits the member"):
        if tally[key]:
            print(f"  {key}: {tally[key]}")
    for line in offenders[:12]:
        print("   " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
