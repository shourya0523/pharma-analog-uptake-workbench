"""Build the rows, priority tiers, notes and batches the verification app serves.

Reads the published gold dataset and writes one JSON file:

    python tools/verification-app/scripts/build_rows.py [--out PATH]

Every figure a person is asked to check is a gold row for a product on the
requested list (docs/sourcing/target_products.csv): each quarterly row, and
each annual row of a product that has no quarterly series. A row's tier says
how much rides on it; its note says what in the document makes it easy to
misread. Both are derived from the row and its series, so a rebuild of gold
re-derives them.

This is a gold-side tool. Nothing the pipeline reads may come from here
(CLAUDE.md rule 3), and backend/tests/test_verification_app_is_not_an_input.py
checks the pipeline never names it.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[3]
GOLD = REPO / "seed" / "gold"
MANIFESTS = GOLD / "source_manifests"
TARGETS = REPO / "docs" / "sourcing" / "target_products.csv"
LOG = REPO / "docs" / "sourcing" / "verification_log.json"
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "rows.json"

BATCH_SIZE = 25

# Why a row is P1: an error in it moves a peak, rests on arithmetic, failed the
# automated check, was kept despite looking wrong, or sits beside a quarter an
# ownership change left unreported.
P1_REASONS = {
    "peak year",
    "derived quarter",
    "automated check failed",
    "reviewed anomaly",
    "next to an ownership gap",
}
# Why a row is P2: it bounds its series, or its source is one the automated
# check reads least reliably.
P2_REASONS = {"series start", "series end", "prose figure", "issuer website source"}

DERIVATION_TEXT = {
    "annual_less_reported_first_nine_months": "the full year less the first nine months",
    "full_year_less_other_reported_quarters": "the full year less the other three quarters",
    "year_to_date_less_reported_quarters": "a year-to-date figure less the earlier quarters",
    "acquisition_bridge_sum": "the sum of two part-quarters either side of an acquisition",
    "identity_normalization_pre_dpi": "a recast that puts earlier quarters on the later line definition",
}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def quarter_index(period: str) -> int:
    return int(period[:4]) * 4 + int(period[-1])


def requested_products() -> set[str]:
    with TARGETS.open(newline="") as handle:
        return {row["brand_name"] for row in csv.DictReader(handle)}


def series_meta() -> dict[str, dict]:
    """Researched series metadata by product, for start/end reasons and anomalies."""
    out: dict[str, dict] = {}
    for path in MANIFESTS.glob("*.meta.json"):
        meta = json.loads(path.read_text())
        out.setdefault(meta["drug_name"], meta)
    return out


def fiscal_hint(row: dict) -> str | None:
    """Spell out the quarter mapping when the issuer's fiscal year is April-March."""
    notes = (row.get("gold_notes") or "").lower()
    if "april" not in notes or "march" not in notes:
        return None
    quarter = int(row["period"][-1])
    fiscal_quarter = {2: 1, 3: 2, 4: 3, 1: 4}[quarter]
    fiscal_year = int(row["period"][:4]) - (1 if quarter == 1 else 0)
    return (
        f"Fiscal year runs April to March: calendar {row['period']} is fiscal Q{fiscal_quarter} "
        f"of the year starting April {fiscal_year}. Read that quarter's own column; some tables "
        "print only year-to-date figures, in which case the quarter is the difference of two."
    )


