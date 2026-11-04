"""What a brand's line meant on a given date, from the FDA's approval history.

A question can name one form of a brand - "Nebulized Calderon" - while a filer
prints a line for the bare brand. Before a second form of the brand was
approved, that line can only have been the first form; after, it may be
either. Drugs@FDA lists every application under a brand with its dosage form,
route and original approval date, so which forms existed on a date is read,
not remembered.

`aliases(product, period)` returns the other names a filer's line for that
period could carry and still be exactly the product. Nothing is stored.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

import httpx

DRUGSFDA_URL = "https://api.fda.gov/drug/drugsfda.json"

DESCRIPTOR_PROMPT = """\
Does the product descriptor "{descriptor}" describe a drug product whose dosage
form is "{form}" and route is "{route}"? For example "nebulized" describes an
inhalation solution, "dry powder" an inhalation powder, "extended-release" an
extended-release tablet or capsule.

Answer with one JSON object and nothing else:
{{"matches": true or false, "reason": "<one sentence>"}}
"""


@dataclass(frozen=True)
class Application:
    """One FDA application under a brand."""

    number: str
    brand: str
    dosage_form: str
    route: str
    approved: dt.date


@lru_cache(maxsize=None)
def fda_applications(brand: str) -> tuple[Application, ...]:
    """Every approved application whose brand name starts with `brand`."""
    response = httpx.get(DRUGSFDA_URL, params={"search": f'openfda.brand_name:"{brand}"', "limit": 50}, timeout=60)
    if response.status_code == 404:  # openFDA answers "no matches" with 404
        return ()
    response.raise_for_status()
    found = []
    for result in response.json().get("results", []):
        original = [s for s in result.get("submissions", [])
                    if s.get("submission_type") == "ORIG" and s.get("submission_status") == "AP"]
        if not original:
            continue
        approved = dt.datetime.strptime(original[0]["submission_status_date"], "%Y%m%d").date()
        for product in result.get("products", []):
            name = str(product.get("brand_name") or "")
            if name.lower().startswith(brand.lower()):
                found.append(Application(result["application_number"], name, str(product.get("dosage_form") or ""),
                                         str(product.get("route") or ""), approved))
                break
    return tuple(sorted(set(found), key=lambda a: a.approved))


def split_brand(product: str, lookup: Callable = fda_applications) -> tuple[str, str] | None:
    """`(brand, descriptor)` where `brand` is the longest run of the product's
    words that FDA lists as a brand and `descriptor` the words left over.
    `Nebulized Calderon` -> ("Calderon", "Nebulized"); a name FDA lists whole
    has no descriptor."""
    words = product.split()
    for size in range(len(words), 0, -1):
        for start in range(len(words) - size + 1):
            brand = " ".join(words[start:start + size])
            if lookup(brand):
                descriptor = " ".join(words[:start] + words[start + size:])
                return brand, descriptor
    return None


def gemini_describes(descriptor: str, form: str, route: str, *, client: Any = None) -> bool:
    """Ask Gemini, without search, whether the descriptor names that form."""
    from app.retrieval.gemini_search import make_client, parse_json_answer

    client = client or make_client()
    model = os.environ.get("GEMINI_READER_MODEL") or "gemini-3.8-flash"
    reply = client.interactions.create(model=model, input=DESCRIPTOR_PROMPT.format(
        descriptor=descriptor, form=form, route=route))
    data = reply.model_dump(mode="json") if hasattr(reply, "model_dump") else reply
    return bool((parse_json_answer(data.get("output_text") or "") or {}).get("matches"))


def aliases(
    product: str,
    period_end: dt.date,
    *,
    lookup: Callable = fda_applications,
    describes: Callable = gemini_describes,
) -> list[dict[str, Any]]:
    """Names a filer's line for the period could carry and still be exactly
    `product`, each with the evidence: the bare brand, when the product is
    one form of it and that form was the brand's only approved form by
    `period_end`."""
    split = split_brand(product, lookup)
    if not split:
        return []
    brand, descriptor = split
    if not descriptor:
        return []
    on_market = [a for a in lookup(brand) if a.approved <= period_end]
    distinct = {(a.dosage_form.lower(), a.route.lower()) for a in on_market}
    if len(distinct) != 1:
        return []
    application = on_market[0]
    if not describes(descriptor, application.dosage_form, application.route):
        return []
    return [{"name": brand, "evidence": f"{application.number} {application.brand} "
                                        f"({application.dosage_form}, {application.route}) approved "
                                        f"{application.approved}; the only form of {brand} approved by {period_end}"}]


def first_found(read: Callable, product: str, period: str, period_end: dt.date, **kwargs) -> Any:
    """Run `read(product, period)`; if it finds nothing, run it on each alias
    of the product for that period. The answer carries `identity` when an
    alias produced it."""
    answer = read(product, period)
    if getattr(answer, "status", None) == "found":
        return answer
    for alias in aliases(product, period_end, **kwargs):
        alt = read(alias["name"], period)
        if getattr(alt, "status", None) == "found":
            alt.identity = alias
            return alt
    return answer

