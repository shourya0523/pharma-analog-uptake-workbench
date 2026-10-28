"""Read a product's revenue from a filer that sells nothing else.

A filer with one product rarely tags it by name: its product revenue line is
the product. The line is read from the filing's XBRL, and accepted only when
the filing's own text, read by a model and checked quote by quote, names no
other product the filer sold. A filer that tags no products but sells several
has the same XBRL shape, so the text is what tells them apart.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from lxml import html as lxml_html

from app.retrieval.xbrl import (
    Fact,
    XbrlAnswer,
    _GEOGRAPHY_AXIS,
    is_revenue,
    spans,
)

# A revenue line the filer earns other than by selling product. Its presence
# beside an untagged total means the total is not product sales.
_OTHER_REVENUE = re.compile(r"collaborat|licens|royalt|milestone|alliance|grant|service|contract(?!withcustomer)",
                            re.IGNORECASE)
# Concepts that name product sales as such, in us-gaap or filer extensions.
_PRODUCT_CONCEPT = re.compile(r"goods|productsales|productrevenue|netproduct", re.IGNORECASE)

# Sentences that discuss product sales, for the model to read. A filing runs
# to hundreds of thousands of characters; these are the passages that say what
# was sold.
_SALES_SENTENCE = re.compile(r"product (?:revenue|sales)|net sales|net revenue|sales of", re.IGNORECASE)
MAX_PASSAGE_CHARS = 60_000

CONFIRM_PROMPT = """\
Below are passages from a company's quarterly or annual report.

List every product the company itself recorded net product sales (product
revenue) from in the period the report covers. Do not list a product that only
earns the company royalties, collaboration, licence or milestone revenue, a
product candidate that is not yet sold, or a competitor's product.

Answer with one JSON object and nothing else:
{{"products": [{{"name": "<product as the report names it>",
                "quote": "<a sentence copied verbatim from the passages showing the company sold it>"}}]}}

Passages:
{passages}
"""


@dataclass
class Confirmation:
    """What the filing's text says the filer sold."""

    single: bool
    products: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


# ---------------------------------------------------------------- the line


def product_line(contexts: dict[str, dict], facts: list[dict], period_end: dt.date, days: range) -> tuple[Fact | None, list[Fact], str]:
    """The filer's product revenue for the span, its parts, and where it came from.

    In order: revenue under the product/service axis's `ProductMember` (product
    sales beside collaboration revenue); an undimensioned concept that names
    product sales; the undimensioned total revenue, when no other kind of
    revenue is tagged in the span. Returns `(line, parts, basis)`, where parts
    are the line split by place.
    """
    in_span = []
    for f in facts:
        ctx = contexts.get(f["context"])
        if ctx and is_revenue(f["concept"], f["unit"]) and spans(ctx["start"], ctx["end"], period_end, days):
            in_span.append(Fact(f["concept"], f["value"], f["unit"], ctx["start"], ctx["end"], ctx["members"],
                                "single_product", f.get("decimals")))

    def members(f: Fact) -> list[str]:
        return [m.split(":")[-1] for m in f.members.values()]

    def parts_of(root: Fact) -> list[Fact]:
        base = set(root.members.items())
        return [
            f for f in in_span
            if f.concept == root.concept and base <= set(f.members.items())
            and len(set(f.members.items()) - base) == 1
            and any(_GEOGRAPHY_AXIS.search(axis) for axis, _ in set(f.members.items()) - base)
        ]

    product_member = [f for f in in_span if members(f) == ["ProductMember"]]
    if product_member:
        line = max(product_member, key=lambda f: (f.concept.startswith("us-gaap:"), f.value))
        return line, parts_of(line), "product_member"

    plain = [f for f in in_span if not f.members]
    named = [f for f in plain if _PRODUCT_CONCEPT.search(f.concept.split(":")[-1])]
    if named:
        line = max(named, key=lambda f: f.value)
        return line, parts_of(line), "product_sales_concept"

    other = any(
        _OTHER_REVENUE.search(f.concept.split(":")[-1]) or any(_OTHER_REVENUE.search(m) for m in members(f))
        for f in in_span
    ) or any(
        # Royalty and collaboration concepts are not product revenue, so they
        # are not in `in_span`; their presence in the span still counts.
        re.search("revenue", f["concept"].split(":")[-1], re.IGNORECASE)
        and _OTHER_REVENUE.search(f["concept"].split(":")[-1])
        and contexts.get(f["context"]) and spans(contexts[f["context"]]["start"], contexts[f["context"]]["end"], period_end, days)
        for f in facts
    )
    if plain and not other:
        line = max(plain, key=lambda f: f.value)
        return line, parts_of(line), "total_revenue"
    return None, [], "no_product_line"


