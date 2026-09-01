"""Score the extraction stack against the gold dataset's own source text.

Every gold row carries the verbatim text it was read from, the unit that text
was stated in, and the number a careful human recovered from it. That makes the
gold file a real-source regression corpus: replay each quote through the
extractor and the recovered number either equals the gold number or it does not.

The corpus is not uniform, so rows are classified before scoring and each class
is reported separately rather than blended into one flattering percentage:

* ``table_row``   - a delimited issuer table row. Scored strictly: the value is
                    recovered from the text alone, given only the period the
                    row belongs to.
* ``wide_table``  - a retrospective table listing many consecutive periods.
                    Scored weakly, and deliberately so: these tables state
                    their span in prose above the grid, which an isolated row
                    does not carry, so the harness solves for the column offset
                    using the gold values themselves. That confirms the columns
                    run in strict period order under one consistent offset - a
                    misaligned or reordered table admits no such offset - but it
                    does not demonstrate independent recovery the way the
                    ``table_row`` score does.
* ``prose``       - a figure stated in a sentence. Scored strictly: both the
                    period and the amount are read from the sentence.
* ``positional_text`` - whitespace-delimited PDF text where columns are held by
                    position. Not yet scored; the extractor for it is pending.
* ``annotated_composite`` - a quote carrying a curator's column legend rather
                    than issuer table structure.
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
from app.extraction.prose import read_prose  # noqa: E402

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


_MONEY_IN_PROSE_RE = re.compile(r"[$£€]\s?[\d,.]+\s*(?:million|billion|thousand)", re.I)


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
    if _MONEY_IN_PROSE_RE.search(quote):
        return "prose"
    if "|" in quote:
        return "table_row"
    # Whitespace-delimited text lifted out of a PDF, where columns are position
    # rather than punctuation.
    return "positional_text"


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


# Periods cited by each (product, quote) group, filled in before replay.
_PERIOD_GROUPS: dict[tuple[str, str], list[str]] = {}


def _periods_for(drug: str, quote: str) -> list[str]:
    return _PERIOD_GROUPS.get((drug, quote), [])


def _quarter_index(period: str) -> int | None:
    match = _QUARTER_RE.fullmatch(period)
    if not match:
        return None
    return int(match.group(1)) * 4 + int(match.group(2))


def solve_wide_table_offset(
    quote: str, periods_to_values: dict[str, float]
) -> tuple[int | None, list[float | None], str | None]:
    """Find where a retrospective table's columns start relative to its periods.

    These tables state their span once, in prose above the grid, so an isolated
    row does not say which quarter its first column belongs to. Worse, the span
    is wider than the rows citing it: a quarter the issuer later reported
    directly is sourced from that filing instead, leaving a column here that no
    gold row claims.

    So rather than assume the span, this solves for the single column offset
    under which every known period lands on its own value. A unique solution
    means the columns run in period order exactly as the extractor assumes; no
    solution means they do not.
    """
    cells = [cell.strip() for cell in (quote or "").split("|")]
    if len(cells) < 2:
        return None, [], "unsplittable_quote"
    tokens = tokenize_row(cells[1:])

    indexed = {}
    for period, value in periods_to_values.items():
        index = _quarter_index(period)
        if index is None:
            return None, tokens, f"non_quarterly_period_{period}"
        indexed[index] = value
    if not indexed:
        return None, tokens, "no_periods"

    base = min(indexed)
    span = max(indexed) - base
    viable = [
        offset
        for offset in range(0, max(len(tokens) - span, 0))
        if all(
            0 <= offset + (index - base) < len(tokens)
            and tokens[offset + (index - base)] is not None
            and abs(tokens[offset + (index - base)] - value) < 1e-6
            for index, value in indexed.items()
        )
    ]
    if not viable:
        return None, tokens, "no_column_offset_aligns_all_periods"
    if len(viable) > 1:
        return None, tokens, f"ambiguous_offsets={viable}"
    return viable[0], tokens, None


def replay_wide_table(
    row: dict[str, Any], offsets: dict[tuple[str, str], tuple[int | None, list, str | None]]
) -> tuple[float | None, str | None]:
    """Read one period's value from a retrospective table at the solved offset."""
    offset, tokens, reason = offsets[(row["drug_name"], row["source_quote"])]
    if offset is None:
        return None, reason
    base = min(
        index
        for index in (
            _quarter_index(period)
            for period in _periods_for(row["drug_name"], row["source_quote"])
        )
        if index is not None
    )
    index = _quarter_index(row["period"])
    if index is None:
        return None, "non_quarterly_period"
    position = offset + (index - base)
    if not 0 <= position < len(tokens) or tokens[position] is None:
        return None, "period_column_empty"
    return tokens[position], None


