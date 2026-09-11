"""Revenue candidates from facts the filer tagged, rather than from a table.

The deterministic table reader recovers what a table means from how it is laid
out. This does not have to: a tagged fact states its period, its unit, its
currency and which product it belongs to, so there is nothing to infer and
correspondingly nothing to get wrong in the ways ``fingerprint.py`` exists to
prevent.

What it can still get wrong is identity, and that is the whole of the work here.
An axis member is a name the filer invented, so which product it refers to comes
from ``members.py`` and its register - never from matching the member's text
against the product being asked for, which is how "Calderon" ends up answered
with Calderon XR's revenue.

Two rules keep the answers honest:

* only a fact with no geographic axis on it is a product's revenue. A regional
  line is a real figure about a smaller thing, and publishing it as the
  product's would understate the product by the rest of the world.
* only a fact whose member resolves to exactly the product asked for. A member
  covering several products - the combined lines filers report on one row - is
  refused, because the figure covers all of them.
"""

from __future__ import annotations

from typing import Any

from app.extraction.members import Resolution, load_register, resolve, stored
from app.parsing.xbrl import (
    Fact,
    _product_member,
    filer_category,
    parse_facts,
    product_facts,
)

# A tagged fact is the filer's own assertion, checked by the filer's auditors
# and machine-readable. It is a better claim than a number read off a page, and
# is scored above the table reader's 0.75 for that reason.
TAGGED_CONFIDENCE = 0.9


def _million(fact: Fact) -> float | None:
    """The value in millions. Facts are filed in whole units."""
    if fact.unit and fact.unit not in {"USD"}:
        return None
    return fact.value / 1_000_000.0


def rounding_uncertainty(fact: Fact) -> float | None:
    """How far a tagged fact may sit from the true figure, in USD millions.

    Half the unit the filer rounded to. A fact tagged `decimals="-6"` is within
    half a million of the truth; one tagged `INF` is exact; one that says
    nothing gets None, because unknown precision is not the same as exact and
    a derivation over it cannot be bounded.
    """
    unit = fact.rounding_unit
    if unit is None:
        return None
    return unit / 2.0 / 1_000_000.0


def candidates_from_instance(
    raw: bytes,
    *,
    product: str,
    issuer: str = "",
    products: list[str] | None = None,
    register: dict[tuple[str, str], Resolution] | None = None,
    learned: dict[tuple[str, str], Resolution] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """(candidates, notes) for one product, from one XBRL instance.

    ``issuer`` keys the register lookup and is not optional in practice:
    without it the register is never consulted, only the string rules apply,
    and every decision the model has made counts for nothing.

    ``learned`` collects the resolutions the rules made that the register did
    not already hold, so a caller with somewhere durable to put them can.

    An empty result is the ordinary case for a filing from before the issuer's
    tagging cutoff, and says so in the notes rather than looking like a failure
    to read something that was there.
    """
    notes: list[str] = []
    register = register if register is not None else load_register()
    known = products if products is not None else [product]

    # How the product axis is found. A filer states products on whatever axis
    # its taxonomy gives it, so the axis is not named here - each member is put
    # to the same resolver that decides which product a member means, and the
    # axis whose members it can place is the product axis. Memoised because
    # every fact in the instance is asked.
    placed: dict[str, bool] = {}

    def names_a_product(member: str) -> bool:
        if member not in placed:
            placed[member] = resolve(member, known, register, issuer=issuer).resolved
        return placed[member]

    facts = product_facts(parse_facts(raw), names_a_product=names_a_product)
    if not facts:
        category = filer_category(raw) or "unknown filer category"
        notes.append(f"no product-level facts tagged ({category})")
        return [], notes

    found: list[dict[str, Any]] = []
    # A filer tags the same figure in more than one place - once in the revenue
    # note, once in the segment table - so the instance carries it twice under
    # different context ids. Same product, same period, same number, one answer.
    seen: set[tuple[str, float]] = set()
    for fact in facts:
        member = _product_member(fact, names_a_product) or ""
        # Through `resolve` rather than indexing the register here, so this
        # reader gets both of the register's keys: issuer-and-member, because
        # us-gaap:ProductMember is one issuer's sole product and a meaningless
        # total for everyone else; and the member's identity rather than its
        # spelling, because the bulk extracts write "CalderonXR" where an
        # instance writes "acme:CalderonXRMember".
        resolution = resolve(member, known, register, issuer=issuer)
        # Anything the register did not hand back is new: a member it has never
        # seen, or one whose recorded "nothing matched" was about a different
        # product list and has just been superseded by the rules. Worth writing
        # down not because recomputing it is expensive - the string rules are
        # free - but because a decision nobody can see is a decision nobody can
        # correct. This is the row a reviewer overrides.
        if (resolution.resolved and learned is not None
                and stored(register, issuer, member) is not resolution):
            learned[(issuer, member)] = resolution
        if not resolution.resolved or resolution.product != product:
            continue
        value = _million(fact)
        if value is None:
            notes.append(f"{member}: unit {fact.unit} is not USD")
            continue
        if value <= 0:
            # Revenue is not negative. A figure that is belongs to something
            # subtracted from revenue - a rebate, a return, a chargeback - and
            # filers tag those on the product axis beside the sales they reduce.
            # The member reads like a product either way, so the sign is what
            # separates them.
            notes.append(f"{member}: {value:,.1f}m is not a revenue")
            continue
        signature = (fact.period or "", round(fact.value, 2))
        if signature in seen:
            continue
        seen.add(signature)
        found.append({
            "period": fact.period,
            "period_type": "quarterly" if fact.months == 3 else "annual",
            "value_reported": fact.value,
            "value_normalized_usd_millions": value,
            "currency": "USD",
            "unit": "units",
            "revenue_scope": "Product family",
            "formulation": None,
            # There is no line of prose to quote. The citation names the fact
            # precisely enough to be found in the instance and checked.
            "source_quote": fact.citation,
            "product_mentioned_in_quote": True,
            "is_company_total": False,
            "confidence": TAGGED_CONFIDENCE,
            "extraction_method": "xbrl_fact",
            "xbrl_member": member,
            "xbrl_context": fact.context_id,
            "member_resolved_by": resolution.method,
            "rounding_uncertainty_usd_millions": rounding_uncertainty(fact),
            "_from_table": False,
            "_from_xbrl": True,
        })
    if not found:
        notes.append(f"{len(facts)} product facts tagged, none resolving to {product}")
    return found, notes
