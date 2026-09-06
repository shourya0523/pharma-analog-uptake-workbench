"""Does the model describe the rows gold cites, before any reader runs?

For every gold quarterly row read from a grid, find the grid and row that
print the row's ``source_quote``, ask the fingerprinter to describe the
document, and check that a described row of the right product, line kind
and geography sits there. This measures the contract and the model alone:
no header grammar, no reader, no series. It is the Phase 1 acceptance
number for the model-first design.

Usage (from ``backend/``):

    uv run python ../scripts/audit_fingerprints.py [--product NAME]... [--rendering markdown|raw]
        [--json PATH] [--show-misses]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from app.benchmark.corpus import Corpus  # noqa: E402
from app.benchmark.schema import canonical_geography  # noqa: E402
from app.fingerprint.llm import LLMFingerprinter, squash  # noqa: E402
from app.parsing.documents import DocumentParser  # noqa: E402
from eval_pipeline import load_jsonl  # noqa: E402

OWN = {"own_revenue", "subtotal_of_geographies"}


def _locate(doc, quote: str) -> tuple[int, int] | None:
    """(grid index, row index) of the row that prints the quote's leading cells."""
    wanted = squash(quote)
    if not wanted:
        return None
    best: tuple[int, int] | None = None
    for gi, rows in enumerate(doc.tables or []):
        for ri, row in enumerate(rows):
            printed = squash(" ".join(row))
            if printed and (printed.startswith(wanted[: max(12, len(printed) // 2)]) or wanted.startswith(printed[:24]) and len(printed) > 12):
                if wanted[:24] == printed[:24]:
                    return gi, ri
                best = best or (gi, ri)
    return best


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", action="append")
    parser.add_argument("--rendering", choices=("markdown", "raw"), default="markdown")
    parser.add_argument("--json")
    parser.add_argument("--show-misses", action="store_true")
    parser.add_argument("--model")
    args = parser.parse_args()

    quarterly = load_jsonl("quarterly_revenue.jsonl")
    annual = load_jsonl("annual_revenue.jsonl")
    if args.product:
        quarterly = [r for r in quarterly if r["drug_name"] in set(args.product)]
    catalog: dict[str, list[str]] = defaultdict(list)
    for row in load_jsonl("quarterly_revenue.jsonl") + annual:
        if row["drug_name"] not in catalog[row["manufacturer"]]:
            catalog[row["manufacturer"]].append(row["drug_name"])
    generics = {r["drug_name"]: r.get("generic_name") for r in load_jsonl("quarterly_revenue.jsonl") + annual}

    corpus = Corpus(rendering=args.rendering)
    doc_parser = DocumentParser(corpus.file_store())
    fingerprinter = LLMFingerprinter(model=args.model, max_calls=10**6)
    print(f"contract v{fingerprinter.version}; strong={fingerprinter.model} fast={fingerprinter.fast_model}")

    parsed: dict[str, object] = {}
    described: dict[str, object] = {}
    outcomes: Counter = Counter()
    misses: list[dict] = []
    by_product: dict[str, Counter] = defaultdict(Counter)
    tiers: Counter = Counter()
    rejected: Counter = Counter()

    async def fingerprint(url: str, issuer: str):
        if url in described:
            return described[url]
        source = corpus.source_for(url)
        doc = await doc_parser.parse(source) if source else None
        parsed[url] = doc
        if doc is None or doc.parsing_status.value != "success":
            described[url] = None
            return None
        result = await fingerprinter.fingerprint(
            doc, products=catalog[issuer], generics=generics, title=source.title or "", url=url,
        )
        for tier, count in result.tiers.items():
            tiers[tier] += count
        for reason in result.rejected:
            rejected[reason.split(":")[-1].split("(")[0]] += 1
        described[url] = result
        return result

    # Describe every cited document up front, as many at a time as the
    # fingerprinter's own concurrency allows; the checks below then read cache.
    todo = {row["source_url"]: row["manufacturer"] for row in quarterly}
    await asyncio.gather(*(fingerprint(url, issuer) for url, issuer in todo.items()))

    for row in quarterly:
        url = row["source_url"]
        product = row["drug_name"]
        result = await fingerprint(url, row["manufacturer"])
        doc = parsed.get(url)
        if doc is None:
            outcomes["document_unreadable"] += 1
            by_product[product]["document_unreadable"] += 1
            continue
        location = _locate(doc, row["source_quote"])
        if location is None:
            outcomes["quote_not_in_a_grid"] += 1
            by_product[product]["quote_not_in_a_grid"] += 1
            continue
        gi, ri = location
        region = result.grid(gi) if result else None
        if region is None:
            outcome = "grid_not_described"
        else:
            rows = [r for r in region.rows if r.row_index == ri]
            if not rows:
                outcome = "row_not_described"
            else:
                r = rows[0]
                want_geo = canonical_geography(row.get("geography"))
                have_geo = canonical_geography(r.geography) if r.geography else "unspecified"
                if r.product.lower() not in {product.lower(), (row.get("generic_name") or "").lower()}:
                    outcome = f"wrong_product({r.product})"
                elif r.line not in OWN:
                    outcome = f"line_kind({r.line})"
                elif have_geo not in {want_geo, "unspecified"} and want_geo != "unspecified":
                    outcome = f"geography({r.geography})"
                else:
                    outcome = "described"
        outcomes[outcome.split("(")[0]] += 1
        by_product[product][outcome.split("(")[0]] += 1
        if outcome != "described":
            misses.append({"product": product, "period": row["period"], "url": url, "grid": gi, "row": ri,
                           "outcome": outcome, "quote": row["source_quote"][:100]})

    total = sum(outcomes.values())
    print(f"\ngold quarterly rows checked: {total}")
    for key, count in outcomes.most_common():
        print(f"  {key:28} {count:5}  {100 * count / total:5.1f}%")
    located = total - outcomes["quote_not_in_a_grid"] - outcomes["document_unreadable"]
    print(f"\ndescribed / rows located in a grid: {outcomes['described']}/{located} = {100 * outcomes['described'] / located if located else 0:.1f}%")
    print(f"model tiers used: {dict(tiers)}; calls this run: {fingerprinter.calls}")
    print(f"parser rejections by kind: {dict(rejected.most_common(12))}")
    print("\nby product:")
    for product, counts in sorted(by_product.items()):
        n = sum(counts.values())
        print(f"  {product:18} {counts['described']:4}/{n:<4} {dict(counts)}")
    if args.show_misses:
        for m in misses[:200]:
            print(f"  MISS {m['product']:14} {m['period']} grid{m['grid']} r{m['row']} {m['outcome']:32} {m['quote']!r}")
    if args.json:
        Path(args.json).write_text(json.dumps({"outcomes": dict(outcomes), "by_product": {k: dict(v) for k, v in by_product.items()},
                                               "misses": misses, "tiers": dict(tiers), "rejected": dict(rejected)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
