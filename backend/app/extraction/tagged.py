"""Revenue candidates from facts the filer tagged, rather than from a table.

The deterministic table reader recovers what a table means from how it is laid
out. This does not have to: a tagged fact states its period, its unit, its
currency and which product it belongs to, so there is nothing to infer and
correspondingly nothing to get wrong in the ways ``fingerprint.py`` exists to
prevent.

What it can still get wrong is identity, and that is the whole of the work here.
An axis member is a name the filer invented, so which product it refers to comes
from ``members.py`` and its register - never from matching the member's text
against the product being asked for, which is how "Tyvaso" ends up answered with
Tyvaso DPI's revenue.

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

from app.extraction.members import Resolution, load_register, resolve
from app.parsing.xbrl import Fact, filer_category, parse_facts, product_facts

# A tagged fact is the filer's own assertion, checked by the filer's auditors
# and machine-readable. It is a better claim than a number read off a page, and
# is scored above the table reader's 0.75 for that reason.
TAGGED_CONFIDENCE = 0.9


def _million(fact: Fact) -> float | None:
    """The value in millions. Facts are filed in whole units."""
    if fact.unit and fact.unit not in {"USD"}:
        return None
    return fact.value / 1_000_000.0


def candidates_from_instance(
    raw: bytes,
    *,
    product: str,
    issuer: str = "",
    products: list[str] | None = None,
    register: dict[tuple[str, str], Resolution] | None = None,
    learned: dict[tuple[str, str], Resolution] | None = None,
    quarterly_only: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    """(candidates, notes) for one product, from one XBRL instance.

    ``issuer`` keys the register lookup and is not optional in practice: without
    it only the string rules apply, which is how this shipped in its first
    measured run and why the model's decisions counted for nothing.

    ``learned`` collects the resolutions the rules made that the register did
    not already hold, so a caller with somewhere durable to put them can.

    An empty result is the ordinary case for a filing from before the issuer's
    tagging cutoff, and says so in the notes rather than looking like a failure
    to read something that was there.
    """
    notes: list[str] = []
    facts = product_facts(parse_facts(raw))
    if not facts:
        category = filer_category(raw) or "unknown filer category"
        notes.append(f"no product-level facts tagged ({category})")
        return [], notes

    register = register if register is not None else load_register()
    known = products if products is not None else [product]
    found: list[dict[str, Any]] = []
    # A filer tags the same figure in more than one place - once in the revenue
    # note, once in the segment table - so the instance carries it twice under
    # different context ids. Same product, same period, same number, one answer.
    seen: set[tuple[str, float]] = set()
    for fact in facts:
        member = fact.product_member or ""
        # Keyed by issuer and member together: us-gaap:ProductMember is Yutrepia
        # for Liquidia, which markets one product, and a meaningless total for
        # anyone else. Looking it up by member alone answers with whichever
        # filer was written last.
        resolution = resolve(member, known, register, issuer=issuer)
        # `resolve` hands back the stored decision itself when one applies, so
        # anything else is new: a member the register has never seen, or one
        # whose recorded "nothing matched" was about a different product list
        # and has just been superseded by the rules. Worth writing down not
        # because recomputing it is expensive - the string rules are free - but
        # because a decision nobody can see is a decision nobody can correct.
        # This is the row a reviewer overrides.
        if (resolution.resolved and learned is not None
                and register.get((issuer, member)) is not resolution):
            learned[(issuer, member)] = resolution
        if not resolution.resolved or resolution.product != product:
            continue
        value = _million(fact)
        if value is None:
            notes.append(f"{member}: unit {fact.unit} is not USD")
            continue
        if quarterly_only and fact.months != 3:
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
            "_from_table": False,
            "_from_xbrl": True,
        })
    if not found:
        notes.append(f"{len(facts)} product facts tagged, none resolving to {product}")
    return found, notes
