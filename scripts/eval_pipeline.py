"""Run the whole pipeline end to end on a case file, and score it.

    cd backend && uv run python ../scripts/eval_pipeline.py --cases ../seed/holdout_pipeline/cases.jsonl --out /tmp/e2e.jsonl
    cd backend && uv run python ../scripts/eval_pipeline.py --gold-sample 30 --seed 71 --out /tmp/e2e_gold.jsonl

Two sources of cases:

  --cases   a held-out file (rows: product, period, issuer, expect value |
            not_reported, value, unit, currency). This is the score (CLAUDE.md
            rule 4): issuers no answer key uses, labelled by reading the filing.
  --gold-sample N
            N gold quarters, one per product, drawn with --seed across gold's
            own fields (directly printed and derived; SEC and non-SEC sources).
            Gold is a defect oracle here, never the score.

Outcomes per case: `correct` (equal within the expected figure's last printed
digit, or the answer's own declared spread, same currency), `wrong`,
`declined`, `needs_review` (a figure whose source refused the client),
`correct_refusal` / `false_find` for not-reported cases, `error`. Totals are
printed per route, so a gain or a defect can be traced to the route that made
it.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GOLD = REPO / "seed" / "gold"
sys.path.insert(0, str(REPO / "backend"))

from app.retrieval.pipeline import quarter_revenue  # noqa: E402

SCALE = {"units": 1.0, "thousands": 1e3, "millions": 1e6, "billions": 1e9}


def gold_sample(n: int, seed: int) -> list[dict]:
    rows = [json.loads(line) for line in (GOLD / "quarterly_revenue.jsonl").open()]
    rng, used, cases = random.Random(seed), set(), []
    for r in rng.sample(rows, len(rows)):
        if len(cases) == n:
            break
        if r["drug_name"] in used:
            continue
        used.add(r["drug_name"])
        cases.append({"product": r["drug_name"], "period": f"Q{r['calendar_quarter']} {r['calendar_year']}",
                      "issuer": r.get("manufacturer"), "expect": "value", "value": r["source_value_reported"],
                      "unit": r["source_unit"], "currency": r["currency"], "scope": r.get("revenue_scope"),
                      "kind": r["derivation"], "source_url": r["source_url"]})
    return cases


def _half_last_digit(value: float, unit: str) -> float:
    text = repr(float(value))
    decimals = len(text.split(".")[1].rstrip("0")) if "." in text else 0
    return 0.5 * 10 ** -decimals * SCALE.get(unit, 1.0)


# A case scope that opens with the United States ("U.S.", "Total U.S. ...",
# "United States revenue for ..."); one joining another territory to it
# ("U.S. and Canada") is not U.S. alone.
_US_OPENING = re.compile(r"^\s*(?:total\s+)?(?:u\.\s?s\.|us\b|united states\b)", re.IGNORECASE)
_JOINED = re.compile(r"^\s*(?:and\b|plus\b|&)", re.IGNORECASE)


def question_scope(case: dict) -> str:
    """The pipeline scope a case asks for, from its scope text."""
    text = str(case.get("scope") or "")
    opening = _US_OPENING.match(text)
    return "us" if opening and not _JOINED.match(text[opening.end():]) else "worldwide"


def run(case: dict, allow_search: bool) -> dict:
    started = time.monotonic()
    try:
        result = quarter_revenue(case["product"], case["period"], case.get("issuer"), allow_search=allow_search,
                                 scope=question_scope(case))
    except Exception as exc:
        return {**case, "outcome": "error", "route": None, "error": f"{type(exc).__name__}: {exc}"[:300]}
    out = {**case, "seconds": round(time.monotonic() - started, 1), "result": asdict(result), "route": result.route}
    if case["expect"] == "not_reported":
        return {**out, "outcome": "false_find" if result.status == "found" else "correct_refusal"}
    if result.status == "needs_review":
        return {**out, "outcome": "needs_review"}
    if result.status != "found":
        return {**out, "outcome": "declined"}
    expected = float(case["value"]) * SCALE.get(str(case["unit"]).lower(), 1.0)
    slack = _half_last_digit(case["value"], str(case["unit"]).lower()) + (result.plus_minus or 0.0) + 1e-6
    same_currency = (result.currency or "").upper() == str(case["currency"]).upper()
    return {**out, "outcome": "correct" if same_currency and abs(result.value - expected) <= slack else "wrong"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--gold-sample", type=int)
    parser.add_argument("--seed", type=int, default=71)
    parser.add_argument("--no-search", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if bool(args.cases) == bool(args.gold_sample):
        parser.error("give exactly one of --cases or --gold-sample")

    cases = ([json.loads(line) for line in args.cases.open() if line.strip()] if args.cases
             else gold_sample(args.gold_sample, args.seed))
    source = str(args.cases) if args.cases else f"gold sample n={args.gold_sample} seed={args.seed} (defect oracle)"
    print(f"cases={len(cases)} source={source} search={'off' if args.no_search else 'on'} workers={args.workers}")
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda c: run(c, not args.no_search), cases))
    args.out.write_text("".join(json.dumps(r, default=str) + "\n" for r in results))

    print(f"\n{'outcome':<16} {'route':<38} {'product':<24} {'period':<8} {'expected':>14} {'answer':>16}")
    for r in results:
        res = r.get("result") or {}
        expected = "" if r["expect"] != "value" else f"{r['value']} {r['unit']} {r['currency']}"
        answer = "" if res.get("value") is None else f"{res['value']:.4g} {res.get('currency')}"
        print(f"{r['outcome']:<16} {str(r.get('route') or r.get('error', ''))[:38]:<38} {r['product'][:24]:<24} "
              f"{r['period']:<8} {expected[:14]:>14} {answer:>16}")
    totals = Counter(r["outcome"] for r in results)
    by_route = defaultdict(Counter)
    for r in results:
        by_route[str(r.get("route"))][r["outcome"]] += 1
    print(f"\ntotals: {dict(totals)} of {len(results)}")
    for route, counts in sorted(by_route.items()):
        print(f"  {route:<40} {dict(counts)}")
    print(f"saved: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