def replay_prose(row: dict[str, Any]) -> tuple[float | None, str | None]:
    """Recover the row's value from a sentence, with the period read from text."""
    values = read_prose(row.get("source_quote") or "", product=row["drug_name"])
    if not values:
        return None, "no_value_extracted"
    for value in values:
        if value.period == row["period"]:
            return value.value_as_reported, None
    return None, f"periods_found={[v.period for v in values]} want={row['period']}"


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
    for name in ("table_row", "wide_table", "prose", "positional_text", "annotated_composite", "derived"):
        print(f"  {name:12} {len(buckets[name])}")

    # Rows of a retrospective table share one quote. Solve each table's column
    # offset once from all the periods that cite it, then read each row at it.
    groups: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for row in buckets["wide_table"]:
        groups[(row["drug_name"], row["source_quote"])][row["period"]] = float(
            row["source_value_reported"]
        )
    _PERIOD_GROUPS.update({key: sorted(value) for key, value in groups.items()})
    offsets = {
        key: solve_wide_table_offset(key[1], values) for key, values in groups.items()
    }

    def replay_wide(row: dict[str, Any]) -> tuple[float | None, str | None]:
        return replay_wide_table(row, offsets)

    replays = {
        "table_row": replay_table_row,
        "wide_table": replay_wide,
        "prose": replay_prose,
    }
    overall_ok = overall_total = 0
    all_failures: list[tuple[str, dict[str, Any], str]] = []

    for bucket, replay in replays.items():
        per_product: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for row in buckets[bucket]:
            recovered, reason = replay(row)
            expected = float(row["source_value_reported"])
            stats = per_product[row["drug_name"]]
            stats[1] += 1
            if recovered is not None and abs(recovered - expected) < 1e-6:
                stats[0] += 1
            else:
                all_failures.append((bucket, row, reason or f"got {recovered} want {expected}"))

        if not per_product:
            continue
        label = {
            "table_row": "delimited issuer table rows",
            "wide_table": "retrospective multi-period tables",
            "prose": "figures stated in sentences",
        }[bucket]
        print(f"\nextraction accuracy on {label}")
        total_ok = total = 0
        for product, (ok, count) in sorted(per_product.items()):
            total_ok += ok
            total += count
            flag = "" if ok == count else "   <-- "
            print(f"  {product:20} {ok:4}/{count:<4} {100 * ok / count:6.2f}%{flag}")
        print(f"  {'TOTAL':20} {total_ok:4}/{total:<4} {100 * total_ok / total:6.2f}%")
        overall_ok += total_ok
        overall_total += total

    if overall_total:
        print(
            f"\nOVERALL {overall_ok}/{overall_total} "
            f"{100 * overall_ok / overall_total:.2f}% of extractable gold rows"
        )

    if all_failures and args.show_failures:
        print(f"\n{len(all_failures)} failures:")
        for bucket, row, detail in all_failures[:40]:
            print(f"  [{bucket}] {row['drug_name']:16} {row['period']:8} {detail}")
            print(f"      quote: {row['source_quote'][:150]}")

    return 0 if overall_total and overall_ok == overall_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
