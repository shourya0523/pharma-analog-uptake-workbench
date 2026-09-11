from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher
from typing import Any

from app.extraction.members import split_camel
from app.parsing.evidence import SCOPE_PATTERNS, TOTAL_REVENUE_RE, product_aliases

# A label states more than one product by joining names with one of these. A
# hyphen is deliberately absent: "Calderon - Europe" is one product under a
# geography, and it is the commonest label shape there is.
_JOINER_RE = re.compile(r"\s*(?:\+|&|;|,|\band\b|\bwith\b|\bplus\b)\s*", re.IGNORECASE)

# A slash is not in that list, because a slash is what a filer writes between
# the names of ONE product: a brand and its generic ("CALDERON/CALDERINOL"), a
# brand and the name it carries in another market ("CALDERON/CALDERIX"), a
# brand and its own combination ("NUVESSA/NUVESSA-D"), or a brand and its other
# presentations ("CALDERON / CALDERON XR", "NUVESSA IV/NUVESSA SC/NUVESSA
# PEN"). Treating it as a product joiner refuses every one of those lines, and
# a filer that reports a product only under its slashed label publishes no
# other figure for it.
#
# What still separates products is the shape the filer uses for products:
# commas and "and", as in "share of pre-tax profits in the U.S. for CALDERON,
# NUVESSA and TAVORAL". And a slash-joined name that turns out to have a row of
# its own in the same table IS a separate product, so the line covers both -
# which is the same evidence the XBRL member resolver uses, applied to printed
# labels instead of axis members.
_SLASH_RE = re.compile(r"\s*/\s*")

# Words that qualify a product rather than name one. A part made only of these
# is not a competing brand.
_QUALIFIER_WORDS = frozenset(
    {
        "net", "gross", "sales", "sale", "revenue", "revenues", "product",
        "brand", "brands", "royalty", "royalties", "collaboration", "contract",
        "the", "a", "an", "of", "in", "for", "from", "to", "its", "our",
        "inc", "corp", "corporation", "ltd", "co", "plc", "sa", "ag", "nv",
        "llc", "gmbh", "group", "segment", "division", "business", "unit",
        "including", "excluding", "less", "and", "or",
        # Slices of a product's own sales. A filer printing "US", "Intl",
        # "WW" and "US Exports" beneath a brand means all four for that brand;
        # without these, "US Exports" reduces to "exports" and reads as a
        # competing name, which drops the row from the components and leaves
        # the worldwide total unidentifiable by its arithmetic.
        "export", "exports", "region", "regions", "geography", "geographic",
    }
)

# Words that say the line covers more than the product asked for. A part built
# only from these is an aggregate, which is not this product's own revenue
# even though no second brand is spelled out: "Calderon and other products".
_AGGREGATE_WORDS = frozenset(
    {"other", "others", "all", "combined", "franchise", "total", "aggregate", "products", "various", "misc", "miscellaneous"}
)

_SCOPE_RE = re.compile(
    "|".join(pattern for _label, pattern in SCOPE_PATTERNS), re.IGNORECASE
)


def _strip_noise(part: str) -> str:
    """What is left of a label part once scope and qualifier words are removed."""
    without_scope = _SCOPE_RE.sub(" ", part)
    words = [w for w in re.findall(r"[\w'&.-]+", without_scope.lower()) if w]
    return " ".join(w for w in words if w not in _QUALIFIER_WORDS)


def _is_spelling_variant(name: str, own: set[str]) -> bool:
    """Whether a name is the same product spelled for another market.

    A drug is often sold under one name in the US and a near-identical one in
    Europe, and a filer prints "Calderon/Calderyon" as a single line because it
    is a single product. Refusing that line loses a real quarter. Two names that are
    genuinely different products for the same indication are not near-spellings
    of each other, so the distance does the work a name mapping would.
    """
    for alias in own:
        if len(alias) < 5 or len(name) < 5:
            continue
        if SequenceMatcher(None, alias, name).ratio() >= 0.8:
            return True
    return False


