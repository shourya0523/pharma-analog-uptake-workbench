"""Derive a product's analog attributes from what the pipeline extracted.

`analog_matching` compares products on mechanism class, route, approval era and
how crowded the indication was at launch. Those four - and the identifying
fields that travel with them - were curated reference data for the products
this repo already tracks, and no procedure produced them for a product it had
never seen.

This module is that procedure. It reads the profile fields the extraction stage
stored for a product and the catalogue the pipeline has built from its own
runs, and answers from those or refuses. It deliberately does not read the
curated attribute columns: those are also what the answer key is built from, so
a derivation that consulted them would be scored against its own input.

Refusal is load-bearing. An attribute this cannot ground is None, and
`score_analog` drops a None from the denominator rather than counting it as
agreement, so an incomplete profile ranks below a complete one instead of
beating it on a coincidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date

from app.analytics.competitive_intensity import FORMULA_VERSION as INTENSITY_FORMULA
from app.analytics.competitive_intensity import (
    CompetitivePeer,
    calculate_competitive_snapshot,
    categorize_snapshots,
)

# What this module stamps on the attributes it derives, so a consumer can tell
# them from attributes a person curated.
PROVENANCE = "pipeline_derived"

# The label the intensity attribute carries: the rule that produced it, taken
# from the rule itself so the two cannot drift apart.
INTENSITY_BASIS = f"catalog_peer_classification_{INTENSITY_FORMULA}"

DISTINCT_PRODUCT = "distinct_product"
FORMULATION_OF = "formulation_of:"

# Approval eras are five-year buckets so that "the commercial environment this
# launched into" is a span rather than a year. The span is computed, not
# listed: a bucket for a year no product has reached yet needs no new entry.
ERA_SPAN_YEARS = 5


def era_for_year(year: int) -> str:
    """The five-year bucket a year falls in, as ``start-end``.

    A product approved in the first year of a bucket and one approved in its
    last are the same era here; `score_analog` gives adjacent buckets partial
    credit, so the boundary is a soft one.
    """
    start = year - (year % ERA_SPAN_YEARS)
    return f"{start}-{start + ERA_SPAN_YEARS - 1}"


def year_of(value: object) -> int | None:
    """The four-digit year in a stored approval date, or None.

    The date arrives as whatever the source spelled - an ISO date from a
    structured field, a year on its own from prose - so this takes the year and
    ignores the rest rather than requiring one spelling.
    """
    match = re.search(r"(1[89]\d{2}|2\d{3})", str(value or ""))
    return int(match.group(1)) if match else None


def route_of_administration(route_terms: list[str] | None) -> str | None:
    """The single route the label states, spelled as a name rather than a code.

    A label listing several routes has not stated one route, and an attribute
    compared for equality cannot be a list, so it is left unresolved instead of
    collapsed to whichever happened to be first. Products whose route genuinely
    differs by presentation are compared on their other attributes.
    """
    terms = {str(term).strip() for term in (route_terms or []) if str(term).strip()}
    if len(terms) != 1:
        return None
    return terms.pop().title()


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def peer_universe_role(drug_name: str, catalogue_names: list[str]) -> str:
    """``formulation_of:<product>`` when this name is another product's, extended.

    Nebulized Calderon and Calderon XR are Calderon's approval recorded a second
    time. Counted as prior approvals they would overstate how crowded the
    indication was, and ranked as analogs they would return the product itself
    wearing a different presentation.
    """
    mine = _key(drug_name)
    if not mine:
        return DISTINCT_PRODUCT
    parents = [
        name
        for name in catalogue_names
        if (other := _key(name))
        and other != mine
        and (
            mine.startswith(other + " ")
            or mine.endswith(" " + other)
            or f" {other} " in f" {mine} "
        )
    ]
    if not parents:
        return DISTINCT_PRODUCT
    # The longest parent name is the most specific reading: a product extending
    # "Calderon XR" is that product's formulation, not the base Calderon's.
    return FORMULATION_OF + max(parents, key=lambda name: len(_key(name)))


@dataclass(frozen=True)
class AnalogProfile:
    """One product's analog attributes, in the shape the analytics layer reads."""

    drug_name: str
    moa: str | None = None
    moa_class: str | None = None
    route_of_administration: str | None = None
    first_approval_year: int | None = None
    approval_era: str | None = None
    indication_area: str | None = None
    competitive_intensity_at_launch: str | None = None
    marketed_peers_at_launch: int | None = None
    competitive_intensity_basis: str | None = None
    peer_universe_role: str = DISTINCT_PRODUCT
    attribute_provenance: str = PROVENANCE
    unresolved: tuple[str, ...] = field(default_factory=tuple)

    def as_row(self) -> dict[str, object]:
        """The attributes as a plain row, for callers that read profiles as dicts."""
        return {
            "drug_name": self.drug_name,
            "moa": self.moa,
            "moa_class": self.moa_class,
            "route_of_administration": self.route_of_administration,
            "first_approval_year": self.first_approval_year,
            "approval_era": self.approval_era,
            "indication_area": self.indication_area,
            "competitive_intensity_at_launch": self.competitive_intensity_at_launch,
            "marketed_peers_at_launch": self.marketed_peers_at_launch,
            "competitive_intensity_basis": self.competitive_intensity_basis,
            "peer_universe_role": self.peer_universe_role,
            "attribute_provenance": self.attribute_provenance,
        }


