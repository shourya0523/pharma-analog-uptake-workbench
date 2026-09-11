"""Revenue candidates from tagged facts read in bulk.

The same claim as `tagged.py` - the filer's own machine-readable assertion -
reached through the Commission's quarterly extracts instead of through one
instance document at a time. Everything that decides what a fact *means* is
imported from the instance path rather than restated here, so the two cannot
disagree: `product_facts` applies the standard-element list, the
worldwide-is-the-absence-of-geography rule and the least-qualified-statement
rule; `members.py` decides which product a member names.

One thing differs and it matters. `DIM.segments` drops namespaces and the
"Member" suffix, so `acme:RespiratoryProductsNuVessaMember` arrives as
`RespiratoryProductsNuVessa`, and the register - keyed on the full member
string as an instance spells it -
cannot be looked up directly. Resolution therefore runs structurally first, by
the whole-word suffix match that needs no per-issuer seeding, and consults the
register only for a member that structure could not place.

That ordering is deliberate. The register only covers issuers someone has run
the builder against, so depending on it first would make this reader work for
those filers and return nothing elsewhere - a coverage cliff shaped like
whoever has been looked at, reading as "the data is not tagged there" rather
than "we never taught it these names".
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from app.extraction.members import Resolution, load_register, match
from app.extraction.tagged import TAGGED_CONFIDENCE, rounding_uncertainty
from app.parsing.notes_datasets import (
    Submission,
    iter_facts,
    load_dimensions,
    load_submissions,
)
from app.parsing.xbrl import Fact, _product_member, product_facts

# Forms whose XBRL exhibits carry the notes. An 8-K earnings exhibit is not
# tagged at all, so asking for one returns nothing and would look like a
# reading failure rather than an absence.
TAGGED_FORMS = frozenset({"10-K", "10-Q", "20-F", "40-F", "10-K405", "10-KT", "10-QT"})


def _resolve(
    member: str,
    *,
    product: str,
    issuer: str,
    known: list[str],
    register: dict[tuple[str, str], Resolution],
) -> Resolution | None:
    """Which product a bulk member names, structure first, register second."""
    structural = match(member, known)
    if structural.resolved:
        return structural
    # The register spells a member as the instance does. Compare on the part
    # that survives the extract's truncation rather than on the whole string.
    for (register_issuer, register_member), resolution in register.items():
        if register_issuer != issuer or not resolution.resolved:
            continue
        stem = register_member.split(":")[-1]
        for suffix in ("Member", "Domain"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
        if stem.casefold().endswith(member.casefold()):
            return resolution
    return None


def candidates_from_notes(
    root: Path,
    *,
    product: str,
    issuer: str,
    cik: int,
    products: list[str] | None = None,
    register: dict[tuple[str, str], Resolution] | None = None,
    forms: Iterable[str] | None = None,
    dimensions: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """(candidates, notes) for one product, from one extract directory.

    `root` holds `sub.tsv`, `dim.tsv` and `num.tsv` as downloaded. An empty
    result is an ordinary answer - a filer below its detail-tagging cutoff
    states nothing on the product axis - and says which of the possible reasons
    applies rather than reporting a bare zero.
    """
    notes: list[str] = []
    subs = load_submissions(
        root, ciks={cik}, forms=set(forms) if forms is not None else set(TAGGED_FORMS)
    )
    if not subs:
        notes.append(f"no tagged filing for CIK {cik} in {root.name}")
        return [], notes

    dims = dimensions if dimensions is not None else load_dimensions(root)
    by_submission: dict[str, list[Fact]] = defaultdict(list)
    for adsh, fact in iter_facts(root, submissions=subs, dimensions=dims):
        by_submission[adsh].append(fact)

    register = register if register is not None else load_register()
    known = products if products is not None else [product]

    # The same way the instance reader finds the product axis: by asking the
    # resolver, not by naming the axis. `DIM` strips a segment to its bare name
    # so the spelling here is the extract's rather than the filing's, which is
    # one more reason not to decide on it.
    placed: dict[str, bool] = {}

    def names_a_product(member: str) -> bool:
        if member not in placed:
            resolution = _resolve(member, product=product, issuer=issuer,
                                  known=known, register=register)
            placed[member] = resolution is not None and resolution.resolved
        return placed[member]

    found: list[dict[str, Any]] = []
    # The same figure is tagged in the revenue note and again in the segment
    # table, and a later filing repeats it as a prior-year comparative. Same
    # product, same period, same number, one answer - and the earliest filing
    # to state it is the one cited, because that is where it was first reported.
    seen: dict[tuple[str, float], dict[str, Any]] = {}
    product_axis_facts = 0

    for adsh in sorted(subs, key=lambda a: subs[a].filed):
        submission: Submission = subs[adsh]
        facts = product_facts(by_submission.get(adsh, []), names_a_product=names_a_product)
        product_axis_facts += len(facts)
        for fact in facts:
            member = _product_member(fact, names_a_product) or ""
            resolution = _resolve(
                member, product=product, issuer=issuer, known=known, register=register
            )
            if resolution is None or resolution.product != product:
                continue
            if fact.unit and fact.unit != "USD":
                notes.append(f"{member}: unit {fact.unit} is not USD")
                continue
            if fact.value <= 0:
                # Revenue is not negative; see `tagged.py`.
                notes.append(f"{member}: {fact.value / 1e6:,.1f}m is not a revenue")
                continue
            signature = (fact.period or "", round(fact.value, 2))
            if signature in seen:
                continue
            seen[signature] = {
                "period": fact.period,
                "period_type": "quarterly" if fact.months == 3 else "annual",
                "value_reported": fact.value,
                "value_normalized_usd_millions": fact.value / 1_000_000.0,
                "currency": "USD",
                "unit": "units",
                "revenue_scope": "Product family",
                "formulation": None,
                "source_quote": fact.citation,
                "source_url": submission.url,
                "product_mentioned_in_quote": True,
                "is_company_total": False,
                "confidence": TAGGED_CONFIDENCE,
                "extraction_method": "xbrl_fact",
                "xbrl_member": member,
                "xbrl_context": fact.context_id,
                "xbrl_accession": adsh,
                "member_resolved_by": resolution.method,
                "rounding_uncertainty_usd_millions": rounding_uncertainty(fact),
                "_from_table": False,
                "_from_xbrl": True,
                "_from_notes_dataset": True,
            }
    found = list(seen.values())
    if not found:
        notes.append(
            f"{product_axis_facts} product facts across {len(subs)} filing(s), "
            f"none resolving to {product}"
            if product_axis_facts
            else f"{len(subs)} filing(s) tag nothing on the product axis"
        )
    return found, notes
