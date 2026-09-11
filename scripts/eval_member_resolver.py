"""Does the member resolver still tell one drug from several?

The string rules refuse a member that runs names together, and they are right to
- most of the time. A filer reports several drugs on one line
("OPSUMITOPSYNVI"), and claiming that figure for the one brand you recognise
overstates it by the rest. But a filer also writes one drug's two trade names
the same way ("CompleraEviplera", US and EU), and refusing that loses a real
quarter.

Nothing in the spelling separates the two cases - as strings they are equally
similar, 0.57 against 0.62 - so this is the judgement the model exists to make,
and the prompt spent a long time forbidding it: the rule said to return nothing
whenever names ran together, without asking whether they were one drug or
several. Complera read 1 of 30 quarters as a result.

This is a prompt contract, not a coverage measure. It costs four model calls and
should be run after any edit to `xbrl_member_resolver.yaml`. The cases are two
that must resolve and two that must not, because a prompt loosened until the
first pair works will quietly break the second.

    OPENROUTER_API_KEY=... python scripts/eval_member_resolver.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.extraction.members import load_products  # noqa: E402
from app.llm.client import LLMModules  # noqa: E402

# (issuer, member, sibling members, expected product or None, what it tests)
CASES = [
    ("Gilead", "CompleraEviplera",
     ["AntiviralProductsAtripla", "AntiviralProductsTruvada", "Stribild", "Ambisome"],
     "Complera", "one drug under its US and EU trade names"),
    ("Gilead", "LDVSOF",
     ["AntiviralProductsAtripla", "Sovaldi", "AntiviralProductsTruvada"],
     "Harvoni", "the initials of a brand's two generic components"),
    ("Johnson & Johnson", "OPSUMITOPSYNVI",
     ["UPTRAVI", "XARELTO", "STELARA"],
     None, "two different drugs reported on one line"),
    ("Gilead", "HIVProductSales",
     ["AntiviralProductsAtripla", "AntiviralProductsTruvada"],
     None, "a category, not a product"),
]


async def run() -> dict:
    known = sorted(load_products())
    llm = LLMModules()
    rows = []
    for issuer, member, siblings, expected, what in CASES:
        try:
            out = await llm.resolve_xbrl_member(
                issuer=issuer, member=member, siblings=siblings, candidates=known
            )
        except Exception as exc:  # noqa: BLE001 - a failed call is a failed case
            rows.append((member, what, expected, f"ERROR {type(exc).__name__}", None, False))
            continue
        got = out.get("product")
        rows.append((member, what, expected, got, out.get("reason"), got == expected))

    print("Member resolver, prompt contract\n")
    for member, what, expected, got, reason, ok in rows:
        print(f"  {'ok ' if ok else 'BAD'} {member:20} -> {str(got):12} "
              f"(wanted {str(expected)})")
        print(f"      {what}")
        if reason:
            print(f"      said: {reason}")
    passed = sum(1 for r in rows if r[5])
    print(f"\n  {passed}/{len(rows)}")
    print("  PASS" if passed == len(rows) else "  FAIL")
    return {
        "cases": len(rows),
        "passed": passed,
        "results": [
            {"member": m, "expected": e, "got": g, "reason": r, "ok": ok}
            for m, _w, e, g, r, ok in rows
        ],
    }


if __name__ == "__main__":
    summary = asyncio.run(run())
    out = REPO_ROOT / "exports" / "member_resolver_contract.json"
    if out.parent.exists():
        out.write_text(json.dumps(summary, indent=1) + "\n")
    raise SystemExit(0 if summary["passed"] == summary["cases"] else 1)