# What the attributes say about where they came from, rather than what the
# product is. These are always answered, so they are never what `unresolved` is
# reporting and never what an answer key is compared against - a key curated by
# a person and a row derived here disagree on them by definition, and should.
PROVENANCE_FIELDS = frozenset(
    {"drug_name", "attribute_provenance", "competitive_intensity_basis", "unresolved"}
)


def _reportable() -> tuple[str, ...]:
    """The attributes `unresolved` speaks for: every one that says what the
    product is.

    Taken from the dataclass rather than listed beside it, because a list
    written here is one a field added above would not appear in - and the
    attribute would then be missing from the profile with nothing saying so,
    which is the single failure `unresolved` exists to prevent.
    """
    return tuple(
        name for name in AnalogProfile.__dataclass_fields__ if name not in PROVENANCE_FIELDS
    )


def _classified(target: dict[str, object], peer: dict[str, object]) -> str:
    """How directly a peer competes, from what the two products share.

    Same mechanism class and same route is the most direct competition there
    is; the same class by another route reaches some of the same patients;
    anything else is a treatment alternative rather than a rival for the same
    prescription.
    """
    same_class = bool(
        target.get("moa_class")
        and target.get("moa_class") == peer.get("moa_class")
    )
    same_route = bool(
        target.get("route_of_administration")
        and target.get("route_of_administration") == peer.get("route_of_administration")
    )
    if same_class and same_route:
        return "direct"
    if same_class:
        return "substitutable"
    return "indirect"


def peers_marketed_at_launch(
    target: dict[str, object], catalogue: list[dict[str, object]]
) -> list[dict[str, object]]:
    """The products already approved in this indication when the target launched.

    A formulation of a product already on the roster is that product's approval
    recorded again, so it is not a second competitor.
    """
    year = target.get("first_approval_year")
    area = target.get("indication_area")
    if year is None or not area:
        return []
    return [
        row
        for row in catalogue
        if row.get("drug_name") != target.get("drug_name")
        and row.get("indication_area") == area
        and row.get("first_approval_year") is not None
        and int(row["first_approval_year"]) < int(year)
        and str(row.get("peer_universe_role") or DISTINCT_PRODUCT) == DISTINCT_PRODUCT
    ]


def _cohort_snapshots(rows: list[dict[str, object]]) -> list:
    """One snapshot per product in a single indication's cohort.

    Every product in the cohort is measured against the same roster rule, so the
    percentile `categorize_snapshots` puts it at is a comparison with the other
    launches into that indication rather than with a threshold picked in advance.
    """
    by_name = {str(row.get("drug_name")): row for row in rows}
    snapshots = []
    for name, row in sorted(by_name.items()):
        peers = peers_marketed_at_launch(row, list(by_name.values()))
        snapshots.append(
            calculate_competitive_snapshot(
                indication_id=name,
                launch_date=date(int(row["first_approval_year"]), 1, 1),
                geography="worldwide",
                peers=[
                    CompetitivePeer(
                        id=str(peer.get("drug_name")),
                        classification=_classified(row, peer),
                        launch_or_expected_date=date(int(peer["first_approval_year"]), 1, 1),
                        same_moa=bool(
                            row.get("moa_class")
                            and row.get("moa_class") == peer.get("moa_class")
                        ),
                        same_route=bool(
                            row.get("route_of_administration")
                            and row.get("route_of_administration")
                            == peer.get("route_of_administration")
                        ),
                    )
                    for peer in peers
                ],
            )
        )
    return snapshots


def intensity_by_product(
    catalogue: list[dict[str, object]],
) -> dict[str, tuple[str | None, int | None]]:
    """The label and peer count for every product, each judged within its own area.

    A cohort answers for all of its members at once, so deriving a whole
    catalogue costs one pass per indication rather than one per product. Asking
    per product repeats every cohort once for each of its members, which on a
    catalogue concentrated in one indication - which is what an analog workbench
    accumulates - is the difference between a page that loads and one that does
    not.
    """
    by_area: dict[str, list[dict[str, object]]] = {}
    for row in catalogue:
        area = row.get("indication_area")
        if not area or row.get("first_approval_year") is None:
            continue
        by_area.setdefault(str(area), []).append(row)

    found: dict[str, tuple[str | None, int | None]] = {}
    for rows in by_area.values():
        for item in categorize_snapshots(_cohort_snapshots(rows)):
            found[item.indication_id] = (item.category, len(item.peer_ids))
    return found


