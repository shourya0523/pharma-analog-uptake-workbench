"""Which product a filer's XBRL member names.

An axis member is a private invention. There is no registry: United Therapeutics
writes ``uthr:TyvasoDPIMember`` and Gilead writes
``gild:HIVProductsBiktarvyMember``, both meaning "this product", and either can
rename between filings. So the mapping has to be built and kept, and this module
is the register plus the rules for extending it.

Matching by substring is the obvious approach and it is wrong. ``"tyvaso"`` is a
substring of ``TyvasoDPIMember``, so a substring rule answers a question about
Tyvaso with Tyvaso DPI's revenue - a real number, the wrong product, and nothing
downstream would notice. The rule here is instead:

* compare whole words, never fragments;
* a product may match the member's full name or a trailing run of its words, so
  ``HIVProductsBiktarvy`` resolves to Biktarvy while ``TyvasoDPI`` does not
  resolve to Tyvaso;
* where several products match, the longest wins - ``NebulizedTyvaso`` is
  Nebulized Tyvaso rather than Tyvaso;
* where two products in the list read as the same words, nothing is returned.
  One of them would otherwise be picked silently by whichever the dictionary
  happened to keep.

What the rules cannot settle goes to a model, and whatever it decides is written
to the register with its reasoning, so the decision is made once, is reviewable
in a diff, and never has to be made again.

One decision is not like that, and telling them apart is what ``verdict`` is
for. "This member is a category total" is a fact about the member and holds for
good. "This member named no product we were tracking" is a fact about the
product list it was judged against, and the moment a run brings a different
list it is worth nothing. Both used to be written down as a bare ``-``, and
because the register is consulted before the rules, the second kind was a
standing veto: ``gild:TrodelvyMember`` was recorded as naming no product
because Trodelvy was not among the 46 in ``product_attributes.csv``, so a run
that did ask for Trodelvy read that ``-`` and skipped a fact the string rules
place on the first try. A negative now carries the fingerprint of the list it
was judged against and binds only for that list.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

REGISTER_PATH = Path(__file__).resolve().parents[3] / "seed" / "xbrl_members.csv"
PRODUCTS_PATH = Path(__file__).resolve().parents[3] / "seed" / "product_attributes.csv"
REGISTER_FIELDS = (
    "issuer", "member", "product", "verdict", "method", "confidence",
    "candidates_fingerprint", "note",
)

# What a decision claims, and therefore how long it lasts.
VERDICT_PRODUCT = "product"
# The member names no single product and never will: an issuer's category line,
# a total, a share of someone else's revenue. True of the member itself, so it
# holds against any product list. Only a person sets this - a model asked which
# product a member names cannot distinguish "none of these" from "none at all".
VERDICT_NOT_A_PRODUCT = "not_a_product"
# Nothing in the candidate list matched. Says nothing about the member beyond
# that list, and binds only for the list it was decided against.
VERDICT_NO_CANDIDATE_MATCH = "no_candidate_match"

# A model told to abstain when unsure, that answers anyway and then reports low
# confidence, has said it is guessing. Below this the answer is kept in the
# register for a person to settle and is not used.
LLM_CONFIDENCE_FLOOR = 0.8

# Members that name no single product: an issuer's catch-all for the products it
# no longer breaks out. Recorded so the resolver stops asking about them.
NOT_A_PRODUCT = "-"


def fingerprint(products: list[str]) -> str:
    """Identify a candidate list, so a decision can say what it was judged against.

    Order and duplicates do not change which products were on offer, so neither
    changes the fingerprint. Adding or removing one does, which is the whole
    point: that is exactly when a negative decision stops applying.
    """
    canonical = "\n".join(sorted({p.strip() for p in products if p and p.strip()}))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Resolution:
    """What a member was taken to mean, and on what basis."""

    member: str
    product: str | None
    method: str
    confidence: float = 1.0
    note: str = ""
    verdict: str = ""
    candidates_fingerprint: str = ""

    @property
    def resolved(self) -> bool:
        if not self.product or self.product == NOT_A_PRODUCT:
            return False
        return self.method != "llm" or self.confidence >= LLM_CONFIDENCE_FLOOR

    @property
    def claim(self) -> str:
        """The verdict, derived for a decision recorded before there was one."""
        if self.verdict:
            return self.verdict
        return VERDICT_PRODUCT if self.resolved else VERDICT_NO_CANDIDATE_MATCH

    def binds(self, products: list[str]) -> bool:
        """Whether this decision still answers for a run asking about ``products``.

        A member that names a product names it whoever is asking. A member that
        names no product *at all* is equally durable. What does not survive a
        change of list is "nothing here matched" - and answering with that is
        how a drug the rules would place gets silently dropped instead.
        """
        if self.resolved or self.claim == VERDICT_NOT_A_PRODUCT:
            return True
        if not self.candidates_fingerprint:
            # Decided against a list nobody wrote down. Treat it as spent
            # rather than as a veto of unknown provenance.
            return False
        return self.candidates_fingerprint == fingerprint(products)


def words(text: str) -> list[str]:
    """A member or product name as lowercase words.

    ``uthr:NebulizedTyvasoMember`` and ``Nebulized Tyvaso`` both become
    ``["nebulized", "tyvaso"]``, so the two vocabularies can be compared without
    either having to know how the other punctuates.
    """
    local = text.split(":")[-1]
    local = re.sub(r"Member$", "", local)
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", local)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if w]


def _suffixes(parts: list[str]) -> list[list[str]]:
    """Every trailing run of words, longest first.

    A filer often prefixes a member with its category - ``HIVProducts`` before
    ``Biktarvy`` - so the product is at the end. Only trailing runs count: a
    leading or middle match would let ``Tyvaso`` claim ``TyvasoDPI``.
    """
    return [parts[i:] for i in range(len(parts))]


def match(member: str, products: list[str]) -> Resolution:
    """Resolve one member against the products we track, or decline to."""
    parts = words(member)
    if not parts:
        return Resolution(member, None, "unknown", 0.0, "no words in member")
    by_words: dict[tuple[str, ...], set[str]] = {}
    for product in products:
        by_words.setdefault(tuple(words(product)), set()).add(product)
    for run in _suffixes(parts):
        claimants = by_words.get(tuple(run))
        if not claimants:
            continue
        # Longest first, so a member ending in a shorter product's name goes to
        # the longer one: NebulizedTyvaso is Nebulized Tyvaso, not Tyvaso.
        if len(claimants) > 1:
            return Resolution(member, None, "ambiguous", 0.0,
                              f"these read the same: {', '.join(sorted(claimants))}")
        product = next(iter(claimants))
        exact = tuple(parts) == tuple(run)
        return Resolution(
            member,
            product,
            "exact" if exact else "suffix",
            1.0 if exact else 0.9,
            "" if exact else f"product is the trailing words of {member.split(':')[-1]}",
        )
    return Resolution(member, None, "unmatched", 0.0, "no product's words end this member")


def load_products(path: Path | None = None) -> list[str]:
    """Every product this pipeline tracks, for resolving a member against.

    `match` decides between a member's possible readings by preferring the
    longest product name that ends it - "NebulizedTyvaso is Nebulized Tyvaso,
    not Tyvaso". That comparison can only be made against products it has been
    told about, so asked with a one-name list it has nothing to prefer and
    `uthr:NebulizedTyvasoMember` suffix-matches to Tyvaso: a sibling
    formulation's tagged revenue accepted as the product's own.

    This is pipeline reference data, not the answer key. The register is built
    from it, and `product_attributes.csv` is one of the files
    `test_gold_is_not_an_input` names as a legitimate pipeline input.
    """
    target = path or PRODUCTS_PATH
    if not target.exists():
        return []
    with target.open(newline="") as handle:
        return sorted(
            {
                name
                for row in csv.DictReader(handle)
                if (name := (row.get("drug_name") or "").strip())
            }
        )


def load_register(path: Path | None = None) -> dict[tuple[str, str], Resolution]:
    """The decisions already made, keyed by issuer and member.

    The issuer is part of the key because a member name is only unique within
    one filer's taxonomy. ``us-gaap:ProductMember`` is Yutrepia for Liquidia,
    which markets one product, and is a meaningless total for anyone else.
    """
    path = path or REGISTER_PATH
    if not path.exists():
        return {}
    with path.open(newline="") as handle:
        return {
            (row.get("issuer", ""), row["member"]): Resolution(
                member=row["member"],
                product=row["product"] or None,
                method=row["method"],
                confidence=float(row["confidence"] or 0),
                note=row.get("note", ""),
                verdict=row.get("verdict", ""),
                candidates_fingerprint=row.get("candidates_fingerprint", "") or "",
            )
            for row in csv.DictReader(handle)
        }


def save_register(entries: dict[tuple[str, str], Resolution], path: Path | None = None) -> None:
    """Write the register back, sorted, so a change reads as a diff."""
    path = path or REGISTER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTER_FIELDS)
        writer.writeheader()
        for issuer, member in sorted(entries):
            entry = entries[(issuer, member)]
            writer.writerow({
                "issuer": issuer,
                "member": entry.member,
                "product": entry.product or "",
                "verdict": entry.claim,
                "method": entry.method,
                "confidence": f"{entry.confidence:g}",
                "candidates_fingerprint": entry.candidates_fingerprint,
                "note": entry.note,
            })


def resolve(
    member: str,
    products: list[str],
    register: dict[tuple[str, str], Resolution] | None = None,
    issuer: str = "",
) -> Resolution:
    """The register first where it still applies, then the rules.

    "Where it still applies" is the whole of the change from looking the member
    up and taking whatever comes back. A decision that nothing matched is only
    as good as the list it was made against, so against a different list the
    rules run again rather than the old answer standing.

    Returning an unresolved answer is not a failure - it is how a member the
    rules cannot place stays out of the data until someone or something decides
    what it is.
    """
    register = register if register is not None else load_register()
    entry = register.get((issuer, member))
    if entry is not None and entry.binds(products):
        return entry
    return match(member, products)
