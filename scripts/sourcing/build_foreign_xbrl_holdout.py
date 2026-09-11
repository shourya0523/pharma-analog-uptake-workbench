"""Build a held-out set of foreign filers that tag products in XBRL.

The issuers here are chosen by one rule: they file with the SEC, they tag
products, and they appear in no answer key and in none of the filings the
axis/element derivation was designed against. The products are not chosen at
all - they are whatever the filing tags, named the way a person would write
them, so the set cannot be a list of the cases that happen to work.

The oracle is the filer's own printed document. A tagged figure the pipeline
returns has to appear, as printed, in the human-readable filing beside the
product - two routes to one number, neither of them an answer key.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.connectors.sources import _instance_document  # noqa: E402
from app.extraction.members import words  # noqa: E402
from app.parsing.xbrl import parse_facts  # noqa: E402

UA = "pharma-analog-research mehr.anand@bitsdime.com"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "seed" / "holdout_foreign_xbrl.json"
CACHE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".cache" / "foreign_xbrl"

# Tickers, not names: the ticker is exact, and which company it is comes from
# EDGAR rather than from anything written here.
CANDIDATES = ["ARGX", "GMAB", "BNTX", "ALVO", "GRFS", "HCM", "RDY", "INDV"]
PER_ISSUER = 6


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


def spoken_name(member: str) -> str:
    """A member as a person would write the product: `argx:VyvgartMember` -> Vyvgart."""
    return " ".join(word.capitalize() for word in words(member))


def main() -> None:
    tickers = json.loads(get("https://www.sec.gov/files/company_tickers.json") or b"{}")
    by_ticker = {row["ticker"].upper(): (str(row["cik_str"]).zfill(10), row["title"])
                 for row in tickers.values()}
    cases: list[dict] = []
    for tick in CANDIDATES:
        if tick not in by_ticker:
            print(f"  {tick}: not in the ticker map", file=sys.stderr)
            continue
        cik, title = by_ticker[tick]
        sub = get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        if not sub:
            continue
        recent = json.loads(sub)["filings"]["recent"]
        chosen = None
        for form, acc, filed, tagged in zip(
            recent["form"], recent["accessionNumber"], recent["filingDate"],
            recent.get("isXBRL", []),
        ):
            if not tagged or form not in {"6-K", "20-F"}:
                continue
            listing = get(f"{ARCHIVES}/{int(cik)}/{acc.replace('-','')}/index.json")
            if not listing:
                continue
            names = [i.get("name", "") for i in
                     json.loads(listing).get("directory", {}).get("item", [])]
            instance = _instance_document(names)
            primary = recent["primaryDocument"][recent["accessionNumber"].index(acc)]
            if instance:
                chosen = (form, acc, filed, instance, primary)
                break
        if not chosen:
            print(f"  {tick}: no tagged 6-K/20-F", file=sys.stderr)
            continue
        form, acc, filed, instance, primary = chosen
        raw = get(f"{ARCHIVES}/{int(cik)}/{acc.replace('-','')}/{instance}")
        if not raw:
            continue
        facts = parse_facts(raw)
        # Every member the filing puts on any axis, with the money facts it
        # carries. Nothing here decides which axis is the product one; that is
        # what the pipeline has to work out.
        seen: dict[str, int] = {}
        for fact in facts:
            if not (fact.states_an_amount and fact.unit and fact.months):
                continue
            for member in fact.members.values():
                seen[member] = seen.get(member, 0) + 1
        named = [m for m in sorted(seen, key=lambda m: -seen[m]) if spoken_name(m)]
        for member in named[:PER_ISSUER]:
            cases.append({
                "issuer": title,
                "ticker": tick,
                "cik": int(cik),
                "form": form,
                "accession": acc,
                "filed": filed,
                "instance": instance,
                "primary_document": primary,
                "member": member,
                "product": spoken_name(member),
                "expect": "a figure that is printed in the filing",
            })
        print(f"  {tick}: {form} {filed} {len(named[:PER_ISSUER])} members", file=sys.stderr)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cases, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(cases)} cases, "
          f"{len({c['ticker'] for c in cases})} issuers")


if __name__ == "__main__":
    main()
