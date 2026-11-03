"""Run the earnings-release reader on a seeded batch of gold quarters, and compare.

    cd backend && uv run python ../scripts/eval_earnings_reader.py --n 15 --negatives 5 --out /tmp/er.jsonl

Gold is the oracle for finding defects, not a held-out score (CLAUDE.md rule
4). Two pools, drawn one row per product:

  positives  directly reported quarters whose gold source is an SEC exhibit 99
             (an 8-K or 6-K results exhibit), by its file name - `ex99`,
             `ex-99`, `exv99`, `99_1` and the like. A filter on names, so an
             exhibit filed under another name is not drawn; that limits the
             pool, not what the reader can read.
  negatives  the same products two years before the first quarter gold holds
             for them, where the product was not yet sold and nothing should
             be found.

The issuer gold names is passed. Outcomes: `correct` (equal to gold within its
last printed digit, same currency), `wrong`, `declined`; for negatives,
`correct_refusal` or `false_find`.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GOLD = REPO / "seed" / "gold"
sys.path.insert(0, str(REPO / "backend"))

from app.retrieval.earnings import read_earnings  # noqa: E402

SCALE = {"units": 1.0, "thousands": 1e3, "millions": 1e6, "billions": 1e9}
_EXHIBIT_NAME = re.compile(r"ex-?99|exv99|dex99|ex_99|[-_]99[-_]?\d", re.IGNORECASE)


def pick(n: int, negatives: int, seed: int) -> list[dict]:
    rows = [json.loads(line) for line in (GOLD / "quarterly_revenue.jsonl").open()]
    first = {}
    for r in rows:
        key = r["drug_name"]
        first[key] = min(first.get(key, (9999, 9)), (r["calendar_year"], r["calendar_quarter"]))
    pool = [r for r in rows if r["derivation"] == "direct_reported" and "sec.gov/Archives" in r["source_url"]
            and _EXHIBIT_NAME.search(r["source_url"].rsplit("/", 1)[-1])]
    rng, used, cases = random.Random(seed), set(), []
    for r in rng.sample(pool, len(pool)):
        if len(cases) == n:
            break
        if r["drug_name"] in used:
            continue
        used.add(r["drug_name"])
        cases.append({"kind": "positive", "product": r["drug_name"], "issuer": r.get("manufacturer"),
                      "period": f"Q{r['calendar_quarter']} {r['calendar_year']}", "gold_value": r["source_value_reported"],
                      "gold_unit": r["source_unit"], "gold_currency": r["currency"], "gold_url": r["source_url"]})
    taken = 0
    for r in rng.sample(pool, len(pool)):
        if taken == negatives:
            break
        y, q = first[r["drug_name"]]
        if r["drug_name"] in used or y < 2012:
            continue
        used.add(r["drug_name"])
        taken += 1
        cases.append({"kind": "negative", "product": r["drug_name"], "issuer": r.get("manufacturer"),
                      "period": f"Q{q} {y - 2}", "gold_value": None, "gold_unit": None, "gold_currency": None,
                      "gold_url": None})
    return cases


def _half_last_digit(value: float, unit: str) -> float:
    text = repr(float(value))
    decimals = len(text.split(".")[1].rstrip("0")) if "." in text else 0
    return 0.5 * 10 ** -decimals * SCALE[unit]


def run(case: dict) -> dict:
    started = time.monotonic()
    try:
        answer = read_earnings(case["product"], case["period"], case["issuer"])
    except Exception as exc:
        return {**case, "outcome": "error", "error": f"{type(exc).__name__}: {exc}"}
    out = {**case, "seconds": round(time.monotonic() - started, 1), "answer": asdict(answer)}
    if case["kind"] == "negative":
        return {**out, "outcome": "false_find" if answer.status == "found" else "correct_refusal"}
    if answer.status != "found":
        return {**out, "outcome": "declined"}
    gold = case["gold_value"] * SCALE[case["gold_unit"]]
    ok = (answer.currency == case["gold_currency"]
          and abs(answer.value - gold) <= _half_last_digit(case["gold_value"], case["gold_unit"]) + 1e-6)
    return {**out, "outcome": "correct" if ok else "wrong"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=15)
    parser.add_argument("--negatives", type=int, default=5)
    parser.add_argument("--seed", type=int, default=51)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    cases = pick(args.n, args.negatives, args.seed)
    print(f"seed={args.seed} positives={sum(c['kind'] == 'positive' for c in cases)} "
          f"negatives={sum(c['kind'] == 'negative' for c in cases)} workers={args.workers} issuer=gold reader=gemini, no search")
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(run, cases))
    args.out.write_text("".join(json.dumps(r) + "\n" for r in results))

    print(f"\n{'kind':<9} {'product':<24} {'period':<8} {'outcome':<16} {'status':<14} {'gold':>12} {'found':>14}  readings")
    for r in results:
        a = r.get("answer") or {}
        gold = "" if r["gold_value"] is None else f"{r['gold_value'] * SCALE[r['gold_unit']] / 1e6:.3f} {r['gold_currency']}"
        found = "" if a.get("value") is None else f"{a['value'] / 1e6:.3f} {a.get('currency')}"
        readings = ", ".join(f"{x['filer'][:14]}:{x['status']}" for x in a.get("readings", []))
        print(f"{r['kind']:<9} {r['product'][:24]:<24} {r['period']:<8} {r['outcome']:<16} {(a.get('status') or r.get('error', ''))[:14]:<14} "
              f"{gold:>12} {found:>14}  {readings[:110]}")
    counts = {}
    for r in results:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    print(f"\n{counts} of {len(results)}")
    print(f"saved: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