def names_a_competing_product(
    label: str,
    aliases: list[str],
    siblings: Iterable[str] | None = None,
) -> str | None:
    """The other product this label names, or None when it names only ours.

    A product-sales schedule states its own competitors: the rows around this
    one are the filer's product list, so `siblings` answers the question
    without anyone writing a catalogue of brands. Where the label carries a
    name that has no row of its own - a franchise line naming four brands the
    filer never breaks out - the label's own shape answers instead.

    Neither route knows what any product is called, which is the point: a rule
    keyed to the brands we happen to hold refuses a shared line for those and
    waves the identical line through for every product we have not seen.
    """
    own = {alias.lower() for alias in aliases}
    named = _own_rows(siblings, own)
    parts = [p for p in _JOINER_RE.split(label or "") if p and p.strip()]

    for part in parts:
        for segment in _SLASH_RE.split(part):
            stripped = _strip_noise(segment)
            if not stripped:
                continue
            if any(alias in stripped or stripped in alias for alias in own):
                continue
            if _is_spelling_variant(stripped, own):
                continue
            # A part naming no brand but marking breadth still means the line
            # covers more than this product.
            if all(word in _AGGREGATE_WORDS for word in stripped.split()):
                return stripped
            if len(parts) > 1:
                return stripped
            # Slash-joined, so this is another name for the same product unless
            # the filer prints it on a row of its own - and then the line covers
            # two things the filer reports separately.
            if stripped in named:
                return stripped

    # A name with its own row elsewhere in the table is a product of this
    # issuer, whether or not a joiner separated it here.
    normalized = _normalize(label)
    for sibling in siblings or ():
        name = _strip_noise(sibling)
        if not name or len(name) < 3:
            continue
        # "Total revenues" reduces to "total", which is a word inside "Total
        # Calderon" and names no product. A sibling has to be a name to rule a
        # label out.
        if all(word in _AGGREGATE_WORDS for word in name.split()):
            continue
        if any(alias in name or name in alias for alias in own):
            continue
        if re.search(rf"\b{re.escape(name)}\b", normalized):
            return name
    return None


def _own_rows(siblings: Iterable[str] | None, own: set[str]) -> set[str]:
    """The names this table gives a row of their own, ours excluded."""
    rows: set[str] = set()
    for sibling in siblings or ():
        name = _strip_noise(sibling)
        if not name or len(name) < 3:
            continue
        if all(word in _AGGREGATE_WORDS for word in name.split()):
            continue
        if any(alias in name or name in alias for alias in own):
            continue
        rows.add(name)
    return rows


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def quote_mentions_product(
    quote: str,
    product: str,
    generic: str | None = None,
    extra_aliases: list[str] | None = None,
) -> bool:
    """Whether the quote names the product, however the quote spells it.

    Prose spells a name as people do. A tagged fact's citation spells it as
    the filer's taxonomy does - one identifier, capitals for word breaks, no
    spaces - so ``CalderonXrMember`` has to be read as ``Calderon XR`` before
    it can be found. Both spellings are tried.
    """
    q = _normalize(quote)
    spaced = _normalize(split_camel(quote))
    for alias in product_aliases(product, generic, extra=extra_aliases):
        if alias.lower() in q or _normalize(split_camel(alias)) in spaced:
            return True
    return False


def quote_mentions_generic_only(
    quote: str,
    product: str,
    generic: str | None,
    extra_aliases: list[str] | None = None,
) -> bool:
    if not generic or product.casefold() == generic.casefold():
        return False
    return quote_mentions_product(quote, generic, extra_aliases=extra_aliases) and not quote_mentions_product(
        quote, product, extra_aliases=extra_aliases
    )


def value_is_dosage_not_revenue(quote: str, value: float) -> bool:
    money_context = re.search(r"[$€£]|\b(?:usd|chf|eur|sales?|revenue|million|billion)\b", quote, re.IGNORECASE)
    value_text = re.escape(f"{value:g}")
    dose_context = re.search(rf"(?<![\d.]){value_text}\s*(?:mcg|mg|g|ml|%)\b", quote, re.IGNORECASE)
    return bool(dose_context and not money_context)


def quote_mentions_other_brand(
    quote: str,
    product: str,
    generic: str | None = None,
    extra_aliases: list[str] | None = None,
    peer_names: Iterable[str] | None = None,
) -> str | None:
    """The other brand a sentence names, judged against `peer_names`.

    `peer_names` is what the document itself lists - the product rows around
    the sentence - not a catalogue held in this file. With nothing to compare
    against the question cannot be answered, so this returns None rather than
    guessing; both callers apply it only to quotes that fail to name the
    product at all, and those are already refused for family and formulation
    scopes by the mention rule below.
    """
    q = _normalize(quote)
    own = {a.lower() for a in product_aliases(product, generic, extra=extra_aliases)}
    for peer in peer_names or ():
        name = _normalize(peer)
        if not name or len(name) < 3:
            continue
        if any(alias in name or name in alias for alias in own):
            continue
        if re.search(rf"\b{re.escape(name)}\b", q):
            return name
    return None


