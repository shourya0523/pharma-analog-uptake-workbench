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
import csv
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
from app.parsing.notes_datasets import (
    iter_facts,
    load_dimensions,
    load_submissions,
)
from app.parsing.xbrl import parse_facts, product_facts

UA = os.environ.get("SEC_CONTACT")

# The products the pipeline tracks, and their issuers. Deliberately NOT
# seed/gold: gold is the answer key, and a register built from it would carry
# the key's decisions into a file the pipeline reads at run time. This is
# curated reference data, which gold itself is built from - the dependency runs
# that way round and must not be reversed.
ATTRIBUTES = REPO / "seed" / "product_attributes.csv"

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


def members_from_notes(roots: list[pathlib.Path]) -> dict[str, set[str]]:
    """Members an issuer tagged in the eras these extracts cover.

    `recent_instances` reads the newest filings, which carry the *current*
    names. That is the right source for what a filer tags today and no source
    at all for what it tagged before: a brand renamed, a co-formulation the
    issuer stopped breaking out, a drug tagged by its generic components until
    it had a brand. Those members exist only in older filings, so the register
    could never resolve them, and the pipeline read their quarters as an issuer
    that tags nothing.

    The bulk extracts carry every filer's members per month back to 2009 and
    are already parsed by `notes_datasets`, so history costs a download rather
    than a second walk over EDGAR's older instance layouts.
    """
    by_cik = {cik: issuer for issuer, cik in ISSUERS.items()}
    found: dict[str, set[str]] = {issuer: set() for issuer in ISSUERS}
    for root in roots:
        if not (root / "num.tsv").exists():
            print(f"  {root.name}: not an extract, skipped")
            continue
        subs = load_submissions(root, ciks=set(by_cik), forms={"10-K", "10-Q"})
        if not subs:
            continue
        dims = load_dimensions(root)
        seen = 0
        for adsh, fact in iter_facts(root, submissions=subs, dimensions=dims):
            member = fact.product_member
            if member:
                found[by_cik[subs[adsh].cik]].add(member)
                seen += 1
        print(f"  {root.name:<12}{len(subs):>3} filings, {seen:>6} product facts")
    for issuer, members in found.items():
        print(f"  {issuer:<22}{len(members):>3} members tagged")
    return found


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
    parser.add_argument("--from-notes", type=pathlib.Path, action="append", metavar="DIR",
                        help="collect members from an unzipped Financial Statement and "
                             "Notes Data Set instead of the newest filings; repeatable, "
                             "and the way to reach members an issuer no longer uses")
    args = parser.parse_args()
    if not UA:
        raise SystemExit("Set SEC_CONTACT, e.g. 'project you@example.com'")

    with ATTRIBUTES.open(newline="") as handle:
        products = sorted({row["drug_name"].strip()
                           for row in csv.DictReader(handle) if row.get("drug_name")})
    print(f"{len(products)} products in seed/product_attributes.csv")

    if args.from_notes:
        print(f"collecting members from {len(args.from_notes)} extract(s)")
        tagged = members_from_notes(args.from_notes)
    else:
        print("collecting members from the newest filings")
        tagged = members_by_issuer()

    existing = load_register()
    resolved: dict[tuple[str, str], Resolution] = dict(existing)
    pending: list[tuple[str, str, list[str], list[str]]] = []
    kept = by_rules = 0
    for issuer, members in tagged.items():
        # Every product we track is a candidate for every issuer. Narrowing the
        # list per issuer would need a product-to-issuer mapping, and the only
        # one to hand is gold's.
        candidates = products
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
