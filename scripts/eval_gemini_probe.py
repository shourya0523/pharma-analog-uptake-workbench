"""Run the Gemini revenue probe on one gold row of each kind, check and score it.

    cd backend && uv run python ../scripts/eval_gemini_probe.py --out /tmp/run.jsonl

Gold is the oracle here, for finding where the probe goes wrong; it is not a
held-out score (CLAUDE.md rule 4). Each kind below is a predicate over gold's
own fields, and one row is drawn from each with a fixed seed, so the same
seed picks the same rows while gold is unchanged.

For every row the probe's answer is checked against the document it cites
(`app.retrieval.verify`, which never sees gold) and then compared with gold:
the value scaled to the same unit, and the currency.
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

from app.retrieval.gemini_search import DEFAULT_MODEL, QuotaExceeded, ask_revenue  # noqa: E402
from app.retrieval.verify import verify_answer  # noqa: E402

SCALE = {"units": 1e-6, "thousands": 1e-3, "millions": 1.0, "billions": 1e3}

# Kinds of row, each a predicate over a gold quarterly row. They are chosen to
# cover the axes gold varies on - currency, unit, scope, source, age and whether
# the value is printed at all - so that a failure says which axis broke.
QUARTERLY_KINDS = {
    "usd_sec_worldwide_recent": lambda r: r["currency"] == "USD" and r["source_type"] == "sec_filing"
    and r["revenue_scope"] == "Worldwide" and r["derivation"] == "direct_reported" and r["calendar_year"] >= 2018,
    "non_usd": lambda r: r["currency"] != "USD" and r["derivation"] == "direct_reported",
    "thousands_unit": lambda r: r["source_unit"] == "thousands" and r["derivation"] == "direct_reported",
    "us_only_scope": lambda r: r["revenue_scope"] == "U.S." and r["derivation"] == "direct_reported",
    "derived_not_printed": lambda r: r["derivation"] == "annual_less_reported_first_nine_months",
    "pre_2010": lambda r: r["calendar_year"] < 2010 and r["derivation"] == "direct_reported",
    "company_ir_source": lambda r: r["source_type"] == "company_ir" and r["derivation"] == "direct_reported",
    "product_family_scope": lambda r: r["revenue_scope"] == "Product family" and r["derivation"] == "direct_reported",
}


def pick_cases(seed: int) -> list[dict]:
    rng = random.Random(seed)
    quarterly = [json.loads(line) for line in (GOLD / "quarterly_revenue.jsonl").open()]
    annual = [json.loads(line) for line in (GOLD / "annual_revenue.jsonl").open()]
    excluded = [json.loads(line) for line in (GOLD / "excluded_products.jsonl").open()]

    cases, used_products = [], set()
    for kind, keep in QUARTERLY_KINDS.items():
        pool = [r for r in quarterly if keep(r) and r["drug_name"] not in used_products]
        row = rng.choice(pool)
        used_products.add(row["drug_name"])
        cases.append({
            "kind": kind, "gold_id": row["gold_id"], "product": row["drug_name"],
            "issuer": row.get("manufacturer"), "period": f"Q{row['calendar_quarter']} {row['calendar_year']}",
            "gold_value": row["source_value_reported"], "gold_unit": row["source_unit"],
            "gold_currency": row["currency"], "gold_url": row["source_url"],
            # The probe asks what was reported. A quarter gold derives by
            # subtraction was never printed, so declining it is the right answer.
            "expect_value": row["derivation"].startswith("direct"),
        })

    row = rng.choice([r for r in annual if r["series_role"] == "peak_benchmark" and r["drug_name"] not in used_products])
    used_products.add(row["drug_name"])
    cases.append({
        "kind": "annual", "gold_id": row["gold_id"], "product": row["drug_name"], "issuer": row.get("manufacturer"),
        # A calendar-basis row is asked as calendar dates; a fiscal one by the
        # issuer's own fiscal-year label.
        "period": row["period"] if row["period_basis"] == "calendar" else f"FY{row['period']}",
        "gold_value": row["value_reported"], "gold_unit": row["unit"],
        "gold_currency": row["currency"], "gold_url": row["source_url"], "expect_value": True,
    })

    # An excluded product has no period in gold; any year it was on the market
    # has no standalone figure, so a recent complete calendar year is asked.
    row = rng.choice([r for r in excluded if r["reason_code"] == "aggregated_with_other_products"
                      and r.get("approval_era") not in (None, "2025+")])
    cases.append({
        "kind": "excluded_aggregated", "gold_id": row["gold_id"], "product": row["drug_name"], "issuer": None,
        "period": "2023", "gold_value": None, "gold_unit": None, "gold_currency": None,
        "gold_url": row["source_url"], "expect_value": False, "gold_reason": row["reason_code"],
    })
    return cases


def score(case: dict, parsed: dict | None) -> dict:
    """Compare the probe's parsed answer with gold."""
    value = (parsed or {}).get("value")
    if not case["expect_value"]:
        return {"value_match": value is None, "currency_match": None, "probe_value_millions": None}
    if value is None:
        return {"value_match": False, "currency_match": None, "probe_value_millions": None}
    try:
        probe_m = float(value) * SCALE.get(str(parsed.get("unit", "")).lower(), float("nan"))
    except (TypeError, ValueError):
        probe_m = float("nan")
    gold_m = float(case["gold_value"]) * SCALE[case["gold_unit"]]
    # Equal to within the last printed digit of either figure: 180.3 million
    # and 180,282 thousand are the same number printed at two precisions.
    slack = max(_half_last_digit(case["gold_value"], case["gold_unit"]),
                _half_last_digit(value, str(parsed.get("unit", "")).lower()))
    match = abs(probe_m - gold_m) <= slack + abs(gold_m) * 1e-9
    return {
        "value_match": bool(match),
        "currency_match": str(parsed.get("currency", "")).upper() == case["gold_currency"],
        "probe_value_millions": probe_m,
    }


