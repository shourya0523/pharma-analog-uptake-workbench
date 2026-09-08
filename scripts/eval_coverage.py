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
from app.extraction.derive import complete_series
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
    _ATTRS = [r for r in csv.DictReader(_handle) if r.get("drug_name")]
PRODUCTS = sorted({r["drug_name"].strip() for r in _ATTRS})
APPROVED = {
    r["drug_name"].strip(): int(r["first_approval_year"])
    for r in _ATTRS
    if (r.get("first_approval_year") or "").strip().isdigit()
}
# Which product is a formulation of which family, from the pipeline's own
# reference data. Gold says nothing here; seed/product_attributes.csv carries
# "formulation_of:Tyvaso" because that is a fact about the product.
FAMILY_OF = {
    r["drug_name"].strip(): r["peer_universe_role"].split(":", 1)[1].strip()
    for r in _ATTRS
    if (r.get("peer_universe_role") or "").startswith("formulation_of:")
}
SIBLINGS = {
    product: sorted(
        {other for other, fam in FAMILY_OF.items() if fam == family and other != product}
        | {other["drug_name"].strip() for other in _ATTRS
           if other["drug_name"].strip() != product
           and other["drug_name"].strip() != family
           and other["drug_name"].strip().startswith(family + " ")}
    )
    for product, family in FAMILY_OF.items()
}
print(f"xbrl: {'on' if TAGGED else 'off'}  register: {len(REGISTER)} members  "
      f"products: {len(PRODUCTS)}")
def _agrees(values: list[float], target: float) -> bool:
    """Whether the pipeline answered this period, with one answer.

    Crediting the row when *any* candidate matches scores a reader that emits
    the right figure beside a wrong one as if it had read the table, and the
    wrong figure is what a consumer with no answer key would have to choose
    between. Two candidates that disagree are not an answer, so this counts them
    as the failure they are.
    """
    if not values:
        return False
    spread = max(values) - min(values)
    return spread <= E.TOLERANCE and abs(values[0] - target) <= E.TOLERANCE


outcome = collections.Counter()
per_issuer = collections.defaultdict(collections.Counter)
detail = []
# Every candidate the run extracted, so a series can be completed from what the
# issuer published across its filings rather than from one quarter's document.
pool: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)

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
                # The 10-Q as well as the 8-K: before the product-sales
                # exhibit existed, the figure is a sentence in the filing.
                include_primary=True, include_earnings=True,
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
            if doc.parsing_status.value == "success" and (doc.tables or doc.full_text):
                docs.append(doc)
        for row in group:
            if TAGGED and instances:
                # The filer's own assertion, tried before anything is read off
                # a page. Nothing here narrows what the table reader then sees.
                tagged: list[dict] = []
                for raw in instances:
                    got, _notes = candidates_from_instance(
                        raw, product=row["drug_name"], issuer=maker,
                        products=PRODUCTS, register=REGISTER)
                    tagged.extend(got)
                pool[(maker, row["drug_name"])].extend(tagged)
                same = [c for c in tagged if str(c.get("period")) == row["period"]]
                target = row["value_normalized_usd_millions"]
                values = [float(c["value_normalized_usd_millions"]) for c in same]
                if values:
                    hit = _agrees(values, target)
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
                    grids=doc.table_grids, captions=doc.table_captions,
                    prose=doc.full_text, quarterly_only=False)
                found.extend(got)
            pool[(maker, row["drug_name"])].extend(found)
            found = [c for c in found if c.get("period_type") == "quarterly"]
            target = row["value_normalized_usd_millions"]
            same = [c for c in found if str(c.get("period")) == row["period"]
                    and c.get("value_normalized_usd_millions") is not None]
            values = [float(c["value_normalized_usd_millions"]) for c in same]
            if _agrees(values, target):
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

# Stage 3b: the quarters a product's own series implies. Applied only to rows
# nothing was found for, so a derivation can fill a gap and never overrule a
# figure read off a page. Both derivations are exact arithmetic over values the
# issuer published; neither invents a number.
derived_cache: dict[tuple[str, str], dict[str, dict]] = {}
for record in detail:
    if record["state"] != "not_found":
        continue
    maker, product = record["manufacturer"], record["drug_name"]
    if (maker, product) not in derived_cache:
        family = FAMILY_OF.get(product)
        derived_cache[(maker, product)] = {
            str(candidate["period"]): candidate
            for candidate in complete_series(
                {name: pool[(maker, name)] for name in
                 {product, family, *SIBLINGS.get(product, ())} if name},
                product=product, family=family,
                siblings=SIBLINGS.get(product, ()),
                sibling_first_year=min(
                    (APPROVED[name] for name in SIBLINGS.get(product, ()) if name in APPROVED),
                    default=None,
                ),
            )
        }
    candidate = derived_cache[(maker, product)].get(record["period"])
    if candidate is None:
        continue
    value = candidate["value_normalized_usd_millions"]
    target = record["value_normalized_usd_millions"]
    hit = abs(float(value) - float(target)) <= E.TOLERANCE
    outcome["not_found"] -= 1
    per_issuer[maker]["not_found"] -= 1
    state = "read_derived" if hit else "wrong_value_derived"
    outcome[state] += 1
    per_issuer[maker][state] += 1
    record["state"] = "read" if hit else "wrong_value"
    record["read"] = float(value)
    record["via"] = candidate["extraction_method"]

total = sum(outcome.values())
print(f"\nrows scored {total}")
for state, n in outcome.most_common():
    print(f"  {state:<22}{n:>6}  {n/total:6.2%}")
print("\nby issuer")
for maker, counts in sorted(per_issuer.items()):
    n = sum(counts.values())
    correct = counts["read"] + counts["read_tagged"] + counts["read_derived"]
    print(f"  {maker:<22}{correct:>5}/{n:<6}{correct/n:7.1%}   "
          f"tagged {counts['read_tagged']:>4}  table {counts['read']:>4}  "
          f"derived {counts['read_derived']:>3}  "
          f"missed {n - correct:>4}")
OUT.write_text(json.dumps(detail))
print("\nper-row detail written to", OUT)