# ---------------------------------------------------------------- the text


def filing_text(document: bytes) -> str:
    """The filing's visible text, whitespace collapsed."""
    try:
        text = lxml_html.fromstring(document).text_content()
    except (ValueError, TypeError):
        text = re.sub(rb"<[^>]+>", b" ", document).decode("utf-8", "ignore")
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def sales_passages(text: str) -> str:
    """The sentences that discuss product sales, up to `MAX_PASSAGE_CHARS`."""
    kept, size, seen = [], 0, set()
    for sentence in re.split(r"(?<=[.;])\s+", text):
        if not _SALES_SENTENCE.search(sentence) or sentence in seen:
            continue
        seen.add(sentence)
        if size + len(sentence) > MAX_PASSAGE_CHARS:
            break
        kept.append(sentence)
        size += len(sentence) + 1
    return "\n".join(kept)


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def names_the_product(name: str, product: str) -> bool:
    """Whether a name the report gives is the product or one of its forms:
    `Calderon cream 1%` is Calderon; `NuVessa` is not."""
    name, alternatives = _normal(name), [_normal(a) for a in re.split(r"\s*/\s*", product)]
    return any(a and (name == a or name.startswith(a + " ") or name.replace(" ", "") == a.replace(" ", ""))
               for a in alternatives)


def judge(product: str, listed: list[dict[str, Any]], passages: str) -> Confirmation:
    """Decide from the model's list. Single only if every listed product is
    this one (or a form of it) and at least one quote for it is in the text."""
    haystack = _normal(passages)
    verified = []
    for item in listed:
        quote = _normal(str(item.get("quote") or ""))
        verified.append({**item, "quote_found": bool(quote) and quote in haystack})
    ours = [v for v in verified if names_the_product(str(v.get("name") or ""), product)]
    others = [v for v in verified if not names_the_product(str(v.get("name") or ""), product)]
    if others:
        return Confirmation(False, verified, "the filing names other products sold: "
                            + ", ".join(str(o.get("name")) for o in others))
    if not any(v["quote_found"] for v in ours):
        return Confirmation(False, verified, "no quote naming the product was found in the filing")
    return Confirmation(True, verified, "the filing names no other product sold")


def gemini_confirm(product: str, passages: str, *, client: Any = None, model: str | None = None) -> Confirmation:
    """Ask Gemini, without search, what the passages say the filer sold."""
    from app.retrieval.gemini_search import make_client, parse_json_answer

    client = client or make_client()
    model = model or os.environ.get("GEMINI_CONFIRM_MODEL") or "gemini-3.8-flash"
    interaction = client.interactions.create(model=model, input=CONFIRM_PROMPT.format(passages=passages))
    data = interaction.model_dump(mode="json") if hasattr(interaction, "model_dump") else interaction
    parsed = parse_json_answer(data.get("output_text") or "") or {}
    listed = parsed.get("products")
    if not isinstance(listed, list):
        return Confirmation(False, [], f"no product list in the reply: {json.dumps(data.get('output_text'))[:200]}")
    return judge(product, listed, passages)


# ---------------------------------------------------------------- reading


def read_single_product(
    answer: XbrlAnswer,
    filing: dict,
    contexts: dict[str, dict],
    facts: list[dict],
    url: str,
    days: range,
    confirm: Callable[[str, str], Confirmation],
) -> XbrlAnswer:
    """Fill `answer` from the issuer's filing if it sells only this product.

    `confirm(product, passages)` returns a `Confirmation`; `gemini_confirm` is
    the one used in practice.
    """
    answer.filer, answer.cik, answer.accession, answer.filing_url = (
        filing["filer"], filing["cik"], filing["accession"], url,
    )
    line, parts, basis = product_line(contexts, facts, dt.date.fromisoformat(filing["period_end"]), days)
    if line is None:
        answer.status = "no_member"
        answer.candidates = [{"basis": basis}]
        return answer
    passages = sales_passages(filing_text(filing.get("primary") or b""))
    verdict = confirm(answer.product, passages)
    answer.candidates = [{"basis": basis, "value": line.value, "concept": line.concept,
                          "confirmation": verdict.reason, "products": verdict.products}]
    if not verdict.single:
        answer.status = "multi_product" if verdict.products else "unconfirmed"
        return answer
    answer.status = "found"
    answer.match = "single_product"
    answer.value, answer.unit, answer.concept, answer.decimals = line.value, line.unit, line.concept, line.decimals
    answer.period_start, answer.period_end = line.start, line.end
    answer.member = basis
    answer.by_geography = {
        next(m for a, m in p.members.items() if _GEOGRAPHY_AXIS.search(a)): p.value for p in parts
    }
    return answer
