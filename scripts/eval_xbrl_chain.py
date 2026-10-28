"""Run the whole XBRL path - first pass, single-product reader, derivation -
on a small seeded batch of gold quarters, and compare.

    cd backend && uv run python ../scripts/eval_xbrl_chain.py --pool q4 --n 10 --out /tmp/q4.jsonl

Gold is the oracle for finding defects, not a held-out score (CLAUDE.md
rule 4). Pools, each defined on gold's own fields and drawn one row per
product:

  q4      calendar Q4 rows of U.S.-dollar series citing an SEC document -
          the quarters no 10-Q states, so derivation is what is exercised.
  q1_q3   directly reported Q1-Q3 rows of U.S.-dollar series citing an SEC
          document - where a filer that tags no product sends the row to the
          single-product reader.

The issuer gold names is passed, saying whose figure is wanted. The
single-product reader confirms with Gemini (`GEMINI_API_KEY`), without search.

Outcomes: `correct` (equal to gold within gold's last printed digit),
`correct_us_part` (gold is U.S.-scope and the figure's U.S. part equals it),
`off_by_rounding` (a derived quarter one unit of gold's last digit away -
the issuer rounding each period it published on its own),
`wrong`, `declined`, `error`; and the route that produced the figure.
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

from app.retrieval.derive import Derived, xbrl_quarter  # noqa: E402
from app.retrieval.single_product import gemini_confirm  # noqa: E402

SCALE = {"units": 1e-6, "thousands": 1e-3, "millions": 1.0, "billions": 1e3}
US_SCOPES = ("U.S.", "United States", "US")

POOLS = {
    "q4": lambda r: r["calendar_quarter"] == 4,
    "q1_q3": lambda r: r["calendar_quarter"] < 4 and r["derivation"] == "direct_reported",
}


def pick(pool: str, n: int, seed: int, since: int) -> list[dict]:
    rows = [json.loads(line) for line in (GOLD / "quarterly_revenue.jsonl").open()]
    rows = [r for r in rows if POOLS[pool](r) and r["currency"] == "USD" and "sec.gov/Archives" in r["source_url"]
            and r["calendar_year"] >= since]
    rng, used, cases = random.Random(seed), set(), []
    for r in rng.sample(rows, len(rows)):
        if len(cases) == n:
            break
        if r["drug_name"] in used:
            continue
        used.add(r["drug_name"])
        cases.append({
            "pool": pool, "gold_id": r["gold_id"], "product": r["drug_name"],
            "period": f"Q{r['calendar_quarter']} {r['calendar_year']}", "issuer": r.get("manufacturer"),
            "gold_value": r["source_value_reported"], "gold_unit": r["source_unit"], "gold_scope": r.get("revenue_scope"),
            "gold_derivation": r["derivation"],
        })
    return cases


def _half_last_digit(value: float, unit: str) -> float:
    text = repr(float(value))
    decimals = len(text.split(".")[1].rstrip("0")) if "." in text else 0
    return 0.5 * 10 ** -decimals * SCALE[unit]


def run(case: dict) -> dict:
    started = time.monotonic()
    try:
        result = xbrl_quarter(case["product"], case["period"], case["issuer"], confirm=gemini_confirm)
    except Exception as exc:
        return {**case, "outcome": "error", "route": None, "error": f"{type(exc).__name__}: {exc}"}
    out = {**case, "seconds": round(time.monotonic() - started, 1), "result": asdict(result)}
    if isinstance(result, Derived):
        route = result.method if result.status == "derived" else f"derive:{result.status}"
        value, parts = (result.value if result.status == "derived" else None), {}
    else:
        route = (result.match or "member") if result.status == "found" else result.status
        value, parts = (result.value if result.status == "found" else None), result.by_geography
    out["route"] = route
    if value is None:
        return {**out, "outcome": "declined"}
    gold_m = case["gold_value"] * SCALE[case["gold_unit"]]
    slack = _half_last_digit(case["gold_value"], case["gold_unit"]) + 1e-9
    out["found_millions"] = value / 1e6
    if abs(value / 1e6 - gold_m) <= slack:
        return {**out, "outcome": "correct"}
    us = parts.get("country:US")
    if case["gold_scope"] in US_SCOPES and us is not None and abs(us / 1e6 - gold_m) <= slack:
        return {**out, "outcome": "correct_us_part", "found_millions": us / 1e6}
    if route and route.startswith("full_year_less") or route == "ytd_less_previous_ytd":
        # A derived quarter one unit of gold's last digit off is the issuer's
        # own rounding of the periods it published, not a misread figure.
        if abs(value / 1e6 - gold_m) <= 2 * slack:
            return {**out, "outcome": "off_by_rounding"}
    return {**out, "outcome": "wrong"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pool", choices=sorted(POOLS), required=True)
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--since", type=int, default=0, help="only gold rows from this calendar year on")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    cases = pick(args.pool, args.n, args.seed, args.since)
    print(f"pool={args.pool} n={len(cases)} seed={args.seed} since={args.since} workers={args.workers} issuer=gold confirm=gemini")
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(run, cases))
    args.out.write_text("".join(json.dumps(r) + "\n" for r in results))

    print(f"\n{'product':<24} {'period':<8} {'outcome':<15} {'route':<28} {'gold':>10} {'found':>10}  gold derivation")
    for r in results:
        gold = r["gold_value"] * SCALE[r["gold_unit"]]
        found = f"{r['found_millions']:.3f}" if "found_millions" in r else ""
        print(f"{r['product'][:24]:<24} {r['period']:<8} {r['outcome']:<15} {(r['route'] or r.get('error', ''))[:28]:<28} "
              f"{gold:>10.3f} {found:>10}  {r['gold_derivation']}")
    counts = {k: sum(r["outcome"] == k for r in results)
              for k in ("correct", "correct_us_part", "off_by_rounding", "wrong", "declined", "error")}
    print(f"\n{counts} of {len(results)}")
    print(f"saved: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
