"""Can the model reader do what the table reader cannot, on non-US filers?

The deterministic reader refuses every table in these documents. Novartis,
Sanofi and Novo Nordisk state the period, the unit and often the currency in
the document's heading rather than in the table, so `fingerprint` returns
`no_period_header` for all 99 tables across the three and reads nothing.

That is exactly the case the model reader exists for: it sits below a table
and above a sentence in `CLAIM_STRENGTH`, and it reads running text rather
than geometry. Whether it handles this document class has never been measured,
and the answer decides something real - whether to teach the fingerprint a new
shape, or to route these filings to the reader already built for them.

`seed/holdout2` is the answer key: 126 quarters across Wegovy, Dupixent and
Kisqali, every row citing an SEC document. It has never scored anything - the
three files that mention it all name it only as issuers to exclude from some
other holdout.

Scored on the figure as reported, in the filer's own currency and millions,
because that is what the answer key records: it leaves `source_currency` null
and normalizes nothing, since the currency is not in the table either.

Scored on the answer key's 27 worldwide rows rather than all 126. The other 99
are the regional breakdowns these filers print beside each total - United
States, EUCAN, Region China - and every reader in this pipeline returns the
worldwide figure only, so scoring against them would be measuring a question
nobody asked. The same exclusion is applied when gold scores the SEC path.

What the number is worth, as of the last run: 16/27, up from 9/27. That rise
came from teaching `detect_period_context` the quarter notation, and that rule
was developed against these same 25 documents - so the second number is
in-sample and this set is spent as a scorer for anything touching document
dating. The 9/27 was clean. A further change to the reader needs a set drawn
from issuers this one does not use.

    OPENROUTER_API_KEY=... python scripts/eval_foreign_filer_reader.py [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.domain.models import (  # noqa: E402
    RetrievalStatus,
    RetrievedSource,
    SourceType,
)
from app.llm.client import LLMModules  # noqa: E402
from app.parsing.documents import DocumentParser  # noqa: E402
from app.parsing.evidence import build_revenue_llm_text  # noqa: E402
from app.parsing.periods import detect_period_context, normalize_period  # noqa: E402
from app.quality.candidate_filters import filter_revenue_candidates  # noqa: E402
from app.storage.filestore import LocalFileStore  # noqa: E402

HOLDOUT = REPO_ROOT / "seed" / "holdout2" / "quarterly_revenue.jsonl"
CACHE = Path("/tmp/holdout2-docs")
UA = "pharma-analog-research mehr.anand@bitsdime.com"
# The answer key records whole millions; agreement is the same figure, not a
# near one, because these are printed numbers rather than derived ones.
TOLERANCE = 0.51


def fetch(url: str) -> str | None:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / (re.sub(r"[^A-Za-z0-9]", "_", url)[-120:] + ".htm")
    if not path.exists():
        rc = subprocess.run(
            ["curl", "-sS", "--max-time", "180", "-A", UA, "-o", str(path), url,
             "-w", "%{http_code}"], capture_output=True, text=True)
        if rc.stdout.strip() != "200":
            path.unlink(missing_ok=True)
            return None
        time.sleep(0.1)
    return path.read_text(errors="ignore")


async def run(limit: int | None = None) -> dict:
    rows = [json.loads(line) for line in HOLDOUT.read_text().splitlines() if line.strip()]
    # (url, product) -> {period: value as reported}
    wanted: dict[tuple[str, str], dict[str, float]] = collections.defaultdict(dict)
    issuer_of: dict[tuple[str, str], str] = {}
    for row in rows:
        if row.get("geography") != "Worldwide":
            continue
        key = (row["source_url"], row["drug_name"])
        wanted[key][row["period"]] = row.get("source_value_reported")
        issuer_of[key] = row["manufacturer"]

    jobs = sorted(wanted)[:limit]
    parser = DocumentParser(LocalFileStore("/tmp/holdout2-store"))
    llm = LLMModules()
    results = []

    for url, product in jobs:
        html = fetch(url)
        if not html:
            results.append({"product": product, "url": url, "error": "fetch failed",
                            "wanted": len(wanted[(url, product)]), "matched": 0})
            print(f"  {product:9} FETCH FAILED {url[-48:]}", flush=True)
            continue
        source = RetrievedSource(
            source_id="s1", source_type=SourceType.SEC_FILING, url=url,
            filing_type="6-K", retrieval_status=RetrievalStatus.SUCCESS,
            raw_text=html,
        )
        doc = await parser.parse(source)
        period_context = detect_period_context(doc.full_text)
        llm_text, evidence = build_revenue_llm_text(doc, product=product, generic=None)
        try:
            out = await llm.extract_revenue(
                product=product, company=issuer_of[(url, product)],
                source_meta={
                    "url": url, "type": "sec_filing", "filing_type": "6-K",
                    "evidence": evidence,
                    "reporting_period": period_context.describe() if period_context else None,
                    "period_columns": (
                        [str(period_context.year), str(period_context.comparative_year)]
                        if period_context else None),
                },
                text=llm_text,
            )
        except Exception as exc:  # noqa: BLE001 - a failed call is a failed document
            results.append({"product": product, "url": url,
                            "error": f"{type(exc).__name__}: {exc}",
                            "wanted": len(wanted[(url, product)]), "matched": 0})
            print(f"  {product:9} ERROR {type(exc).__name__}", flush=True)
            continue

        span_corpus = "\n\n".join(s.get("span_text") or ""
                                  for s in (out.get("spans") or [])) or llm_text
        kept, dropped = filter_revenue_candidates(
            out.get("candidates") or [], product=product, source_text=span_corpus)
        # The model answers "Q1 2025"; the key is written "2025Q1". The
        # orchestrator canonicalises every model period the same way before
        # storing it, so the eval has to as well or it scores its own
        # formatting rather than the reader.
        got: dict[str, object] = {}
        for c in kept:
            period = normalize_period(
                c.get("period"), period_type=c.get("period_type"),
                context=period_context)
            if period and period not in got:
                got[period] = c.get("value_reported")
        key_rows = wanted[(url, product)]
        matched, wrong, absent = [], [], []
        for period, value in sorted(key_rows.items()):
            if period not in got:
                absent.append(period)
            elif value is not None and got[period] is not None and \
                    abs(float(got[period]) - float(value)) <= TOLERANCE:
                matched.append(period)
            else:
                wrong.append((period, got[period], value))
        results.append({
            "product": product, "url": url, "wanted": len(key_rows),
            "offered": len(out.get("candidates") or []), "kept": len(kept),
            "dropped": len(dropped), "matched": len(matched),
            "wrong": [[p, g, w] for p, g, w in wrong], "absent": absent,
            "period_context": period_context.describe() if period_context else None,
        })
        print(f"  {product:9} {url[-34:]:36} key={len(key_rows):2} "
              f"offered={len(out.get('candidates') or []):2} kept={len(kept):2} "
              f"match={len(matched):2} wrong={len(wrong):2} missing={len(absent):2}",
              flush=True)

    total_key = sum(r["wanted"] for r in results)
    total_match = sum(r.get("matched", 0) for r in results)
    total_wrong = sum(len(r.get("wrong", [])) for r in results)
    by_product: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for r in results:
        by_product[r["product"]][0] += r.get("matched", 0)
        by_product[r["product"]][1] += r["wanted"]

    print("\nMODEL READER ON NON-US FILERS, scored against seed/holdout2\n")
    for product, (m, w) in sorted(by_product.items()):
        print(f"  {product:10} {m:3}/{w:<3} ({m / w * 100:.0f}%)" if w else f"  {product}: -")
    print(f"\n  TOTAL    {total_match}/{total_key}"
          f"  ({total_match / total_key * 100:.1f}%)   wrong values: {total_wrong}")
    return {"documents": len(results), "matched": total_match, "key_rows": total_key,
            "wrong": total_wrong,
            "by_product": {k: v for k, v in by_product.items()}, "results": results}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    summary = asyncio.run(run(args.limit))
    out = REPO_ROOT / "exports" / "foreign_filer_reader.json"
    if out.parent.exists():
        out.write_text(json.dumps(summary, indent=1) + "\n")
    raise SystemExit(0)
