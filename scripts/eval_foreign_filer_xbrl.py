"""Does the tagged reader recover what a foreign filer tagged?

Scored on `seed/holdout_foreign_xbrl.json`: eight issuers that file with the
SEC, tag products, and appear in no answer key and in none of the filings the
axis and element derivations were built against.

The oracle is the filer's own printed figure, recorded in the holdout beside
the accession it came from. A case expecting nothing is as much of a result as
one expecting a figure - most of these issuers tag revenue categories and
segments rather than brands, and reading "Product Revenue" or "Biopharma" as a
product would be the failure this is watching for.

Reported beside the derived reader is what the same filings give up when the
product axis is named instead of found, which is what the reader did before.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.extraction.members import load_products  # noqa: E402
from app.extraction.tagged import candidates_from_instance  # noqa: E402
from app.parsing.xbrl import parse_facts, product_facts  # noqa: E402

UA = "pharma-analog-research mehr.anand@bitsdime.com"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
HOLDOUT = ROOT / "seed" / "holdout_foreign_xbrl.json"
CACHE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".cache" / "foreign_xbrl"


def get(url: str) -> bytes | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / ("x_" + re.sub(r"[^A-Za-z0-9]", "_", url)[-120:])
    if not cache.exists():
        rc = subprocess.run(
            ["curl", "-sS", "--max-time", "180", "--max-filesize", "90000000",
             "-A", UA, "-o", str(cache), url, "-w", "%{http_code}"],
            capture_output=True, text=True,
        )
        if rc.stdout.strip() != "200":
            cache.unlink(missing_ok=True)
            return None
        time.sleep(0.15)
    return cache.read_bytes()


def main() -> int:
    cases = json.loads(HOLDOUT.read_text())
    tracked = load_products()
    passed = failed = unreachable = 0
    named_axis_found = 0
    misses: list[str] = []

    for case in cases:
        url = (f"{ARCHIVES}/{case['cik']}/{case['accession'].replace('-', '')}"
               f"/{case['instance']}")
        raw = get(url)
        if not raw:
            unreachable += 1
            print(f"  {case['ticker']:5} {case['product'][:30]:30} instance unreachable")
            continue
        products = sorted({*tracked, case["product"]})
        found, _notes = candidates_from_instance(
            raw, product=case["product"], issuer=case["issuer"],
            products=products, register={},
        )
        # What the same filing gives up when the axis is named rather than found.
        if product_facts(parse_facts(raw)):
            named_axis_found += 1

        if case["expect"] == "a figure":
            hit = next((c for c in found if c["period"] == case["period"]), None)
            got = hit and round(hit["value_normalized_usd_millions"], 3)
            ok = got == case["printed_in_the_filing"]
            note = "" if ok else f" (got {got!r}, printed {case['printed_in_the_filing']})"
        else:
            ok = not found
            note = "" if ok else (
                f" (answered {found[0]['period']} = "
                f"{found[0]['value_normalized_usd_millions']:,.0f}m)"
            )
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        mark = "ok  " if ok else "MISS"
        print(f"  {mark} {case['ticker']:5} {case['product'][:32]:32} "
              f"expect {case['expect']:9}{note}")
        if not ok:
            misses.append(f"{case['ticker']} {case['product']}: {case['why']}{note}")

    total = passed + failed
    print(f"\n  {passed}/{total} correct"
          + (f", {unreachable} unreachable" if unreachable else ""))
    print(f"  the product axis, named rather than found, reaches "
          f"{named_axis_found} of {total} filings at all")
    for miss in misses:
        print(f"    miss: {miss}")
    caveated = [c for c in cases if c.get("caveat")]
    if caveated:
        print(f"\n  {len(caveated)} case(s) carry a caveat the reader does not act on:")
        for c in caveated:
            print(f"    {c['ticker']} {c['product']}: {c['caveat']}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
