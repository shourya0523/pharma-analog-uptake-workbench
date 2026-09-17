from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from app.parsing.fda_label import brand_name_paths, openfda_block, read_path
from app.quality.profile import blends_sibling_brand

# An alias shorter than this is a fragment - an abbreviation of a row label, a
# two-letter formulation code - and matched against a registry brand it selects
# whatever happens to contain it. The product's own name is never held to this
# floor: it is the name we were asked about, and a short one is still the
# question.
MIN_ALIAS_LENGTH = 4


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().casefold()


def _contains_word(haystack: str, needle: str) -> bool:
    """True when one normalised name appears inside another on word boundaries."""
    if not haystack or not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def openfda_brand_names(result: dict[str, Any]) -> list[str]:
    """Every brand this application is marketed under, wherever it states them.

    An older or discontinued application comes back with no ``openfda`` block
    at all and its brands only in ``products[].brand_name``, so both paths are
    read and the union returned rather than one path being assumed. Which
    paths those are is the record reader's answer, not a second copy of it.
    """
    names: list[str] = []
    for path in brand_name_paths():
        for name in read_path(result, path):
            if name and name not in names:
                names.append(name)
    return names


def molecule_names(result: dict[str, Any], generic: str | None = None) -> set[str]:
    """The molecule spellings this record itself declares, plus the job's own.

    A record's ``openfda`` block names its molecule twice, as ``generic_name``
    and as ``substance_name``, and either may be spelled differently from the
    name the job was uploaded with. A record with no ``openfda`` block declares
    neither, which is why the job's own generic is still in the set.
    """
    block = openfda_block(result)
    names = [*block.get("generic_name", []), *block.get("substance_name", [])]
    if generic:
        names.append(generic)
    return {norm for norm in (_normalize(name) for name in names) if norm}


def names_the_molecule(name: str, molecules: set[str]) -> bool:
    """True when a name is the molecule wearing a variant spelling, not a brand.

    Given a record declaring molecule ``acme``: ``acme``, ``Acme phosphate``
    and ``acme extended-release`` all name the molecule; ``Calderon`` does not.
    Containment runs both ways because the variant can be on either side.
    """
    norm = _normalize(name)
    if not norm:
        return False
    return any(_contains_word(norm, mol) or _contains_word(mol, norm) for mol in molecules)


def _candidates(product: str, aliases: Iterable[str] | None) -> list[str]:
    """The names that may select an application, the product's own first.

    The product's own name is always a candidate. `MIN_ALIAS_LENGTH` applies to
    the aliases only: held against the product's own name it drops a short
    brand and leaves its sibling line extension as the only way to select an
    application - a job for `Duo` keeping only `Duo XR`, which is the harm this
    match exists to prevent, inverted.

    An alias the product name *extends* is dropped: for a job named `Nebulized
    Calderon`, the alias `Calderon` can only ever select the more general
    product's application, which is the whole of the harm this match is
    guarding against. An alias that extends the product name is kept, because
    that is the same product under a fuller SKU name.
    """
    product_norm = _normalize(product)
    candidates: list[str] = [product_norm] if product_norm else []
    for name in aliases or []:
        norm = _normalize(name)
        if not norm or len(norm) < MIN_ALIAS_LENGTH:
            continue
        if norm != product_norm and _contains_word(product_norm, norm):
            continue
        if norm not in candidates:
            candidates.append(norm)
    return candidates


def brand_matched_results(
    results: list[dict[str, Any]],
    *,
    product: str,
    generic: str | None = None,
    aliases: Iterable[str] | None = None,
) -> list[tuple[dict[str, Any], str]]:
    """Every drugsFDA application that is this product, in the order returned.

    A search on brand OR generic name returns every application sharing the
    molecule, so a result is accepted only on a brand match. A name that is the
    molecule under any spelling the record declares is refused on both sides -
    it is shared with competitor and ANDA products, and one molecule variant
    surviving into the candidate list is how a sibling's application gets
    selected.

    Exact brand matches win outright. Where none matches exactly, a record
    whose brand *extends* the requested one is accepted - `Calderon` against a
    registry brand `Calderon Extended-Release` is the same product under its
    full SKU name - but never one that *truncates* it: `Nebulized Calderon`
    against `Calderon` is a different, more general product with its own
    application. An extension that a stored alias says is a sibling brand is
    refused too.

    Returns the exact matches if there are any, otherwise the extensions -
    never a mixture, so a caller taking the earliest approval across the list
    is looking at one kind of match.
    """
    candidates = _candidates(product, aliases)
    if not candidates:
        return []
    alias_list = list(aliases or [])

    exact: list[tuple[dict[str, Any], str]] = []
    extending: list[tuple[dict[str, Any], str]] = []
    for result in results:
        molecules = molecule_names(result, generic)
        hit_exact: tuple[dict[str, Any], str] | None = None
        hit_extending: tuple[dict[str, Any], str] | None = None
        for brand in openfda_brand_names(result):
            if names_the_molecule(brand, molecules):
                continue  # an ANDA or a listing marketed under the molecule name
            brand_norm = _normalize(brand)
            for candidate in candidates:
                if names_the_molecule(candidate, molecules):
                    continue
                if brand_norm == candidate:
                    hit_exact = hit_exact or (result, brand)
                elif (
                    hit_extending is None
                    and _contains_word(brand_norm, candidate)
                    and not blends_sibling_brand(brand, product=product, aliases=alias_list)
                ):
                    hit_extending = (result, brand)
        if hit_exact:
            exact.append(hit_exact)
        elif hit_extending:
            extending.append(hit_extending)
    return exact or extending


def parse_openfda_date(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    try:
        datetime.fromisoformat(s)
        return s[:10]
    except ValueError:
        return None


def earliest_approval_date(results: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Return (iso_date, source_field) from OpenFDA drugsFDA submissions.

    Prefers earliest ORIG submission with AP status; falls back to earliest AP date.
    """
    dates: list[tuple[str, str]] = []
    for result in results:
        for sub in result.get("submissions") or []:
            status = (sub.get("submission_status") or "").upper()
            if status and status != "AP":
                continue
            parsed = parse_openfda_date(sub.get("submission_status_date"))
            if not parsed:
                continue
            stype = (sub.get("submission_type") or "").upper()
            field = f"submissions[type={stype or 'UNK'}].submission_status_date"
            dates.append((parsed, field if stype == "ORIG" else f"fallback:{field}"))

    orig = [(d, f) for d, f in dates if not f.startswith("fallback:")]
    pool = orig or [(d, f.removeprefix("fallback:")) for d, f in dates]
    if not pool:
        return None, None
    pool.sort(key=lambda x: x[0])
    return pool[0]


def earliest_approved_match(
    matches: list[tuple[dict[str, Any], str]],
) -> tuple[dict[str, Any] | None, str | None]:
    """The matching application the product launched on, and the brand it matched.

    Not the first returned: openFDA documents no result order, so reading a
    product's route and dosage form from the first match reads them from an
    arbitrary application - and for a brand whose later application is a line
    extension by another route, from the wrong one. The earliest approved
    application is the product as it launched, and it is the one the approval
    date already comes from, so the fields and the date describe one
    application rather than two.

    Falls back to the first match when no match states an approval date, and
    returns (None, None) for an empty list.
    """
    if not matches:
        return None, None
    dated = [
        (approval, result, brand)
        for result, brand in matches
        if (approval := earliest_approval_date([result])[0])
    ]
    if not dated:
        return matches[0]
    approval, result, brand = min(dated, key=lambda item: item[0])
    return result, brand
