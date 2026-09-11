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

One decision is not like that, and telling the two apart is what ``verdict``
is for. "This member is a category total" is a fact about the member, and holds
against any list. "This member named nothing in the candidate list" is a fact
about the list, and against a different list it claims nothing at all. Written
down as the same answer, the second becomes a veto: a member recorded as naming
no product while a drug went untracked stays unplaceable for the run that
uploads that drug, even where the string rules would place it outright. So a
negative carries a fingerprint of the list it was judged against and binds only
for that list, while a resolution to a product binds for every list.
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

# `resolve` is called once per member per filing, so re-keying the register on
# every call would be the same work repeated. Keyed on the register's identity
# and length, and holding one entry, so a register that is rebuilt or added to
# is re-indexed rather than answered from a stale index.
_IDENTITY_CACHE: dict[int, tuple[int, dict[tuple[str, str], "Resolution"]]] = {}


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


def canonical_member(member: str) -> str:
    """A member's identity, independent of which reader spelled it.

    The same member reaches the register under two notations: an instance
    document names it in full (``gild:CompleraEvipleraMember``) and the bulk
    notes datasets store the segment stripped of prefix and suffix
    (``CompleraEviplera``). Keyed literally, a decision made from one source is
    invisible to the other, and the register exists so a decision is made once.

    Case is dropped for the same reason - a filer writes both ``AmBisome`` and
    ``Ambisome`` - so the register need not carry one member twice.
    """
    local = re.sub(r"Member$", "", member.split(":")[-1])
    return re.sub(r"[^a-z0-9]", "", local.lower())


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


# A member joining two names is a line covering both, and the trailing-run rule
# would quietly award it to whichever is written last: `RemicadeAndSimponi`
# resolved to Simponi, a figure that includes Remicade. The rules cannot tell a
# joined pair from a category ending in a product's name, so they decline and
# the model decides - which costs one call and can still resolve it, where
# guessing costs a real number attributed to the wrong product.
_JOINED = frozenset({"and", "plus"})
_JOINING_MARKS = ("&", "+")

# A member can also name a product in order to say the line is everything BUT
# that product. The trailing-run rule reads the name at the end and hands the
# residual to the one thing it is defined to exclude:
#
#   ProductsExcludingALDURAZYME -> Aldurazyme      (BioMarin files this)
#   AllProductsExceptNuVessa    -> NuVessa
#   ProductsOtherThanCalderon   -> Calderon
#
# It is the `RemicadeAndSimponi` defect inverted - there the figure was too
# large, here it is the complement of the product it gets published as - and it
# arrives at the top of CLAIM_STRENGTH, as a fact the filer tagged. The model
# is not asked either: a line defined by what it leaves out is not any single
# product's revenue, whoever reads it.
_EXCLUDING = frozenset({"excluding", "excluded", "except", "excludes",
                        "other", "than", "outside", "without"})


def match(member: str, products: list[str]) -> Resolution:
    """Resolve one member against the products we track, or decline to."""
    parts = words(member)
    if not parts:
        return Resolution(member, None, "unknown", 0.0, "no words in member")
    local = member.split(":")[-1]
    if any(part in _JOINED for part in parts) or any(m in local for m in _JOINING_MARKS):
        return Resolution(member, None, "joined", 0.0,
                          "member joins names; a figure covering both is not one product's")
    # The marker has to sit before the trailing run, because that is the run the
    # rules below would otherwise read as the product. A member ending in one
    # ("HIV Other") names no product either, and the rules decline it as
    # unmatched without help.
    if any(part in _EXCLUDING for part in parts[:-1]):
        return Resolution(
            member, None, "excluding", 0.0,
            "member names a product to exclude it; the line is everything but that",
        )
    # Keyed on the run's words joined up, not on the tuple of words, because
    # only the *member* is a machine-generated identifier whose capitals mark
    # word boundaries. A brand may carry a capital of its own - "AmBisome" -
    # and `words` then splits the product into ["am", "bisome"] while the filer
    # writing it plainly gives ["ambisome"]. Two words never equal one, so the
    # match was impossible however the filer spelled it, and only a hand-added
    # register entry was covering it.
    #
    # Runs still begin at the member's own token boundaries, so this stays a
    # whole-word rule: "tyvaso" is not a trailing run of "TyvasoDPI" and does
    # not become one by being joined up.
    by_words: dict[str, set[str]] = {}
    for product in products:
        by_words.setdefault("".join(words(product)), set()).add(product)
    for run in _suffixes(parts):
        claimants = by_words.get("".join(run))
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

    The register is tried under both its keys - the member as spelled, then the
    member's identity, because the bulk extracts write "CompleraEviplera" where
    an instance writes ``gild:CompleraEvipleraMember``.

    "Where it still applies" is the rest. A decision that nothing matched is
    only as good as the list it was made against, so against a different list
    the rules run again rather than the old answer standing.

    Returning an unresolved answer is not a failure - it is how a member the
    rules cannot place stays out of the data until someone or something decides
    what it is.
    """
    register = register if register is not None else load_register()
    entry = stored(register, issuer, member)
    if entry is not None and entry.binds(products):
        return entry
    return match(member, products)


def stored(
    register: dict[tuple[str, str], Resolution],
    issuer: str,
    member: str,
) -> Resolution | None:
    """The decision on record for this member, under either of the register's keys.

    Separate from `resolve` because a caller that writes decisions down needs
    to know whether one came from the register or from the rules, and cannot
    tell from the answer alone.
    """
    return (
        register.get((issuer, member))
        or _by_identity(register).get((issuer, canonical_member(member)))
    )


def _by_identity(
    register: dict[tuple[str, str], Resolution],
) -> dict[tuple[str, str], Resolution]:
    """The register re-keyed on member identity rather than on spelling.

    Where two spellings of one member were decided differently, neither is
    returned. That is a disagreement in the register, and answering it by
    whichever row was read last is the same coin toss `match` refuses to make
    for two products that read the same.
    """
    cached = _IDENTITY_CACHE.get(id(register))
    if cached is not None and cached[0] == len(register):
        return cached[1]
    index: dict[tuple[str, str], Resolution] = {}
    disputed: set[tuple[str, str]] = set()
    for (issuer, member), entry in register.items():
        key = (issuer, canonical_member(member))
        seen = index.get(key)
        if seen is not None and seen.product != entry.product:
            disputed.add(key)
        index[key] = entry
    for key in disputed:
        index.pop(key, None)
    _IDENTITY_CACHE.clear()
    _IDENTITY_CACHE[id(register)] = (len(register), index)
    return index
