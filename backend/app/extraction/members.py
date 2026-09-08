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
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

REGISTER_PATH = Path(__file__).resolve().parents[3] / "seed" / "xbrl_members.csv"
PRODUCTS_PATH = Path(__file__).resolve().parents[3] / "seed" / "product_attributes.csv"
REGISTER_FIELDS = ("issuer", "member", "product", "method", "confidence", "note")

# A model told to abstain when unsure, that answers anyway and then reports low
# confidence, has said it is guessing. Below this the answer is kept in the
# register for a person to settle and is not used.
LLM_CONFIDENCE_FLOOR = 0.8

# Members that name no single product: an issuer's catch-all for the products it
# no longer breaks out. Recorded so the resolver stops asking about them.
NOT_A_PRODUCT = "-"


@dataclass(frozen=True)
class Resolution:
    """What a member was taken to mean, and on what basis."""

    member: str
    product: str | None
    method: str
    confidence: float = 1.0
    note: str = ""

    @property
    def resolved(self) -> bool:
        if not self.product or self.product == NOT_A_PRODUCT:
            return False
        return self.method != "llm" or self.confidence >= LLM_CONFIDENCE_FLOOR


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
                "method": entry.method,
                "confidence": f"{entry.confidence:g}",
                "note": entry.note,
            })


def resolve(
    member: str,
    products: list[str],
    register: dict[tuple[str, str], Resolution] | None = None,
    issuer: str = "",
) -> Resolution:
    """The register first, then the rules. A model is asked only for the rest.

    Returning an unresolved answer is not a failure - it is how a member the
    rules cannot place stays out of the data until someone or something decides
    what it is.
    """
    register = register if register is not None else load_register()
    if (issuer, member) in register:
        return register[(issuer, member)]
    return match(member, products)
