"""Which issuers and products the answer keys under seed/ already spend.

Rule 4: a change is measured on a set it was not built from, and a held-out
set is only held out while none of its issuers appears in any other answer
key. So the keys are discovered from the tree rather than named: a set added
later cannot be forgotten here.

Discovery is the whole point of the module, so it is watched by
`test_every_answer_key_is_read.py` - a key file that this module opens and
gets no name out of is a silent hole, and that test turns it into a failure.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SEED = REPO / "seed"


def answer_key_paths() -> list[Path]:
    """Every file under seed/ that carries an expectation: gold's jsonl series,
    the member, label and foreign-XBRL holdouts, and the eval case files.

    Recursive, and rooted at seed/ rather than seed/*/: a set may sit directly
    under seed/ (`holdout_foreign_xbrl.json` does) or a directory deeper.
    """
    return sorted(SEED.rglob("*.jsonl")) + sorted(SEED.rglob("*.json"))


def cases_in(path: Path) -> list[dict]:
    """The rows of one answer key, whatever shape the file wraps them in.

    Three wrappers are in use: a jsonl stream, a bare top-level list, and an
    object with the rows under ``cases``. A file that is none of these - gold's
    `manifest.json` and `build_report.json` are summaries, not keys - yields no
    rows, which is why the discovery guard checks for names rather than rows.
    """
    text = path.read_text()
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        payload = json.loads(text)
        rows = payload.get("cases") if isinstance(payload, dict) else payload
    return [row for row in (rows or ()) if isinstance(row, dict)]


# Which key an answer file writes the company under. A snapshot of the key
# names in use across seed/; it goes stale the moment a set is added that
# spells it differently, and `test_every_answer_key_is_read` is what says so.
ISSUER_KEYS = ("manufacturer", "issuer")

# Likewise for the product. `expect` is deliberately absent: in the eval case
# files it holds the expected figures, and in the foreign-XBRL holdout it
# holds "nothing" / "a figure". It is an expectation, not a name.
PRODUCT_KEYS = ("drug_name", "drug", "product", "expected", "expected_product")


def issuers_in(path: Path) -> set[str]:
    """The issuers one answer key names.

    A gold row and a holdout row name a ``manufacturer``; a member, label or
    foreign-XBRL case names its ``issuer``. All of them are answer keys.
    """
    found = {
        str(case.get(key) or "")
        for case in cases_in(path)
        for key in ISSUER_KEYS
    }
    return {name for name in found if name}


def products_in(path: Path) -> set[str]:
    """The products one answer key names.

    A gold row names a ``drug_name``; a reference check names a ``drug``; a
    label and a foreign-XBRL case name a ``product``; a member case names the
    ``member``, whose own spelling carries the brands it runs together, and the
    ``expected`` product the resolver should return. All of them are answers.
    """
    found: set[str] = set()
    for case in cases_in(path):
        for key in PRODUCT_KEYS:
            found.add(str(case.get(key) or ""))
        member = str(case.get("member") or "")
        found.add(member.split(":")[-1].removesuffix("Member"))
    return {name for name in found if name}


# An accession as EDGAR writes it. A holdout case stores one under
# `accession`; an eval case stores it inside the `source` prose it was built
# from. Either way it names the filing, which is what makes two files one set.
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")


def accessions_in(path: Path) -> set[str]:
    """Every filing accession one answer key cites.

    Used to recognise two views of the same set. An eval case file built from a
    holdout cites the holdout's filings, so it is not an independent key and
    must not count as one when asking what an issuer has already been spent on.
    """
    return set(_ACCESSION.findall(path.read_text()))


def _excluded(excluding: Path | Iterable[Path]) -> list[Path]:
    paths = [excluding] if isinstance(excluding, Path) else list(excluding)
    return [p for p in paths if p.exists()]


def spent_issuers(*, excluding: Path | Iterable[Path]) -> set[str]:
    """Every issuer any answer key other than ``excluding`` is spent on.

    ``excluding`` may be several paths, because one held-out set is sometimes
    stored twice - once in its own shape and once as an eval case file built
    from it. Both are the set; neither spends the other's issuers.
    """
    skip = _excluded(excluding)
    spent: set[str] = set()
    for path in answer_key_paths():
        if any(path.samefile(other) for other in skip):
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
    "sa", "ag", "as", "and", "the", "se",
})

# An issuer name carries punctuation one answer key wrote and another did not:
# "NuVessa Pharmaceuticals, Inc." against "NuVessa". Splitting on anything that
# is not a letter or digit normalises both, and subsumes the joiners - "/",
# "&", "-" - that used to be replaced one at a time.
_WORD = re.compile(r"[^a-z0-9]+")


def identifying(issuer: str) -> set[str]:
    """The words of an issuer's name that say which company it is.

    "Calderon/NuVessa" names NuVessa, so comparison is on these words rather
    than on the string an answer key happened to write. A one-character token
    is never one of them: "Calderon A/S" says Danish limited company, not which
    company, and matching on `a` or `s` would call two unrelated issuers the
    same one.
    """
    words = _WORD.split(issuer.lower())
    return {w for w in words if len(w) > 1 and w not in COMPANY_WORDS}


def scored_words(*, excluding: Path | Iterable[Path]) -> set[str]:
    return {w for issuer in spent_issuers(excluding=excluding) for w in identifying(issuer)}


def scored_products() -> set[str]:
    """Every product any answer key holds an answer for."""
    names: set[str] = set()
    for path in answer_key_paths():
        names |= products_in(path)
    return names
