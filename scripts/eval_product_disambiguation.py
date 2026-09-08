"""Does the reader tell a product's own line from a line covering more?

Every other gate here scores period detection, geometry, arithmetic or a
citation. None of them scores *product identity*, which is the one thing the
reader used to decide from a list of brand names held in this repository -
`KNOWN_PEER_BRANDS`, whose members were gold's own catalog. A rule keyed to the
answer set refuses a shared line for the products it knows and waves the
identical line through for every product it does not, so the refusal the
pipeline is built on was strongest exactly where it was measured.

This scores the replacement on issuers that appear in no dataset here: the
labels come from Biogen's, Jazz's and Alkermes's own earnings exhibits, and
`seed/holdout_labels/product_labels.json` records the accession each was read
from. Gold is not opened.

    python scripts/eval_product_disambiguation.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.parsing.evidence import product_aliases  # noqa: E402
from app.quality.candidate_filters import names_a_competing_product  # noqa: E402

FIXTURE = REPO_ROOT / "seed" / "holdout_labels" / "product_labels.json"


def run(verbose: bool = False) -> dict:
    doc = json.loads(FIXTURE.read_text())
    cases = doc["cases"]
    # Every label the issuer prints in that schedule is a sibling of every
    # other, which is how the reader sees them at extraction time.
    siblings_by_issuer: dict[str, list[str]] = {}
    for case in cases:
        siblings_by_issuer.setdefault(case["issuer"], []).append(case["label"])

    rows = []
    for case in cases:
        aliases = product_aliases(case["product"], case.get("generic"))
        siblings = [
            label
            for label in siblings_by_issuer[case["issuer"]]
            if label != case["label"]
        ]
        competitor = names_a_competing_product(case["label"], aliases, siblings)
        got = "shared" if competitor else "own"
        rows.append({**case, "got": got, "competitor": competitor})

    own = [r for r in rows if r["expect"] == "own"]
    shared = [r for r in rows if r["expect"] == "shared"]
    false_refusals = [r for r in own if r["got"] != "own"]
    missed_shared = [r for r in shared if r["got"] != "shared"]

    print(f"Product disambiguation on issuers absent from every dataset here")
    print(f"  fixture: {FIXTURE.relative_to(REPO_ROOT)}")
    print(f"  issuers: {', '.join(sorted({r['issuer'] for r in rows}))}")
    print()
    print(f"  own lines read as own       {len(own) - len(false_refusals)}/{len(own)}")
    print(f"  shared lines refused        {len(shared) - len(missed_shared)}/{len(shared)}")
    print()
    if false_refusals:
        print("  FALSE REFUSALS - a real quarter lost:")
        for r in false_refusals:
            print(f"    {r['issuer']:22} {r['label'][:44]:46} as {r['product']:12} -> {r['competitor']!r}")
    if missed_shared:
        print("  MISSED - a shared line attributed to one product:")
        for r in missed_shared:
            print(f"    {r['issuer']:22} {r['label'][:44]:46} as {r['product']}")
    if verbose:
        for r in rows:
            mark = "ok " if r["got"] == r["expect"] else "BAD"
            print(f"  {mark} {r['label'][:50]:52} as {r['product']:12} -> {r['got']}")
    ok = not false_refusals and not missed_shared
    print()
    print("  PASS" if ok else "  FAIL")
    return {
        "cases": len(rows),
        "false_refusals": len(false_refusals),
        "missed_shared": len(missed_shared),
        "passed": ok,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    summary = run(verbose=args.verbose)
    if args.out:
        args.out.write_text(json.dumps(summary, indent=1) + "\n")
    raise SystemExit(0 if summary["passed"] else 1)
