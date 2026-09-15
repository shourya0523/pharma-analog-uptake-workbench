"""How a derived analog profile is compared with an independently sourced one.

The pipeline and the answer keys name these attributes the same way, which is
not the same as meaning the same thing by them. A route arrives from a drug
label in the FDA's own spelling and from a curated file in a person's; an era
is a computed five-year bucket on one side and a written span with an
open-ended first bucket on the other; a mechanism class is a grouping key whose
wording is arbitrary and whose *grouping* is the whole point.

So the comparison is declared here, once, rather than guessed by each reader:
every attribute says how it is compared and every normalisation says what it is
a snapshot of. An attribute compared the wrong way produces a number that looks
like a score and measures spelling.

Nothing here belongs in the pipeline. This reads the answer key, which is what
a scorer is for and what the thing being scored must never do.
"""

from __future__ import annotations

import itertools
import re

# An attribute whose wording is fixed on both sides: compare the values.
EXACT = "exact"
# A year, wherever in the string it sits.
YEAR = "year"
# A five-year bucket against a written span, which may be open-ended.
ERA = "era"
# An open vocabulary. The key's wording is arbitrary and comparing it would
# measure whether two writers picked the same word; what the key is for is
# which products it puts together, so that is what is compared.
PARTITION = "partition"
# Free prose. Reported so a reader can see it, never scored for equality.
PROSE = "prose"

COMPARISON = {
    "moa": PROSE,
    "moa_class": PARTITION,
    "route_of_administration": EXACT,
    "first_approval_year": YEAR,
    "approval_era": ERA,
    "indication_area": PARTITION,
    "competitive_intensity_at_launch": EXACT,
    "marketed_peers_at_launch": EXACT,
    "peer_universe_role": EXACT,
}

# Route arrives from a label in the FDA's Structured Product Labeling route
# vocabulary, and from a curated file in ordinary words. This is a snapshot of
# where those two disagree on the same route, taken from the SPL terms the
# label endpoint returns; a route spelled the same on both sides needs no entry
# and is compared directly. It goes stale when the FDA adds a term whose
# ordinary-language name is not its own title-cased spelling.
_SPL_ROUTE_SYNONYMS = {
    "respiratory inhalation": "inhaled",
    "inhalation": "inhaled",
    "oral": "oral",
    "intravenous": "intravenous",
    "subcutaneous": "subcutaneous",
    "intramuscular": "intramuscular",
}

# Routes a curated file may record at the level of "not taken by mouth", which
# any of these specific routes satisfies. Wider than the SPL term it is
# compared against, so it is an accepted reading rather than a disagreement.
_PARENTERAL = frozenset({"intravenous", "subcutaneous", "intramuscular"})


def _flat(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _route_key(value: object) -> str:
    flat = _flat(value)
    return _SPL_ROUTE_SYNONYMS.get(flat, flat)


def routes_agree(expected: object, observed: object) -> bool:
    """Whether two spellings of a route are the same route."""
    left, right = _route_key(expected), _route_key(observed)
    if not left or not right:
        return False
    if left == right:
        return True
    return (left == "parenteral" and right in _PARENTERAL) or (
        right == "parenteral" and left in _PARENTERAL
    )


def _era_bounds(era: str) -> tuple[int, int] | None:
    """The years a written era covers, honouring an open first or last bucket.

    ``Pre-2000`` is everything before 2000 and ``2025+`` everything from 2025;
    a plain ``2010-2014`` is itself.
    """
    text = str(era or "").strip()
    if not text:
        return None
    if text.casefold().startswith("pre-"):
        head = re.search(r"\d{4}", text)
        return (0, int(head.group()) - 1) if head else None
    if text.endswith("+"):
        head = re.search(r"\d{4}", text)
        return (int(head.group()), 9999) if head else None
    years = re.findall(r"\d{4}", text)
    if len(years) == 2:
        return int(years[0]), int(years[1])
    return None


def eras_agree(expected: object, observed: object) -> bool:
    """Whether a computed bucket falls inside the era that was expected.

    The pipeline computes a closed five-year bucket for every year, including
    years older than any bucket a curated file bothered to name. So the test is
    containment rather than equality: a bucket inside ``Pre-2000`` is the era
    ``Pre-2000`` describes, spelled at finer grain.
    """
    want = _era_bounds(str(expected or ""))
    got = _era_bounds(str(observed or ""))
    if not want or not got:
        return False
    return want[0] <= got[0] and got[1] <= want[1]


def years_agree(expected: object, observed: object) -> bool:
    left = re.search(r"\d{4}", str(expected or ""))
    right = re.search(r"\d{4}", str(observed or ""))
    return bool(left and right and left.group() == right.group())


def values_agree(attribute: str, expected: object, observed: object) -> bool | None:
    """Whether one attribute agrees, or None when it is not scored for equality.

    An answer key that expects nothing is expecting a refusal, not excusing the
    attribute: a product approved as an injection and as a capsule has no single
    route, and a derivation that names one has got it wrong in the way that
    matters most. So two absences agree, and one absence does not.

    A partition attribute returns None here: it cannot be judged one product at
    a time, because what it claims is about pairs of products.
    """
    how = COMPARISON.get(attribute, EXACT)
    if how in (PROSE, PARTITION):
        return None
    want_absent = expected in (None, "")
    got_absent = observed in (None, "")
    if want_absent or got_absent:
        return want_absent and got_absent
    if how == YEAR:
        return years_agree(expected, observed)
    if how == ERA:
        return eras_agree(expected, observed)
    if attribute == "route_of_administration":
        return routes_agree(expected, observed)
    return _flat(expected) == _flat(observed)


def partition_agreement(
    expected: dict[str, object], observed: dict[str, object]
) -> dict[str, object]:
    """How well an open-vocabulary key groups the products the answer key groups.

    Both sides name their groups in their own words, so the comparison is over
    pairs of products: for every pair both sides resolved, does the answer key
    put the two together, and does the derived key agree? That is the property
    the key exists for - `score_analog` reads it as an equality - and it cannot
    be passed by copying the answer key's wording.

    ``split`` counts pairs the answer key groups and the derivation separates,
    which costs a real analog. ``merged`` counts the reverse, which invents one.
    """
    shared = sorted(
        name
        for name in set(expected) & set(observed)
        if expected.get(name) not in (None, "") and observed.get(name) not in (None, "")
    )
    agreed = split = merged = 0
    for left, right in itertools.combinations(shared, 2):
        want = _flat(expected[left]) == _flat(expected[right])
        got = _flat(observed[left]) == _flat(observed[right])
        if want == got:
            agreed += 1
        elif want:
            split += 1
        else:
            merged += 1
    pairs = agreed + split + merged
    return {
        "products_compared": len(shared),
        "pairs": pairs,
        "agreed": agreed,
        "split": split,
        "merged": merged,
        "agreement": round(agreed / pairs, 4) if pairs else None,
    }
