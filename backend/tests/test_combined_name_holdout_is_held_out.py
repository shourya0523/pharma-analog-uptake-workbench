"""The combined-name holdout must not touch an issuer any answer key uses.

The rule this holdout measures was rewritten because the resolver refused
`jnj:PROCRITEPREXMember` and `jnj:INVEGASUSTENNAXEPLIONTRINZATREVICTAMember`,
whose figures gold holds for Procrit and Invega Sustenna. Gold is the oracle
that found the defect; measuring the fix on the same issuers would be marking
your own homework, and `seed/holdout` and `seed/holdout2` are already spent on
earlier changes.

So this checks the property that makes the number mean anything, rather than
the number.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOLDOUT = REPO / "seed" / "holdout_members" / "combined_name_members.json"


def _issuers(*paths: Path) -> set[str]:
    seen: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if line.strip():
                seen.add(json.loads(line).get("manufacturer", ""))
    return seen


def _every_other_answer_key() -> set[str]:
    """Every issuer any other set under seed/ is already spent on.

    Naming three files by hand is how this test passed while the property it
    exists to enforce was broken: `seed/holdout_labels` scores the peer guard
    on Alkermes, Biogen and Jazz Pharmaceuticals, this file was built reusing
    all three, and nothing said so. Discovering the keys instead means a set
    added later cannot be forgotten here.
    """
    spent: set[str] = set()
    for path in sorted(REPO.glob("seed/*/*.jsonl")) + sorted(REPO.glob("seed/*/*.json")):
        if HOLDOUT.samefile(path) if path.exists() and HOLDOUT.exists() else False:
            continue
        text = path.read_text()
        if path.suffix == ".jsonl":
            spent |= {json.loads(line).get("manufacturer", "")
                      for line in text.splitlines() if line.strip()}
        else:
            payload = json.loads(text)
            cases = payload.get("cases") if isinstance(payload, dict) else payload
            for case in cases or ():
                if isinstance(case, dict) and case.get("issuer"):
                    spent.add(case["issuer"])
    return {name for name in spent if name}


# What kind of company it is, rather than which one. "Pharmaceuticals" is in
# both "Vertex Pharmaceuticals" and "Jazz Pharmaceuticals" and distinguishes
# neither.
_COMPANY_WORDS = frozenset({
    "pharmaceuticals", "pharma", "inc", "corp", "corporation", "company", "co",
    "plc", "ltd", "limited", "holdings", "group", "sciences", "therapeutics",
    "laboratories", "labs", "biosciences", "industries", "nv", "sa", "ag", "as",
    "and", "the",
})


def _identifying(issuer: str) -> set[str]:
    words = issuer.lower().replace("/", " ").replace("-", " ").replace("&", " ").split()
    return {w for w in words if w not in _COMPANY_WORDS}


def test_no_case_comes_from_a_scored_issuer():
    payload = json.loads(HOLDOUT.read_text())
    spent = _every_other_answer_key()
    # "Actelion/J&J" names Johnson & Johnson, so compare on the identifying
    # words rather than on the string an answer key happened to write.
    scored = {w for issuer in spent for w in _identifying(issuer)}
    assert scored, "no answer keys found; this test would pass vacuously"
    for case in payload["cases"]:
        overlap = scored & _identifying(case["issuer"])
        assert not overlap, f"{case['issuer']} is already scored: {overlap}"


def test_both_answers_are_represented():
    """A set that only refuses is passed by a resolver that always refuses -
    which is the defect this exists to catch."""
    cases = json.loads(HOLDOUT.read_text())["cases"]
    assert sum(1 for c in cases if c["expected"]) >= 8
    assert sum(1 for c in cases if not c["expected"]) >= 5


def test_every_case_carries_its_own_evidence():
    """The rule under test reads the siblings, so a case without them tests
    nothing, and an expected product must be answerable from the candidates."""
    for case in json.loads(HOLDOUT.read_text())["cases"]:
        assert case["siblings"], case["member"]
        assert case["candidates"], case["member"]
        if case["expected"]:
            assert case["expected"] in case["candidates"], case["member"]


def test_a_refusal_case_offers_something_wrong_to_return():
    """A refusal is only a test if something wrong was available to return.

    `AvaproAvalideAndPlavix` tests nothing if Plavix is not among the
    candidates: refusing is then the only answer possible. Checked as a
    property rather than by naming members, because the version of this test
    that named them broke the moment the set was rebuilt - and naming them is
    what let the set drift from the issuers it claimed.
    """
    import re

    cases = json.loads(HOLDOUT.read_text())["cases"]
    refusals = [c for c in cases if not c["expected"]]
    assert all(c["candidates"] for c in refusals), "a refusal with nothing to return"

    # A category ("Other Oncology") is a fair refusal even though it names no
    # candidate - the failure it tests is returning any product for a category.
    # What the set must also hold is the hard kind: a member that names a
    # candidate and must still be refused, because the figure covers it and
    # more. Without one, the roll-up rule is never exercised.
    rollups = [
        c["member"] for c in refusals
        if any(re.sub(r"[^a-z0-9]", "", cand.lower())
               in re.sub(r"[^a-z0-9]", "", c["member"].split(":")[-1].lower())
               for cand in c["candidates"])
    ]
    assert rollups, (
        "no refusal case names one of its own candidates, so nothing here "
        "tests a roll-up - only whether a category is refused")
