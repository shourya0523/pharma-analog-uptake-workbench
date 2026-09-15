"""Which issuers the answer keys under seed/ already spend.

Rule 4: a change is measured on a set it was not built from, and a held-out
set is only held out while none of its issuers appears in any other answer
key. Naming the keys by hand is how that property was once silently broken -
a set was built reusing three issuers another key scored, and nothing said
so - so the keys are discovered from the tree instead, and a set added later
cannot be forgotten here.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def answer_key_paths() -> list[Path]:
    """Every file under seed/ that carries an expectation: gold's jsonl series,
    the member and label holdouts, and the eval case files."""
    return sorted(REPO.glob("seed/*/*.jsonl")) + sorted(REPO.glob("seed/*/*.json"))


def issuers_in(path: Path) -> set[str]:
    """The issuers one answer key names.

    A gold row and a holdout row name a ``manufacturer``; a member case names
    its ``issuer``; an eval case names the manufacturer a person would type.
    All of them are answer keys.
    """
    text = path.read_text()
    found: set[str] = set()
    if path.suffix == ".jsonl":
        for line in text.splitlines():
            if line.strip():
                found.add(json.loads(line).get("manufacturer", ""))
        return {name for name in found if name}
    payload = json.loads(text)
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    for case in cases or ():
        if isinstance(case, dict):
            found.add(case.get("issuer") or case.get("manufacturer") or "")
    return {name for name in found if name}


def spent_issuers(*, excluding: Path) -> set[str]:
    """Every issuer any answer key other than ``excluding`` is spent on."""
    spent: set[str] = set()
    for path in answer_key_paths():
        if path.exists() and excluding.exists() and path.samefile(excluding):
            continue
        spent |= issuers_in(path)
    return spent


# What kind of company it is, rather than which one. "Pharmaceuticals" is in
# both "Vertex Pharmaceuticals" and "Jazz Pharmaceuticals" and distinguishes
# neither.
COMPANY_WORDS = frozenset({
    "pharmaceuticals", "pharma", "inc", "inc.", "corp", "corporation", "company",
    "co", "plc", "ltd", "limited", "holdings", "group", "sciences",
    "therapeutics", "laboratories", "labs", "biosciences", "industries", "nv",
    "sa", "ag", "as", "and", "the",
})


def identifying(issuer: str) -> set[str]:
    """The words of an issuer's name that say which company it is.

    "Actelion/J&J" names Johnson & Johnson, so comparison is on these words
    rather than on the string an answer key happened to write.
    """
    words = issuer.lower().replace("/", " ").replace("-", " ").replace("&", " ").split()
    return {w for w in words if w not in COMPANY_WORDS}


def scored_words(*, excluding: Path) -> set[str]:
    return {w for issuer in spent_issuers(excluding=excluding) for w in identifying(issuer)}


def products_in(path: Path) -> set[str]:
    """The products one answer key names.

    A gold row names a ``drug_name``; an eval case names the drug a person
    would type; a member case names the member, whose own spelling carries the
    brands it runs together. All of them are answers.
    """
    text = path.read_text()
    found: set[str] = set()
    if path.suffix == ".jsonl":
        for line in text.splitlines():
            if line.strip():
                found.add(json.loads(line).get("drug_name", ""))
        return {name for name in found if name}
    payload = json.loads(text)
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    for case in cases or ():
        if not isinstance(case, dict):
            continue
        found.add(case.get("drug_name") or "")
        member = case.get("member") or ""
        found.add(member.split(":")[-1].removesuffix("Member"))
        found.add(case.get("expected") or case.get("expected_product") or "")
    return {name for name in found if name}


def scored_products(*, excluding: Path | None = None) -> set[str]:
    """Every product any answer key holds an answer for.

    ``excluding`` skips one key, which is what a set checking its own products
    are held out needs: without it the set finds itself and every case looks
    already scored. The default spends nothing, so a caller asking what the
    prompts must not name still sees all of them.
    """
    names: set[str] = set()
    for path in answer_key_paths():
        if excluding is not None and path.exists() and excluding.exists() and path.samefile(excluding):
            continue
        names |= products_in(path)
    return names
