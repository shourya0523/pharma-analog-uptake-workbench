"""Run the pipeline end to end over the gold corpus and score it row by row.

Unlike ``eval_extraction.py`` (which replays each gold quote through one
reader) and ``eval_completeness.py`` (which checks what derivation could add
to the gold rows), this script gives the pipeline nothing but documents: the
issuer's filings and releases the gold rows cite, exactly as retrieved. The
pipeline parses them, reads them, reconciles them across documents, derives
what they determine, and produces a series. That series and the gold rows are
both reduced to ``app.benchmark.schema.ComparableRevenueRow`` and compared.

The document set handed to the pipeline for a product is every corpus
document cited by any gold row of the same issuer, not only the rows of that
product - the pipeline has to find the right line in the right document, and
must not be misled by the others.

Usage:
    cd backend && uv run python ../scripts/eval_pipeline.py [--product NAME] [--show-failures] [--json PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.benchmark.corpus import Corpus, GOLD_DIR  # noqa: E402
from app.benchmark.schema import Comparison, compare, from_gold, from_series  # noqa: E402
from app.extraction.readers import Observation, read_document  # noqa: E402
from app.fingerprint.llm import Fingerprint, LLMFingerprinter  # noqa: E402
from app.extraction.series import Series, assemble_series, propagate_family  # noqa: E402
from app.parsing.documents import DocumentParser  # noqa: E402


def load_jsonl(name: str) -> list[dict]:
    path = GOLD_DIR / name
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def product_relations() -> dict[str, str]:
    """product -> family parent, from the catalog's formulation attributes."""
    relations: dict[str, str] = {}
    path = REPO_ROOT / "seed" / "product_attributes.csv"
    if not path.exists():
        return relations
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            role = row.get("peer_universe_role") or ""
            if role.startswith("formulation_of:"):
                relations[row["drug_name"]] = role.split(":", 1)[1]
    return relations


def sibling_formulation_periods(parent_observations: list[Observation], *, family: str, product: str) -> list[str]:
    """Periods in which the family line is stated for a formulation other than ``product``."""
    periods: set[str] = set()
    own = product.lower()
    fam = family.lower()
    for obs in parent_observations:
        label = obs.product_label.lower()
        if obs.method != "grid" or obs.period_type != "quarterly" or obs.specificity == 0:
            continue
        if len(label.split()) > 6:
            continue
        if label == own or own in label:
            continue
        if fam not in label or label.startswith("total"):
            continue
        periods.add(obs.period)
    return sorted(periods)