def note_for(row: dict, reasons: list[str], context: dict) -> tuple[str, str]:
    """(note, suggestion) for one row: what could be misread, and what to check."""
    notes: list[str] = []
    text = f"{row['source_quote']} {row.get('gold_notes') or ''}".lower()
    derivation = row["derivation"]

    if derivation in DERIVATION_TEXT:
        urls = [u for u in re.findall(r"https?://[^\s,;)'\"]+", row.get("gold_notes") or "")
                if u.rstrip(".") != row["source_url"]]
        notes.append(
            f"Not printed directly: this quarter is {DERIVATION_TEXT[derivation]}. The quote "
            "shows each input and the result. Check every input in its own document"
            + (f" (also: {', '.join(dict.fromkeys(u.rstrip('.') for u in urls))})" if urls else "")
            + ", and that the inputs are the same line and scope."
        )
    elif derivation == "direct_prior_year_column":
        notes.append(
            "Read from the prior-year column of a later document. If the quarter's own report "
            "prints a different figure, the later one is a restatement: say which you saw."
        )
    elif "retrospective" in derivation:
        notes.append("Read from a retrospective (recast) table, not the quarter's own release.")

    if "unlabel" in text or "subtotal" in text:
        notes.append(
            "The product is printed as regional lines (U.S. / Europe / Other); the figure is "
            "the unlabelled total line beneath them, not the U.S. line."
        )
    if "prose" in text or "narrative" in text:
        notes.append("The figure is stated in the text, not in a table: find the sentence.")
    hint = fiscal_hint(row)
    if hint:
        notes.append(hint)

    unit = row.get("source_unit") or "millions"
    if unit != "millions":
        notes.append(
            f"Printed in {unit} ({row.get('source_value_reported')}); recorded as "
            f"{row['value_reported']:,g} million {row['currency']}."
        )
    if row.get("revenue_scope") not in (None, "Worldwide"):
        notes.append(f"Line scope is {row['revenue_scope']}: read that column, not a total.")
    if row.get("issuer") and row["issuer"] != context.get("current_owner"):
        notes.append(
            f"Printed by {row['issuer']}, an earlier owner; the series continues under "
            f"{context.get('current_owner')}."
        )
    if "next to an ownership gap" in reasons:
        notes.append(
            "A neighbouring quarter is unreported because the product changed owner. Check "
            "this one is a full quarter, not a part-quarter stub from the closing date."
        )
    if row.get("reviewed_anomaly"):
        notes.append(f"Kept although it looks wrong: {row['reviewed_anomaly']}")
    if "peak year" in reasons:
        notes.append(f"Part of {row['drug_name']}'s peak year: an error here moves the peak.")
    if "automated check failed" in reasons:
        notes.append(
            "The automated check could not match this row (quote layout, a blocked site, or "
            "inputs in two documents); it has been read by hand once."
        )
    host = urlparse(row["source_url"]).netloc
    # A snapshot of the issuer hosts that refused every automated client during
    # sourcing; it goes stale when a host changes its bot policy.
    if any(blocked in host for blocked in ("bayer.com", "boehringer-ingelheim.com")):
        notes.append("This site refuses automated clients; open it in a normal browser.")

    label = context.get("row_label") or row["drug_name"]
    if derivation in DERIVATION_TEXT:
        suggestion = f"Recompute {row['value_reported']:,g} from the inputs in the quote."
    else:
        column = "prior-year" if derivation == "direct_prior_year_column" else row["period"]
        suggestion = (
            f"Find the {label} line and check its {column} figure is "
            f"{row.get('source_value_reported', row['value_reported'])} "
            f"({unit}, {row['currency']})."
        )
    return " ".join(notes), suggestion