def _half_last_digit(value, unit: str) -> float:
    """Half of one unit in the last printed place of `value`, in millions."""
    text = value if isinstance(value, str) else repr(float(value))
    decimals = len(text.split(".")[1].rstrip("0")) if "." in text else 0
    return 0.5 * 10 ** -decimals * SCALE.get(unit, 0.0)


def run_case(case: dict, model: str) -> dict:
    started = time.monotonic()
    try:
        answer = ask_revenue(case["product"], case["period"], case["issuer"], model=model)
    except QuotaExceeded as exc:
        return {**case, "error": f"quota: {exc}"}
    except Exception as exc:
        return {**case, "error": f"{type(exc).__name__}: {exc}"}
    check = verify_answer(answer.parsed, case["product"])
    searches = sum(c.get("count", 0) for c in (answer.usage or {}).get("grounding_tool_count") or [])
    return {
        **case, "seconds": round(time.monotonic() - started), "searches": searches,
        "parsed": answer.parsed, "verification": asdict(check), **score(case, answer.parsed),
        "interaction_id": answer.interaction_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=20261027)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="print the cases and stop")
    parser.add_argument("--kinds", nargs="+", help="run only these kinds (the draw is unchanged)")
    args = parser.parse_args()

    cases = pick_cases(args.seed)
    if args.kinds:
        cases = [c for c in cases if c["kind"] in args.kinds]
    print(f"model={args.model} seed={args.seed} cases={len(cases)} workers={args.workers}")
    for c in cases:
        print(f"  {c['kind']:<26} {c['product']} | {c['period']} | {c['issuer']} | gold {c['gold_value']} "
              f"{c['gold_unit'] or ''} {c['gold_currency'] or ''}")
    if args.dry_run:
        return 0

    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda c: run_case(c, args.model), cases))
    args.out.write_text("".join(json.dumps(r) + "\n" for r in results))

    print(f"\n{'kind':<26} {'value':<6} {'ccy':<5} {'check':<23} {'quote':<6} {'srch':>4} {'sec':>4}  probe")
    for r in results:
        if "error" in r:
            print(f"{r['kind']:<26} ERROR {r['error'][:90]}")
            continue
        v, p = r["verification"], r["parsed"] or {}
        quote = f"{v['quote_segments_found']}/{v['quote_segments']}"
        print(f"{r['kind']:<26} {str(r['value_match']):<6} {str(r['currency_match']):<5} {v['status']:<23} "
              f"{quote:<6} {r['searches']:>4} {r['seconds']:>4}  {p.get('value')} {p.get('unit')} {p.get('currency')}")
    done = [r for r in results if "error" not in r]
    print(f"\nvalue matches gold: {sum(r['value_match'] for r in done)}/{len(results)}; "
          f"verified against cited document: {sum(r['verification']['status'] == 'verified' for r in done)}/{len(results)}; "
          f"searches: {sum(r['searches'] for r in done)}")
    print(f"saved: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