class Runner:
    def __init__(self, corpus: Corpus, *, fingerprinter: LLMFingerprinter | None = None,
                 catalog: dict[str, list[str]] | None = None, generics: dict[str, str | None] | None = None) -> None:
        self.corpus = corpus
        self.parser = DocumentParser(corpus.file_store())
        self.fingerprinter = fingerprinter
        self.catalog = catalog or {}          # issuer -> products
        self.generics = generics or {}
        self._parsed: dict[str, object] = {}
        self._fingerprints: dict[str, Fingerprint] = {}
        self.fingerprint_stats = {"documents": 0, "cached": 0, "grids": 0, "prose": 0}

    async def parsed(self, url: str):
        if url not in self._parsed:
            source = self.corpus.source_for(url)
            self._parsed[url] = (source, await self.parser.parse(source)) if source else (None, None)
        return self._parsed[url]

    async def fingerprint(self, url: str, issuer: str | None):
        if self.fingerprinter is None:
            return None
        if url not in self._fingerprints:
            source, doc = await self.parsed(url)
            if doc is None:
                self._fingerprints[url] = Fingerprint()
            else:
                products = self.catalog.get(issuer or "", [])
                result = await self.fingerprinter.fingerprint(
                    doc, products=products, generics=self.generics, title=(source.title or "") if source else "", url=url
                )
                self._fingerprints[url] = result
                self.fingerprint_stats["documents"] += 1
                self.fingerprint_stats["cached"] += int(result.cached)
                self.fingerprint_stats["grids"] += len(result.grids)
                self.fingerprint_stats["prose"] += len(result.prose)
        return self._fingerprints[url]

    async def observe(self, product: str, generic: str | None, urls: list[str], issuer: str | None = None) -> tuple[list[Observation], dict[str, list[str]]]:
        observations: list[Observation] = []
        skipped: dict[str, list[str]] = {}
        # Fingerprint the issuer's documents concurrently, once each.
        if self.fingerprinter is not None:
            await asyncio.gather(*(self.fingerprint(url, issuer) for url in urls))
        for url in urls:
            source, doc = await self.parsed(url)
            if doc is None or doc.parsing_status.value != "success":
                skipped[url] = ["not_parsed"]
                continue
            fingerprint = await self.fingerprint(url, issuer)
            report = read_document(
                doc, product=product, generic=generic, source_url=url, fingerprint=fingerprint,
                issuer_products=self.catalog.get(issuer or "", []),
            )
            observations.extend(report.observations)
            if report.skipped:
                skipped[url] = report.skipped
        return observations, skipped


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", action="append", help="restrict to one or more drug_name values")
    parser.add_argument("--show-failures", action="store_true")
    parser.add_argument("--max-failures", type=int, default=12)
    parser.add_argument("--json", help="write every pipeline row and comparison to this path")
    parser.add_argument("--rendering", choices=("raw", "markdown"), default="raw",
                        help="raw: the bytes the pipeline fetched (falls back per document); markdown: the committed text rendering")
    parser.add_argument("--fingerprinter", choices=("grammar", "llm"), default="grammar",
                        help="llm: ask the model where and how each document states revenue, verified per row; grammar: header grammar only")
    parser.add_argument("--model", help="OpenRouter model for the fingerprinter")
    args = parser.parse_args()

    quarterly = load_jsonl("quarterly_revenue.jsonl")
    annual = load_jsonl("annual_revenue.jsonl")
    coverage = load_jsonl("series_coverage.jsonl")
    relations = product_relations()
    corpus = Corpus(rendering=args.rendering)
    print(f"rendering: {args.rendering} ({sum(1 for d in corpus.documents if d.fetched_via == 'pipeline_http')} raw, "
          f"{sum(1 for d in corpus.documents if d.fetched_via != 'pipeline_http')} text)")
    catalog: dict[str, list[str]] = defaultdict(list)
    for row in quarterly + annual:
        if row["drug_name"] not in catalog[row["manufacturer"]]:
            catalog[row["manufacturer"]].append(row["drug_name"])
    generics_all = {row["drug_name"]: row.get("generic_name") for row in quarterly + annual}
    fingerprinter = LLMFingerprinter(model=args.model) if args.fingerprinter == "llm" else None
    runner = Runner(corpus, fingerprinter=fingerprinter, catalog=dict(catalog), generics=generics_all)
    if fingerprinter is not None:
        print(f"fingerprinter: {fingerprinter.model} (enabled={fingerprinter.enabled})")

    issuer_of = {row["drug_name"]: row["manufacturer"] for row in quarterly + annual}
    generic_of = {row["drug_name"]: row.get("generic_name") for row in quarterly + annual}
    urls_by_issuer: dict[str, set[str]] = defaultdict(set)
    for row in quarterly + annual:
        for source in row.get("sources") or [{"source_url": row["source_url"]}]:
            urls_by_issuer[row["manufacturer"]].add(source["source_url"])
        for component in row.get("bridge_components") or []:
            if component.get("source_url"):
                urls_by_issuer[row["manufacturer"]].add(component["source_url"])

    products = [c["drug_name"] for c in sorted(coverage, key=lambda c: c["drug_name"])]
    if args.product:
        products = [p for p in products if p in set(args.product)]
    # A family parent must be assembled before the formulation that inherits from it.
    ordered = sorted(products, key=lambda p: (p in relations, p))
    for parent in set(relations.values()):
        if parent not in ordered and parent in issuer_of:
            ordered.insert(0, parent)

    gold_rows = {p: [from_gold(r) for r in quarterly if r["drug_name"] == p] for p in products}
    series_by_product: dict[str, Series] = {}
    comparisons: dict[str, list[Comparison]] = {}
    pipeline_rows: dict[str, list] = {}
    skipped_by_product: dict[str, dict[str, list[str]]] = {}

    observations_by_product: dict[str, list[Observation]] = {}
    for product in ordered:
        issuer = issuer_of.get(product)
        urls = sorted(urls_by_issuer.get(issuer, set()))
        observations, skipped = await runner.observe(product, generic_of.get(product), urls, issuer)
        observations_by_product[product] = observations
        series = assemble_series(observations, product=product)
        parent = relations.get(product)
        if parent and parent in series_by_product:
            # The split is visible in the documents themselves: the first
            # period in which the family's line is broken out into a
            # formulation other than this one.
            sibling_periods = sibling_formulation_periods(
                observations_by_product.get(parent, []), family=parent, product=product
            )
            own_periods = {v.period for v in series.values if v.period_type == "quarterly"}
            for value in propagate_family(series_by_product[parent], product=product, sibling_periods=sibling_periods):
                if value.period not in own_periods:
                    series.values.append(value)
        series_by_product[product] = series
        skipped_by_product[product] = skipped
        rows = [from_series(v) for v in series.values] + [from_series(v) for v in series.verdicts]
        pipeline_rows[product] = rows
        if product in gold_rows:
            comparisons[product] = [compare(g, rows) for g in gold_rows[product]]

    print("pipeline delivery against the gold dataset, from documents alone")
    print(f"{'product':18} {'gold':>5} {'match':>6} {'value':>6} {'geo':>5} {'review':>7} {'missing':>8} {'delivered':>10}")
    print("-" * 74)
    totals = defaultdict(int)
    by_issuer: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    failures: list[Comparison] = []
    for product in products:
        results = comparisons.get(product, [])
        counts = defaultdict(int)
        for result in results:
            counts[result.outcome] += 1
            totals[result.outcome] += 1
            if result.outcome != "match":
                failures.append(result)
        n = len(results)
        stat = by_issuer[issuer_of.get(product, "?")]
        stat[0] += n
        stat[1] += counts["match"]
        flag = "" if counts["match"] == n else "  <--"
        print(
            f"{product:18} {n:>5} {counts['match']:>6} {counts['value_mismatch']:>6} "
            f"{counts['geography_mismatch']:>5} {counts['needs_review']:>7} {counts['missing']:>8} "
            f"{100 * counts['match'] / n if n else 0:>9.1f}%{flag}"
        )
    n = sum(totals.values())
    print("-" * 74)
    print(
        f"{'ALL QUARTERLY':18} {n:>5} {totals['match']:>6} {totals['value_mismatch']:>6} "
        f"{totals['geography_mismatch']:>5} {totals['needs_review']:>7} {totals['missing']:>8} "
        f"{100 * totals['match'] / n if n else 0:>9.1f}%"
    )
    print(f"\n{'issuer':24} {'gold':>6} {'delivered':>10}")
    for issuer, (count, ok) in sorted(by_issuer.items(), key=lambda kv: -kv[1][0]):
        print(f"{issuer:24} {count:>6} {100 * ok / count if count else 0:>9.1f}%")

    routes = defaultdict(int)
    for product in products:
        for result in comparisons.get(product, []):
            if result.outcome == "match" and result.pipeline:
                routes[(result.gold.route, result.pipeline.route)] += 1
    if fingerprinter is not None:
        print(f"\nfingerprints: {runner.fingerprint_stats}")
    print("\nmatched rows by route (gold -> pipeline)")
    for (g, p), count in sorted(routes.items()):
        print(f"  {g:>10} -> {p:<10} {count}")

    if failures and args.show_failures:
        print(f"\n{len(failures)} rows not delivered:")
        for result in failures[: args.max_failures]:
            g = result.gold
            print(f"  {g.product:16} {g.period:8} [{result.outcome}] gold={g.value_usd_millions:g} {g.geography} {g.derivation}")
            print(f"      {result.detail[:200]}")
            if result.pipeline:
                print(f"      pipeline quote: {result.pipeline.source_quote[:140]!r}")
            else:
                same = [r for r in pipeline_rows[g.product] if r.period == g.period]
                if same:
                    print(f"      pipeline has for this period: {[(r.geography, r.value_usd_millions, r.status) for r in same][:6]}")
                doc_skips = skipped_by_product.get(g.product, {}).get(g.source_urls[0], [])
                if doc_skips:
                    print(f"      reader skipped in cited document: {doc_skips[:4]}")

    if args.json:
        payload = {
            "pipeline_rows": {p: [r.as_dict() for r in rows] for p, rows in pipeline_rows.items()},
            "comparisons": {
                p: [{"gold": c.gold.as_dict(), "pipeline": c.pipeline.as_dict() if c.pipeline else None,
                     "outcome": c.outcome, "detail": c.detail} for c in results]
                for p, results in comparisons.items()
            },
        }
        Path(args.json).write_text(json.dumps(payload, indent=1))
    return 0 if n and totals["match"] == n else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