def competitive_intensity(
    target: dict[str, object], catalogue: list[dict[str, object]]
) -> tuple[str | None, int | None]:
    """The label and the peer count this one product faced.

    The cohort is every product the catalogue holds for the same indication, so
    the label says how this launch compared with the others we can see. A
    catalogue that holds no indication for the product cannot place it, and says
    so with None rather than with the label an empty roster would produce -
    which would read as "launched into an open market" for a product we simply
    know nothing about.

    The judgement itself is `intensity_by_product`, so the rule exists once and
    a caller deriving one product and a caller deriving a catalogue cannot get
    different answers for the same product.
    """
    if target.get("first_approval_year") is None or not target.get("indication_area"):
        return None, None

    name = str(target.get("drug_name"))
    cohort = {
        str(row.get("drug_name")): row
        for row in catalogue
        if row.get("indication_area") == target.get("indication_area")
    }
    cohort[name] = target
    return intensity_by_product(list(cohort.values())).get(name, (None, None))


def derive_analog_profile(
    *,
    drug_name: str,
    fields: dict[str, object],
    route_terms: list[str] | None = None,
    catalogue: list[dict[str, object]] | None = None,
    intensity: tuple[str | None, int | None] | None = None,
) -> AnalogProfile:
    """Build a product's analog attributes from its extracted profile fields.

    ``fields`` is what the extraction stage stored, keyed by field name;
    ``route_terms`` the routes the label listed, which carry the plural that a
    single stored string has already lost. ``catalogue`` is the analog rows of
    the other products the pipeline holds, and is what the intensity attribute
    is judged within - without it a product has no roster and the attribute is
    refused rather than assumed.
    """
    rows = list(catalogue or [])
    names = [str(row.get("drug_name")) for row in rows if row.get("drug_name")]

    year = year_of(fields.get("fda_approval_date"))
    route = route_of_administration(
        route_terms
        if route_terms is not None
        else [part for part in str(fields.get("roa") or "").split(";") if part.strip()]
    )

    target = {
        "drug_name": drug_name,
        "moa": (str(fields["moa"]) if fields.get("moa") else None),
        "moa_class": (str(fields["moa_class"]) if fields.get("moa_class") else None),
        "route_of_administration": route,
        "first_approval_year": year,
        "approval_era": era_for_year(year) if year is not None else None,
        "indication_area": (
            str(fields["indication_area"]) if fields.get("indication_area") else None
        ),
        "peer_universe_role": peer_universe_role(drug_name, names),
    }

    label, peer_count = (
        intensity if intensity is not None else competitive_intensity(target, rows)
    )
    profile = AnalogProfile(
        drug_name=drug_name,
        moa=target["moa"],
        moa_class=target["moa_class"],
        route_of_administration=route,
        first_approval_year=year,
        approval_era=target["approval_era"],
        indication_area=target["indication_area"],
        competitive_intensity_at_launch=label,
        marketed_peers_at_launch=peer_count,
        competitive_intensity_basis=INTENSITY_BASIS if label else None,
        peer_universe_role=str(target["peer_universe_role"]),
    )
    return replace(
        profile,
        unresolved=tuple(
            name for name in _reportable() if getattr(profile, name) in (None, "")
        ),
    )


# What the classifier says when the evidence does not support a key. Kept as a
# refusal rather than mapped to None at the call site, so a refused key is
# distinguishable in the log from a call that never happened.
INCONCLUSIVE = "inconclusive"

_SNAKE_CASE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


def read_classification(
    response: dict[str, object], *, product: str, epc: str | None = None
) -> tuple[str | None, str | None]:
    """The two grouping keys a classifier response supports, or None for each.

    Each key is checked against the rule the prompt states, because a model
    that has broken one of them has not produced the attribute asked for:

    - a moa_class must be a lower_snake_case group key, so `vessel_peptide_pathway`
      is one and `Vessel Peptide Pathway` is not;
    - it must name a group rather than this product, so `calderon_pathway` is
      refused for Calderon however plausible the rest of the answer reads;
    - it must not be the Established Pharmacologic Class term relabelled, which
      is the substitution the prompt exists to prevent.
    """
    moa_class = str(response.get("moa_class") or "").strip()
    area = str(response.get("indication_area") or "").strip()

    refused = (
        moa_class.casefold() in ("", INCONCLUSIVE)
        or not _SNAKE_CASE.match(moa_class)
        or bool(set(_key(moa_class).split()) & set(_key(product).split()))
        or bool(epc and _key(moa_class.replace("_", " ")) == _key(epc))
    )
    if refused:
        moa_class = ""
    if area.casefold() in ("", INCONCLUSIVE):
        area = ""

    return (moa_class or None, area or None)