def build() -> dict:
    requested = requested_products()
    quarterly = [r for r in load_jsonl(GOLD / "quarterly_revenue.jsonl") if r["drug_name"] in requested]
    annual = [r for r in load_jsonl(GOLD / "annual_revenue.jsonl") if r["drug_name"] in requested]
    peaks = {r["drug_name"]: r for r in load_jsonl(GOLD / "peak_sales.jsonl")}
    coverage = {r["drug_name"]: r for r in load_jsonl(GOLD / "series_coverage.jsonl")}
    log = json.loads(LOG.read_text())
    metas = series_meta()

    rows: list[dict] = []
    by_drug: dict[str, list[dict]] = defaultdict(list)
    for row in quarterly:
        by_drug[row["drug_name"]].append(row)

    for drug, series in by_drug.items():
        series.sort(key=lambda r: r["period"])
        peak = peaks.get(drug) or {}
        holes = {quarter_index(p) for p in coverage.get(drug, {}).get("unreported_quarters", [])}
        entry = log.get(drug, {})
        failed = {p for p, ok in entry.get("automated", {}).items() if not ok}
        checked = set(entry.get("hand_checked", []))
        owner = metas.get(drug, {}).get("manufacturer") or series[-1]["manufacturer"]
        for index, row in enumerate(series):
            reasons: list[str] = []
            if peak.get("peak_year") == row["calendar_year"]:
                reasons.append("peak year")
            if not row["derivation"].startswith("direct"):
                reasons.append("derived quarter")
            if row["period"] in failed:
                reasons.append("automated check failed")
            if row.get("reviewed_anomaly"):
                reasons.append("reviewed anomaly")
            if any(abs(quarter_index(row["period"]) - hole) == 1 for hole in holes):
                reasons.append("next to an ownership gap")
            if index == 0:
                reasons.append("series start")
            if index == len(series) - 1:
                reasons.append("series end")
            if "prose" in (row["source_quote"] + (row.get("gold_notes") or "")).lower():
                reasons.append("prose figure")
            if "sec.gov" not in row["source_url"]:
                reasons.append("issuer website source")
            tier = "P1" if P1_REASONS & set(reasons) else "P2" if P2_REASONS & set(reasons) else "P3"
            note, suggestion = note_for(row, reasons, {"current_owner": owner})
            rows.append({
                "gold_id": row["gold_id"],
                "tier": tier,
                "reasons": reasons,
                "drug_name": drug,
                "generic_name": row.get("generic_name"),
                "issuer": row["manufacturer"],
                "period": row["period"],
                "period_type": "quarter",
                "value_reported": row["value_reported"],
                "currency": row["currency"],
                "source_unit": row.get("source_unit") or "millions",
                "source_value_reported": row.get("source_value_reported"),
                "value_usd_millions": row.get("value_normalized_usd_millions"),
                "derivation": row["derivation"],
                "scope": row.get("revenue_scope"),
                "source_url": row["source_url"],
                "source_quote": row["source_quote"],
                "automated_check": "fail" if row["period"] in failed
                else "pass" if row["period"] in entry.get("automated", {}) else "none",
                "claude_checked": row["period"] in checked,
                "claude_note": note,
                "claude_suggestion": suggestion,
            })

    # Annual rows of products with no quarterly series: the figure is the
    # benchmark itself, so each one is P1.
    for row in annual:
        if row["drug_name"] in by_drug:
            continue
        note, suggestion = note_for(row | {"period": row["period"] + "Q4", "calendar_year": int(row["period"])},
                                    ["peak year"] if row.get("series_role") == "peak_benchmark" else [],
                                    {"current_owner": row["manufacturer"]})
        rows.append({
            "gold_id": row["gold_id"],
            "tier": "P1",
            "reasons": ["annual figure, no quarterly series"],
            "drug_name": row["drug_name"],
            "generic_name": row.get("generic_name"),
            "issuer": row["manufacturer"],
            "period": row["period"],
            "period_type": "year",
            "value_reported": row["value_reported"],
            "currency": row["currency"],
            "source_unit": row.get("source_unit") or "millions",
            "source_value_reported": row.get("source_value_reported"),
            "value_usd_millions": row.get("value_normalized_usd_millions"),
            "derivation": row["derivation"],
            "scope": row.get("revenue_scope"),
            "source_url": row["source_url"],
            "source_quote": row["source_quote"],
            "automated_check": "none",
            "claude_checked": False,
            "claude_note": note.replace(row["period"] + "Q4", row["period"]),
            "claude_suggestion": suggestion.replace(row["period"] + "Q4", row["period"]),
        })

    # Batches keep rows that cite the same document together, so a reviewer
    # opens each document once: one issuer and tier per batch, rows ordered by
    # document then period, cut every BATCH_SIZE rows.
    batches: list[dict] = []
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["tier"], row["issuer"])].append(row)
    for (tier, issuer), members in sorted(groups.items()):
        members.sort(key=lambda r: (r["source_url"], r["drug_name"], r["period"]))
        chunks = [members[i:i + BATCH_SIZE] for i in range(0, len(members), BATCH_SIZE)]
        for number, chunk in enumerate(chunks, start=1):
            slug = re.sub(r"[^a-z0-9]+", "-", issuer.lower()).strip("-")
            batch_id = f"{tier.lower()}-{slug}-{number:02d}"
            drugs = sorted({r["drug_name"] for r in chunk})
            periods = sorted(r["period"] for r in chunk)
            batches.append({
                "id": batch_id,
                "tier": tier,
                "issuer": issuer,
                "title": f"{issuer}: {', '.join(drugs[:3])}{' +' + str(len(drugs) - 3) if len(drugs) > 3 else ''}"
                         f" ({periods[0]}-{periods[-1]})",
                "row_count": len(chunk),
                "document_count": len({r["source_url"] for r in chunk}),
            })
            for row in chunk:
                row["batch_id"] = batch_id

    manifest = json.loads((GOLD / "manifest.json").read_text())
    return {
        "gold_as_of": manifest.get("as_of_quarter"),
        "batches": batches,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    data = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=1, default=str) + "\n")
    tiers = defaultdict(int)
    for row in data["rows"]:
        tiers[row["tier"]] += 1
    print(f"wrote {args.out}: {len(data['rows'])} rows in {len(data['batches'])} batches; "
          + ", ".join(f"{k} {v}" for k, v in sorted(tiers.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