def is_xbrl_noise_quote(quote: str) -> bool:
    q = quote or ""
    if re.search(r"\b\w+:\w+Member\b", q):
        return True
    return bool(re.fullmatch(r"[\w:.\-]+", q.strip()) and ":" in q and len(q) < 80)


def is_placeholder_period(period: object) -> bool:
    """True for prompt-skeleton echoes like "YYYY" or "YYYYQn" instead of a real period.

    A real period label always carries a year digit; "unknown" stays allowed because the
    orchestrator uses it for candidates whose period could not be determined.
    """
    label = str(period or "").strip()
    if not label or label.lower() == "unknown":
        return False
    return not any(ch.isdigit() for ch in label)


def filter_revenue_candidates(
    candidates: list[dict[str, Any]],
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: list[str] | None = None,
    source_text: str | None = None,
    peer_names: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (kept, dropped) with drop reasons.

    Hard rules:
    - Drop XBRL taxonomy noise quotes
    - Drop value==0 unless quote clearly supports zero for the product
    - Company-total language must be scoped Company total (else reclassify or drop if claiming product)
    - Drop other-brand-only quotes
    - Product-family / formulation scopes require product mention in quote
    - Optional: quote must be verbatim in source_text
    """
    from app.llm.grounding import quote_is_verbatim

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    for cand in candidates:
        quote = (cand.get("source_quote") or "").strip()
        scope = (cand.get("revenue_scope") or "Unknown").strip()
        value = cand.get("value_reported")

        if not quote:
            dropped.append({**cand, "_drop_reason": "missing_quote"})
            continue

        if source_text is not None and not quote_is_verbatim(quote, source_text, min_len=1):
            dropped.append({**cand, "_drop_reason": "quote_not_verbatim"})
            continue

        if is_xbrl_noise_quote(quote):
            dropped.append({**cand, "_drop_reason": "xbrl_taxonomy_noise"})
            continue

        if is_placeholder_period(cand.get("period")):
            dropped.append({**cand, "_drop_reason": "placeholder_period"})
            continue

        if cand.get("is_company_total") is True:
            dropped.append({**cand, "_drop_reason": "model_marked_company_total"})
            continue

        mentions_product = quote_mentions_product(quote, product, generic, extra_aliases=extra_aliases)
        if cand.get("product_mentioned_in_quote") is False and not mentions_product:
            dropped.append({**cand, "_drop_reason": "model_product_not_in_quote"})
            continue

        if quote_mentions_generic_only(quote, product, generic, extra_aliases=extra_aliases):
            dropped.append({**cand, "_drop_reason": "generic_only_not_brand"})
            continue

        other = quote_mentions_other_brand(
            quote, product, generic, extra_aliases=extra_aliases, peer_names=peer_names
        )

        if other and not mentions_product:
            dropped.append({**cand, "_drop_reason": f"other_brand:{other}"})
            continue

        if TOTAL_REVENUE_RE.search(quote) and not mentions_product:
            dropped.append({**cand, "_drop_reason": "company_total_not_product"})
            continue

        product_scopes = {
            "Product family",
            "Formulation-specific",
            "Franchise",
            "U.S.",
            "ex-U.S.",
            "Worldwide",
            "International",
            "Regional",
            "Unknown",
        }
        if scope in product_scopes and not mentions_product:
            dropped.append({**cand, "_drop_reason": "product_scope_without_product_in_quote"})
            continue

        if scope == "Company total" and not mentions_product:
            dropped.append({**cand, "_drop_reason": "company_total_not_product"})
            continue

        if value is None:
            dropped.append({**cand, "_drop_reason": "missing_value"})
            continue

        try:
            num = float(value)
        except (TypeError, ValueError):
            dropped.append({**cand, "_drop_reason": "non_numeric_value"})
            continue

        if value_is_dosage_not_revenue(quote, num):
            dropped.append({**cand, "_drop_reason": "dose_not_revenue"})
            continue

        if num == 0.0 and (
            not mentions_product
            or not re.search(r"\b(zero|nil|no\s+sales|\$0)\b", quote, re.IGNORECASE)
        ):
            dropped.append({**cand, "_drop_reason": "zero_value_unsupported"})
            continue

        kept.append(cand)

    return kept, dropped
