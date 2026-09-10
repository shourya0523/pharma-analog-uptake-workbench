"""Does the resolver tell a brand family from a roll-up over several products?

A filer runs names together on one line for two different reasons, and the
answer differs:

    ChantixChampix          one drug, US and EU names        -> that drug
    AvaproAvalideAndPlavix  a roll-up over two tagged lines  -> nothing

Nothing in the spelling separates them. What does separate them is on the axis
already: if a name run together with this one is ALSO tagged as its own member
in the same filing, this line is a roll-up over a figure that exists
separately, and claiming it for one brand overstates it by the rest. If none of
them is, this line IS the issuer's published figure for that product and there
is no finer one to prefer.

Refusing indiscriminately is not the safe default it looks like. Measured
against gold, Johnson & Johnson's `PROCRITEPREXMember` and
`INVEGASUSTENNAXEPLIONTRINZATREVICTAMember` carry exactly the figures gold
holds for Procrit and Invega Sustenna - 12 quarters, 12 exact matches - and the
resolver returned nothing for both while resolving `SimponiSimponiAria` and
`INVOKANAINVOKAMET`, which are the same kind of line.

The cases are drawn from sixteen issuers that no answer key in this repository
uses: not gold's five, not holdout's or holdout2's eight. The siblings are the
real ones, from the same instance the member appears in - deliberately not a
union over years, because a filer that renames a member shows both spellings
across time and that is a rename, not two products.

    OPENROUTER_API_KEY=... python scripts/eval_combined_name_members.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.llm.client import LLMModules  # noqa: E402

HOLDOUT = REPO_ROOT / "seed" / "holdout_members" / "combined_name_members.json"


async def run(limit: int | None = None) -> dict:
    payload = json.loads(HOLDOUT.read_text())
    cases = payload["cases"][:limit]
    llm = LLMModules()
    rows = []
    for case in cases:
        try:
            out = await llm.resolve_xbrl_member(
                issuer=case["issuer"], member=case["member"],
                siblings=case["siblings"], candidates=case["candidates"],
            )
        except Exception as exc:  # noqa: BLE001 - a failed call is a failed case
            rows.append({**case, "got": f"ERROR {type(exc).__name__}",
                         "reason": "", "ok": False})
            continue
        got = out.get("product")
        rows.append({**case, "got": got, "reason": out.get("reason", ""),
                     "confidence": out.get("confidence"),
                     "ok": got == case["expected"]})

    resolve = [r for r in rows if r["expected"]]
    refuse = [r for r in rows if not r["expected"]]
    print("Combined-name members, held-out issuers\n")
    for label, group in (("must resolve", resolve), ("must refuse", refuse)):
        print(f"  -- {label} --")
        for r in group:
            short = r["member"].split(":")[-1].replace("Member", "")
            print(f"   {'ok ' if r['ok'] else 'BAD'} {short:36} -> {str(r['got']):14}"
                  f" (wanted {r['expected']})")
            if not r["ok"]:
                print(f"        {r['why']}")
                if r["reason"]:
                    print(f"        said: {r['reason']}")
        print()
    passed = sum(1 for r in rows if r["ok"])
    print(f"  resolve {sum(1 for r in resolve if r['ok'])}/{len(resolve)}   "
          f"refuse {sum(1 for r in refuse if r['ok'])}/{len(refuse)}   "
          f"total {passed}/{len(rows)}")
    return {
        "cases": len(rows), "passed": passed,
        "resolve": [sum(1 for r in resolve if r["ok"]), len(resolve)],
        "refuse": [sum(1 for r in refuse if r["ok"]), len(refuse)],
        "results": [{k: r[k] for k in ("issuer", "member", "expected", "got",
                                       "reason", "ok")} for r in rows],
    }


if __name__ == "__main__":
    summary = asyncio.run(run())
    out = REPO_ROOT / "exports" / "combined_name_members.json"
    if out.parent.exists():
        out.write_text(json.dumps(summary, indent=1) + "\n")
    raise SystemExit(0)
