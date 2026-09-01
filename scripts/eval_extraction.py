"""Score the extraction stack against the gold dataset's own source text.

Every gold row carries the verbatim text it was read from, the unit that text
was stated in, and the number a careful human recovered from it. That makes the
gold file a real-source regression corpus: replay each quote through the
extractor and the recovered number either equals the gold number or it does not.

The corpus is not uniform, so rows are classified before scoring and each class
is reported separately rather than blended into one flattering percentage:

* ``table_row``   - a delimited issuer table row; the extractor must recover it.
* ``wide_table``  - a retrospective table listing many consecutive periods.
* ``prose``       - a sentence, not a table. Out of scope for table extraction.
* ``derived``     - arithmetic over other rows (bridges, subtractions), which is
                    a processing result rather than something read off a page.

Usage:
    cd backend && uv run python ../scripts/eval_extraction.py
    cd backend && uv run python ../scripts/eval_extraction.py --show-failures
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.extraction.extract import map_values_to_blocks, tokenize_row  # noqa: E402
from app.extraction.fingerprint import PeriodBlock  # noqa: E402

GOLD = REPO_ROOT / "seed" / "gold"

# Derivations that produce a number by arithmetic rather than by reading a page.
DERIVED = {
    "annual_less_reported_first_nine_months",
    "acquisition_bridge_sum",
    "full_year_less_other_reported_quarters",
    "identity_normalization_pre_dpi",
}

_QUARTER_RE = re.compile(r"(\d{4})Q([1-4])")
_END_MONTH_BY_QUARTER = {1: 3, 2: 6, 3: 9, 4: 12}


def load_gold() -> list[dict[str, Any]]:
    path = GOLD / "quarterly_revenue.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# A parenthetical spelling out which column is which period, e.g.
# "(USD thousands; Q2 2026, Q2 2025, H1 2026, H1 2025)". That legend is a
# curator's note about a composite, not issuer table structure, and the row it
# belongs to is not necessarily the leftmost column - so these are reported
# apart from the table rows the extractor is built to read.
_COLUMN_LEGEND_RE = re.compile(
    r"\([^)]*(?:Q[1-4]\s*\d{4}|\d{4}\s*Q[1-4]|H[12]\s*\d{4}|Q[1-4]-Q[1-4])[^)]*\)"
)


def classify(row: dict[str, Any]) -> str:
    quote = row.get("source_quote") or ""
    if row.get("derivation") in DERIVED:
        return "derived"
    if _COLUMN_LEGEND_RE.search(quote):
        return "annotated_composite"
    cells = [cell.strip() for cell in quote.split("|")]
    if len(cells) >= 3:
        numeric = sum(1 for cell in cells[1:] if re.fullmatch(r"[\d,.]+", cell))
        return "wide_table" if numeric >= 5 else "table_row"
    return "prose"


def replay_table_row(row: dict[str, Any]) -> tuple[float | None, str | None]:
    """Recover the row's own period value from its verbatim quote.

    The unit and reporting period come from the gold row because a single
    quoted line does not carry the document header that declares them; what is
    under test here is column alignment, which is where extraction silently
    goes wrong.
    """
    match = _QUARTER_RE.fullmatch(row["period"])
    if not match:
        return None, "not_a_quarter"
    year, quarter = int(match.group(1)), int(match.group(2))
    end_month = _END_MONTH_BY_QUARTER[quarter]

    cells = [cell.strip() for cell in (row.get("source_quote") or "").split("|")]
    if len(cells) < 2:
        return None, "unsplittable_quote"

    tokens = tokenize_row(cells[1:])
    # An issuer row states the period then its prior-year comparative. Quarterly
    # exhibits also carry a year-to-date block beside the quarter - four value
    # columns means "quarter, quarter prior, YTD, YTD prior", which is why a Q4
    # row's third number is the full year and must never be read as a quarter.
    blocks = [
        PeriodBlock(months=3, end_month=end_month, year=year, value_index=0),
        PeriodBlock(months=3, end_month=end_month, year=year - 1, value_index=1),
    ]
    if len(tokens) == 1:
        blocks = blocks[:1]
    elif len(tokens) >= 4:
        ytd_months = quarter * 3
        blocks += [
            PeriodBlock(months=ytd_months, end_month=end_month, year=year, value_index=2),
            PeriodBlock(months=ytd_months, end_month=end_month, year=year - 1, value_index=3),
        ]
    blocks = tuple(blocks)
    assigned, reason = map_values_to_blocks(tokens, blocks)
    if assigned is None:
        return None, reason
    if 0 not in assigned:
        return None, "period_column_empty"
    return assigned[0], None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-failures", action="store_true")
    parser.add_argument("--product", help="restrict to one drug_name")
    args = parser.parse_args()

    rows = load_gold()
    if args.product:
        rows = [row for row in rows if row["drug_name"] == args.product]

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[classify(row)].append(row)

    print(f"gold rows: {len(rows)}")
    for name in ("table_row", "wide_table", "annotated_composite", "prose", "derived"):
        print(f"  {name:12} {len(buckets[name])}")

    scored = buckets["table_row"]
    per_product: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    failures: list[tuple[dict[str, Any], str]] = []

    for row in scored:
        recovered, reason = replay_table_row(row)
        expected = float(row["source_value_reported"])
        stats = per_product[row["drug_name"]]
        stats[1] += 1
        if recovered is not None and abs(recovered - expected) < 1e-6:
            stats[0] += 1
        else:
            detail = reason or f"got {recovered} want {expected}"
            failures.append((row, detail))

    print("\nextraction accuracy on delimited issuer table rows")
    total_ok = total = 0
    for product, (ok, count) in sorted(per_product.items()):
        total_ok += ok
        total += count
        flag = "" if ok == count else "   <-- "
        print(f"  {product:20} {ok:4}/{count:<4} {100 * ok / count:6.2f}%{flag}")
    if total:
        print(f"  {'TOTAL':20} {total_ok:4}/{total:<4} {100 * total_ok / total:6.2f}%")

    if failures and args.show_failures:
        print(f"\n{len(failures)} failures:")
        for row, detail in failures[:40]:
            print(f"  {row['drug_name']:18} {row['period']:8} {detail}")
            print(f"      quote: {row['source_quote'][:150]}")

    return 0 if total and total_ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
