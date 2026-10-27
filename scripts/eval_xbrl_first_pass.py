"""Run the XBRL first pass on a seeded sample of gold quarters and compare.

    cd backend && uv run python ../scripts/eval_xbrl_first_pass.py --out /tmp/xbrl.jsonl

Gold is the oracle for finding defects here, not a held-out score (CLAUDE.md
rule 4). The sample is drawn from directly reported Q1-Q3 rows, one per
product, from two pools defined on gold's own fields: rows citing an SEC
document for 2019 or later, where 10-Q XBRL is expected, and every other row,
where it mostly is not - so both a found figure and a correct absence are
exercised. The first pass is given the product and the quarter only.

Each row is one of: `correct` (found, equal to gold within gold's last printed
digit), `correct_us_part` (gold is a U.S.-scope series and the found figure's
U.S. part equals it), `scope_differs` (gold is U.S.-scope, the filer tags only
a larger worldwide total), `wrong` (found, unequal - the outcome that matters most), `declined`
(nothing found). With `--issuer`, the issuer gold names is passed, which says
whose figure is wanted when a partner also tags the product.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GOLD = REPO / "seed" / "gold"
sys.path.insert(0, str(REPO / "backend"))

from app.retrieval.xbrl import first_pass  # noqa: E402

ISSUER = False
SCALE = {"units": 1e-6, "thousands": 1e-3, "millions": 1.0, "billions": 1e3}


def pick(seed: int, xbrl_era: int, other: int) -> list[dict]:
    rows = [json.loads(line) for line in (GOLD / "quarterly_revenue.jsonl").open()]
    rows = [r for r in rows if r["derivation"] == "direct_reported" and r["calendar_quarter"] < 4]
    in_era = [r for r in rows if "sec.gov/Archives" in r["source_url"] and r["calendar_year"] >= 2019]
    era_ids = {r["gold_id"] for r in in_era}
    rest = [r for r in rows if r["gold_id"] not in era_ids]
    rng, used, cases = random.Random(seed), set(), []
    for pool_name, pool, n in (("sec_2019_on", in_era, xbrl_era), ("other", rest, other)):
        taken = 0
        for r in rng.sample(pool, len(pool)):
            if taken == n:
                break
            if r["drug_name"] in used:
                continue
            used.add(r["drug_name"])
            taken += 1
            cases.append({
                "pool": pool_name, "gold_id": r["gold_id"], "product": r["drug_name"],
                "period": f"Q{r['calendar_quarter']} {r['calendar_year']}", "gold_value": r["source_value_reported"],
                "gold_unit": r["source_unit"], "gold_currency": r["currency"], "gold_issuer": r.get("manufacturer"),
                "gold_scope": r.get("revenue_scope"), "gold_url": r["source_url"],
            })
    return cases


def _half_last_digit(value: float, unit: str) -> float:
    text = repr(float(value))
    decimals = len(text.split(".")[1].rstrip("0")) if "." in text else 0
    return 0.5 * 10 ** -decimals * SCALE[unit]


def run(case: dict) -> dict:
    started = time.monotonic()
    try:
        answer = first_pass(case["product"], case["period"], case["gold_issuer"] if ISSUER else None)
    except Exception as exc:
        return {**case, "outcome": "error", "error": f"{type(exc).__name__}: {exc}"}
    result = {**case, "seconds": round(time.monotonic() - started, 1), "answer": asdict(answer)}
    if answer.status != "found":
        return {**result, "outcome": "declined"}
    gold_m = case["gold_value"] * SCALE[case["gold_unit"]]
    found_m = answer.value / 1e6
    same_currency = (answer.unit or "").split(":")[-1].upper() == case["gold_currency"].upper()
    equal = same_currency and abs(found_m - gold_m) <= _half_last_digit(case["gold_value"], case["gold_unit"]) + 1e-9
    if equal:
        return {**result, "found_millions": found_m, "outcome": "correct"}
    # Gold records some series at U.S. scope where the filer also tags the
    # product's worldwide total; the U.S. part is then the comparable figure.
    if same_currency and case["gold_scope"] in ("U.S.", "United States", "US"):
        us = answer.by_geography.get("country:US")
        if us is not None and abs(us / 1e6 - gold_m) <= _half_last_digit(case["gold_value"], case["gold_unit"]) + 1e-9:
            return {**result, "found_millions": us / 1e6, "outcome": "correct_us_part"}
        if us is None and found_m > gold_m:
            # Only the worldwide total is tagged; the U.S. figure is not in XBRL.
            return {**result, "found_millions": found_m, "outcome": "scope_differs"}
    return {**result, "found_millions": found_m, "outcome": "wrong"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--sec-2019-on", type=int, default=20)
    parser.add_argument("--other", type=int, default=10)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--issuer", action="store_true", help="pass gold's issuer, naming whose figure is wanted")
    args = parser.parse_args()
    global ISSUER
    ISSUER = args.issuer

    cases = pick(args.seed, args.sec_2019_on, args.other)
    print(f"seed={args.seed} cases={len(cases)} (sec_2019_on={args.sec_2019_on}, other={args.other}) "
          f"workers={args.workers} issuer={'gold' if args.issuer else 'none'}")
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(run, cases))
    args.out.write_text("".join(json.dumps(r) + "\n" for r in results))

    print(f"\n{'pool':<12} {'product':<26} {'period':<8} {'outcome':<9} {'status':<20} {'gold':>12} {'xbrl':>12}  member")
    for r in results:
        a = r.get("answer") or {}
        gold = f"{r['gold_value'] * SCALE[r['gold_unit']]:.1f} {r['gold_currency']}"
        found = f"{r['found_millions']:.1f} {a.get('unit')}" if "found_millions" in r else ""
        print(f"{r['pool']:<12} {r['product'][:26]:<26} {r['period']:<8} {r['outcome']:<9} "
              f"{(a.get('status') or r.get('error', ''))[:20]:<20} {gold:>12} {found:>12}  {a.get('member') or ''}")
    for pool_name in ("sec_2019_on", "other"):
        sub = [r for r in results if r["pool"] == pool_name]
        counts = {k: sum(r["outcome"] == k for r in sub)
                  for k in ("correct", "correct_us_part", "scope_differs", "wrong", "declined", "error")}
        print(f"{pool_name}: {counts} of {len(sub)}")
    print(f"saved: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
