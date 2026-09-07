"""Build the register that maps a filer's XBRL member names to our products.

An axis member is a private invention - ``uthr:TyvasoDPIMember``,
``gild:HIVProductsBiktarvyMember`` - and there is no central registry of them.
This walks the issuers we track, collects every member they tag revenue on, and
resolves each one:

    string rules   whole-word matching, longest wins (app/extraction/members.py)
    a model        only for what the rules cannot place
    a person       by editing seed/xbrl_members.csv, which always wins

The point of writing it down is that each decision is made once. The register is
a small CSV in version control, so a mapping arrives as a reviewable diff rather
than as behaviour that changed inside a model call.

    SEC_CONTACT='project you@example.com' python scripts/build_member_register.py
    SEC_CONTACT='...' python scripts/build_member_register.py --no-llm
"""

from __future__ import annotations

# ruff: noqa: BLE001 - an issuer that fails to fetch is skipped, not fatal
import argparse
import asyncio
import collections
import json
import os
import pathlib
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.extraction.members import (
    NOT_A_PRODUCT,
    Resolution,
    load_register,
    match,
    save_register,
)
from app.llm.client import LLMModules
from app.parsing.xbrl import parse_facts, product_facts

UA = os.environ.get("SEC_CONTACT")
GOLD = REPO / "seed" / "gold" / "quarterly_revenue.jsonl"

# The issuers gold tracks. A new issuer is added here and the register regrows.
ISSUERS = {
    "Gilead": 882095,
    "Johnson & Johnson": 200406,
    "United Therapeutics": 1082554,
    "Merck": 310158,
    "Liquidia": 1819576,
}


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def recent_instances(cik: int, forms=("10-Q", "10-K"), per_form: int = 2) -> list[str]:
    """URLs of the newest XBRL instances, which carry the current member names."""
    payload = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
    recent = payload["filings"]["recent"]
    taken: collections.Counter[str] = collections.Counter()
    urls = []
    for form, accession, _date in zip(
        recent["form"], recent["accessionNumber"], recent["filingDate"], strict=False
    ):
        if form not in forms or taken[form] >= per_form:
            continue
        stripped = accession.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{stripped}"
        try:
            listing = json.loads(get(f"{base}/index.json"))["directory"]["item"]
        except Exception as exc:
            print(f"    {accession}: index unavailable ({type(exc).__name__})")
            continue
        instance = [i["name"] for i in listing if i["name"].endswith("_htm.xml")]
        if instance:
            urls.append(f"{base}/{instance[0]}")
            taken[form] += 1
        time.sleep(0.15)
    return urls


def members_by_issuer() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for issuer, cik in ISSUERS.items():
        members: set[str] = set()
        for url in recent_instances(cik):
            try:
                facts = product_facts(parse_facts(get(url)), worldwide_only=False)
            except Exception as exc:
                print(f"  {issuer}: {url.rsplit('/', 1)[-1]}: {exc}")
                continue
            members.update(f.product_member for f in facts if f.product_member)
            time.sleep(0.15)
        found[issuer] = members
        print(f"  {issuer:<22}{len(members):>3} members tagged")
    return found


async def ask_model(
    pending: list[tuple[str, str, list[str], list[str]]],
) -> dict[tuple[str, str], Resolution]:
    llm = LLMModules()
    answers: dict[tuple[str, str], Resolution] = {}
    for issuer, member, siblings, candidates in pending:
        try:
            reply = await llm.resolve_xbrl_member(
                issuer=issuer, member=member, siblings=siblings, candidates=candidates
            )
        except Exception as exc:
            print(f"    {member}: model error {type(exc).__name__}")
            continue
        product = (reply or {}).get("product")
        reason = (reply or {}).get("reason", "")
        confidence = float((reply or {}).get("confidence") or 0)
        answers[(issuer, member)] = Resolution(
            member=member,
            product=product or NOT_A_PRODUCT,
            method="llm",
            confidence=confidence,
            note=reason[:200],
        )
        verdict = product or "(nothing)"
        print(f"    {member.split(':')[-1]:<44} -> {verdict}  [{confidence:.2f}] {reason[:70]}")
    return answers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-llm", action="store_true",
                        help="string rules only; leave the rest unresolved")
    args = parser.parse_args()
    if not UA:
        raise SystemExit("Set SEC_CONTACT, e.g. 'project you@example.com'")

    products_by_issuer: dict[str, list[str]] = collections.defaultdict(list)
    for row in (json.loads(line) for line in GOLD.read_text().splitlines() if line.strip()):
        maker = row["manufacturer"].replace("Actelion/J&J", "Johnson & Johnson")
        if row["drug_name"] not in products_by_issuer[maker]:
            products_by_issuer[maker].append(row["drug_name"])

    print("collecting members from the newest filings")
    tagged = members_by_issuer()

    existing = load_register()
    resolved: dict[tuple[str, str], Resolution] = dict(existing)
    pending: list[tuple[str, str, list[str], list[str]]] = []
    kept = by_rules = 0
    for issuer, members in tagged.items():
        candidates = products_by_issuer.get(issuer, [])
        for member in sorted(members):
            if (issuer, member) in existing:
                kept += 1
                continue
            outcome = match(member, candidates)
            if outcome.resolved:
                resolved[(issuer, member)] = outcome
                by_rules += 1
            else:
                pending.append((issuer, member, sorted(members), candidates))

    print(f"\nalready in the register: {kept}")
    print(f"resolved by the rules:   {by_rules}")
    print(f"left for the model:      {len(pending)}")

    if pending and not args.no_llm:
        print("\nasking the model about the rest")
        resolved.update(asyncio.run(ask_model(pending)))
    elif pending:
        for issuer, member, _siblings, _candidates in pending:
            resolved[(issuer, member)] = Resolution(member, NOT_A_PRODUCT, "unmatched", 0.0,
                                                    "string rules found no product")

    save_register(resolved)
    named = sum(1 for r in resolved.values() if r.resolved)
    print(f"\nregister written: {len(resolved)} members, {named} naming a product we track")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
