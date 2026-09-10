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
    spent = _issuers(
        REPO / "seed" / "gold" / "quarterly_revenue.jsonl",
        REPO / "seed" / "holdout" / "quarterly_revenue.jsonl",
        REPO / "seed" / "holdout2" / "quarterly_revenue.jsonl",
    )
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
    """`AvaproAvalideAndPlavix` is only a test if Plavix is returnable."""
    cases = {c["member"]: c for c in json.loads(HOLDOUT.read_text())["cases"]}
    assert "Plavix" in cases["bmy:AvaproAvalideAndPlavixMember"]["candidates"]
    assert "Braftovi" in cases["pfe:BraftoviMektoviMember"]["candidates"]
