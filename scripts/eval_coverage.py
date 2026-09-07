"""True coverage: every gold row, with the pipeline sourcing for itself.

``eval_extraction_documents.py --discover`` answers the same question a row at
a time, which is fine for a sample and wasteful for the corpus: what the
pipeline finds is decided by the issuer and the quarter, not by the product, so
275 EDGAR walks stand behind 1,415 rows. This does each walk once and scores
every row it covers, which is what makes running the whole set affordable.

Gold contributes the product, the issuer, the quarter and the expected value.
Everything else - which filings exist, which are earnings exhibits, which
exhibit holds the schedule, what the table says - is the pipeline's own work.

    SEC_CONTACT='project you@example.com' DISCOVER_CACHE=/tmp/discovered \
        python scripts/eval_coverage.py /tmp/coverage.json

The JSON is one record per gold row with the state it reached, so a run can be
diffed against the last one to see which rows a change moved.
"""

# ruff: noqa: BLE001 - a walk that fails is an issuer scored as unreachable
import asyncio
import collections
import csv
import json
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts")); sys.path.insert(0, str(REPO / "backend"))
import eval_extraction_documents as E
from app.connectors.sources import SECConnector
from app.extraction.candidates import extract_revenue_candidates
from app.extraction.members import load_register
from app.extraction.tagged import candidates_from_instance
from app.parsing.documents import DocumentParser

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/coverage.json")
rows = E.load_rows()
groups: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
for row in rows:
    groups[(row["manufacturer"], row["period"])].append(row)
print(f"{len(rows)} rows, {len(groups)} (issuer, quarter) pairs", flush=True)

store = E.LocalCacheStore(pathlib.Path(os.environ.get("DISCOVER_CACHE", "/tmp/discovered")))
TAGGED = os.environ.get("USE_XBRL", "1") != "0"
REGISTER = load_register() if TAGGED else {}
with (REPO / "seed" / "product_attributes.csv").open(newline="") as _handle:
    PRODUCTS = sorted({r["drug_name"].strip() for r in csv.DictReader(_handle) if r.get("drug_name")})
print(f"xbrl: {'on' if TAGGED else 'off'}  register: {len(REGISTER)} members  "
      f"products: {len(PRODUCTS)}")
outcome = collections.Counter()
per_issuer = collections.defaultdict(collections.Counter)
detail = []

async def go():
    import datetime as _dt
    started = time.time()
    for index, ((maker, period), group) in enumerate(sorted(groups.items())):
        ticker = E.TICKER.get(maker)
        year, quarter = int(period[:4]), int(period[-1])
        month, day = E.QUARTER_END[quarter]
        end = _dt.date(year, month, day)
        try:
            sources = await SECConnector(store).retrieve(
                run_id="coverage", job_id="coverage", cik=None, ticker=ticker,
                company_name=None if ticker else maker,
                include_primary=False, include_earnings=True,
                include_xbrl=TAGGED,
                earnings_since=end + _dt.timedelta(days=5),
                earnings_until=end + _dt.timedelta(days=120))
        except Exception:
            outcome["connector_error"] += len(group)
            per_issuer[maker]["connector_error"] += len(group)
            continue
        parser = DocumentParser(store)
        docs = []
        instances: list[bytes] = []
        for source in sources:
            if source.retrieval_status.value not in {"success", "partial"}:
                continue
            if source.metadata.get("xbrl_instance"):
                raw = await store.get(source.storage_key) if source.storage_key else None
                if raw:
                    instances.append(raw)
                continue
            doc = await parser.parse(source)
            if doc.parsing_status.value == "success" and doc.tables:
                docs.append(doc)
        for row in group:
            if TAGGED and instances:
                # The filer's own assertion, tried before anything is read off
                # a page. Nothing here narrows what the table reader then sees.
                tagged: list[dict] = []
                for raw in instances:
                    got, _notes = candidates_from_instance(
                        raw, product=row["drug_name"], products=PRODUCTS,
                        register=REGISTER)
                    tagged.extend(got)
                same = [c for c in tagged if str(c.get("period")) == row["period"]]
                target = row["value_normalized_usd_millions"]
                values = [float(c["value_normalized_usd_millions"]) for c in same]
                if values:
                    hit = any(abs(v - target) <= E.TOLERANCE for v in values)
                    state = "read_tagged" if hit else "wrong_value_tagged"
                    outcome[state] += 1
                    per_issuer[maker][state] += 1
                    detail.append({**row, "state": "read" if hit else "wrong_value",
                                   "read": target if hit else values[0], "via": "xbrl"})
                    continue
            if not docs:
                outcome["no_readable_filing"] += 1
                per_issuer[maker]["no_readable_filing"] += 1
                detail.append({**row, "state": "no_readable_filing"})
                continue
            found = []
            for doc in docs:
                got, _f, _s = extract_revenue_candidates(
                    doc.tables, product=row["drug_name"],
                    generic=row.get("generic_name"), context=doc.full_text[:4000],
                    grids=doc.table_grids)
                found.extend(got)
            target = row["value_normalized_usd_millions"]
            same = [c for c in found if str(c.get("period")) == row["period"]
                    and c.get("value_normalized_usd_millions") is not None]
            values = [float(c["value_normalized_usd_millions"]) for c in same]
            if any(abs(v - target) <= E.TOLERANCE for v in values):
                state, read = "read", target
            elif values:
                state, read = "wrong_value", min(values, key=lambda v: abs(v - target))
            else:
                state, read = "not_found", None
            outcome[state] += 1
            per_issuer[maker][state] += 1
            detail.append({**row, "state": state, "read": read, "via": "table"})
        if index % 25 == 0:
            print(f"  {index}/{len(groups)} pairs  {time.time()-started:.0f}s "
                  f"read={outcome['read']}", flush=True)

asyncio.run(go())
total = sum(outcome.values())
print(f"\nrows scored {total}")
for state, n in outcome.most_common():
    print(f"  {state:<22}{n:>6}  {n/total:6.2%}")
print("\nby issuer")
for maker, counts in sorted(per_issuer.items()):
    n = sum(counts.values())
    print(f"  {maker:<22}{counts['read']:>5}/{n:<6}{counts['read']/n:7.1%}  "
          f"{dict(counts)}")
OUT.write_text(json.dumps(detail))
print("\nper-row detail written to", OUT)
