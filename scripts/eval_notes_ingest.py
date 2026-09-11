"""Does the bulk tagged reader agree with itself across filings?

Scored on issuers that appear in no dataset in this repository - not gold, not
`seed/holdout`, not `seed/holdout2`, not `seed/holdout_labels`. There is no
answer key here and none is needed, which is the same trick
`eval_pdf_geometry.py` uses: a figure that two independent filings both state
must be the same figure, and a quarter and its year-to-date must add up. The
filer supplies the oracle.

Three checks, in order of how much they would cost if they failed:

* **restated** - a quarter tagged in its own 10-Q and again a year later as a
  prior-year comparative. Two filings, one number. A disagreement is either a
  restatement or a reading error, and the run says which pairs disagree so the
  difference can be looked at rather than averaged away.
* **geography** - where a filer tags US and non-US beside the undimensioned
  figure, the parts must add to the whole. This is the rule the reader relies
  on when it treats the absence of a geography axis as the worldwide total;
  if that reading were wrong, this is where it would show.
* **coverage** - how many product-quarters come back at all. Not a score
  against anything: a count, per issuer, so that a zero can be told apart from
  a filer that tags nothing.

    python scripts/eval_notes_ingest.py --root /path/to/extract [--root ...]

Each `--root` is an unzipped Financial Statement and Notes Data Set directory
(sub.tsv, dim.tsv, num.tsv) from
https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.extraction.bulk_tagged import TAGGED_FORMS  # noqa: E402
from app.parsing.notes_datasets import (  # noqa: E402
    iter_facts,
    load_dimensions,
    load_submissions,
)
from app.parsing.xbrl import PRODUCT_AXIS, product_facts  # noqa: E402

# Issuers in no dataset in this repository. Kept here rather than in `app/`
# because it is a description of what has already been measured, not a rule the
# pipeline reads.
HELD_OUT = {
    78003: "Pfizer",
    59478: "Eli Lilly",
    14272: "Bristol-Myers Squibb",
    872589: "Regeneron",
    879169: "Incyte",
    914475: "Neurocrine",
    1048477: "BioMarin",
    939767: "Exelixis",
    1001316: "Ionis",
    1159036: "Halozyme",
}


def _collect(roots: list[Path]) -> dict:
    """Every worldwide product-quarter these extracts state, by where it came from."""
    # (cik, member, period) -> {accession: value}
    quarters: dict[tuple[int, str, str], dict[str, float]] = defaultdict(dict)
    # (cik, member, period) -> {geography: value}
    regions: dict[tuple[int, str, str], dict[str, float]] = defaultdict(dict)
    filings: dict[int, set[str]] = defaultdict(set)

    for root in roots:
        subs = load_submissions(root, ciks=set(HELD_OUT), forms=set(TAGGED_FORMS))
        if not subs:
            continue
        dims = load_dimensions(root)
        by_submission: dict[str, list] = defaultdict(list)
        for adsh, fact in iter_facts(root, submissions=subs, dimensions=dims):
            by_submission[adsh].append(fact)
        for adsh, facts in by_submission.items():
            cik = subs[adsh].cik
            filings[cik].add(adsh)
            # Worldwide figures, as the reader itself selects them.
            for fact in product_facts(facts):
                if fact.months != 3 or not fact.period:
                    continue
                quarters[(cik, fact.product_member or "", fact.period)][adsh] = fact.value
            # Regional lines, kept so the parts can be added up.
            for fact in product_facts(facts, worldwide_only=False):
                if fact.months != 3 or not fact.period or fact.is_worldwide:
                    continue
                geography = fact.members.get("srt:StatementGeographicalAxis")
                if not geography or len(fact.members) != 2:
                    continue
                key = (cik, fact.product_member or "", fact.period)
                regions[key][geography] = fact.value
    return {"quarters": quarters, "regions": regions, "filings": filings}


def run(roots: list[Path], *, verbose: bool = False) -> dict:
    data = _collect(roots)
    quarters, regions, filings = data["quarters"], data["regions"], data["filings"]

    restated_pairs = 0
    restated_disagreements = []
    for key, by_filing in quarters.items():
        if len(by_filing) < 2:
            continue
        restated_pairs += 1
        values = sorted(by_filing.values())
        # Independent rounding across filings is ordinary; a real disagreement
        # is not a rounding difference.
        if values[-1] - values[0] > max(abs(values[-1]) * 0.005, 1000.0):
            restated_disagreements.append((key, by_filing))

    geography_checked = 0
    geography_off = []
    for key, parts in regions.items():
        whole = quarters.get(key)
        if not whole or len(parts) < 2:
            continue
        total = sum(parts.values())
        stated = next(iter(whole.values()))
        geography_checked += 1
        if abs(total - stated) > max(abs(stated) * 0.005, 1000.0):
            geography_off.append((key, parts, stated))

    by_issuer: dict[str, int] = defaultdict(int)
    for (cik, _member, _period) in quarters:
        by_issuer[HELD_OUT[cik]] += 1

    print("Bulk tagged reader, on issuers absent from every dataset here")
    print(f"  extracts: {', '.join(r.name for r in roots)}")
    print()
    print("  product-quarters found")
    for issuer in sorted(HELD_OUT.values()):
        found = by_issuer.get(issuer, 0)
        seen = len(filings.get(
            next(c for c, n in HELD_OUT.items() if n == issuer), set()))
        note = "" if found else ("  (no product axis in its filings)" if seen else "  (no tagged filing in these months)")
        print(f"    {issuer:22} {found:5}{note}")
    print(f"    {'total':22} {sum(by_issuer.values()):5}")
    print()
    print(f"  same quarter in two filings   {restated_pairs - len(restated_disagreements)}/{restated_pairs} agree")
    print(f"  regions add to the whole      {geography_checked - len(geography_off)}/{geography_checked} agree")
    print()
    for key, by_filing in restated_disagreements[:10]:
        cik, member, period = key
        print(f"    DISAGREES {HELD_OUT[cik]} {member} {period}: "
              + ", ".join(f"{a}={v:,.0f}" for a, v in by_filing.items()))
    for key, parts, stated in geography_off[:10]:
        cik, member, period = key
        print(f"    PARTS≠WHOLE {HELD_OUT[cik]} {member} {period}: "
              f"{'+'.join(f'{g}={v:,.0f}' for g, v in parts.items())} vs {stated:,.0f}")
    if verbose:
        for (cik, member, period), by_filing in sorted(quarters.items())[:60]:
            print(f"    {HELD_OUT[cik]:20} {member[:34]:36} {period} "
                  f"{next(iter(by_filing.values())):>16,.0f}")

    summary = {
        "extracts": [r.name for r in roots],
        "product_quarters": sum(by_issuer.values()),
        "by_issuer": dict(by_issuer),
        "restated_pairs": restated_pairs,
        "restated_disagreements": len(restated_disagreements),
        "geography_checked": geography_checked,
        "geography_disagreements": len(geography_off),
    }
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, action="append", required=True)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = run(args.root, verbose=args.verbose)
    if args.out:
        args.out.write_text(json.dumps(result, indent=1) + "\n")
