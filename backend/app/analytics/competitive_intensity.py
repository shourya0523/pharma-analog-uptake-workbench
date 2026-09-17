"""Score how crowded the market was when a product launched.

The band is a property of the market, not of the batch a product happens to be
scored in: the same peer roster gives the same band whether it is scored alone
or beside a hundred others. Where the registry does not cover a product's
indication universe at all, no band is produced - "we hold no entries for this
disease" and "this product launched into an empty market" are different
answers, and only the second is `low`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

FORMULA_VERSION = "competitive_intensity_v2"
WEIGHTS = {
    "direct": 1.0,
    "indirect": 0.5,
    "substitutable": 0.75,
    "near_term_phase3": 0.25,
}

# The classifications that describe a product already selling on the launch
# date. A phase 3 programme is a peer in waiting - still counted and still
# weighted into `raw_score`, but not a rival the launch competes with, so it
# does not move the band. Derived from WEIGHTS minus that one exclusion, so a
# classification added to WEIGHTS is marketed by default and has to be
# excluded deliberately.
PIPELINE_CLASSIFICATIONS = frozenset({"near_term_phase3"})
MARKETED_CLASSIFICATIONS = frozenset(WEIGHTS) - PIPELINE_CLASSIFICATIONS

# The band is a step function over the marketed peer count, written once so the
# thresholds cannot drift between branches: below the first cut-point is the
# first label, below the second is the second, at or above it is HIGHEST_BAND.
#
# Snapshot of the launch-intensity banding rule this project settled on - 0-1
# marketed peers low, 2-4 medium, 5 or more high - as recorded in
# docs/plans/2026-09-17-006-what-twelve-reviews-found.md, section 4a. It goes
# stale if that rule is restated, or if MARKETED_CLASSIFICATIONS stops meaning
# "already on the market on the launch date".
PEER_COUNT_BANDS: tuple[tuple[int, str], ...] = ((2, "low"), (5, "medium"))
HIGHEST_BAND = "high"

# Set as `not_assessed_reason` when the registry holds no entry at all for the
# product's indication universe. The peer count is then zero because we looked
# nowhere, not because there was nothing to find.
UNCOVERED_UNIVERSE = "indication_universe_not_in_registry"


def band_for_peer_count(count: int) -> str:
    """The band an absolute marketed-peer count falls in."""
    for cut_point, label in PEER_COUNT_BANDS:
        if count < cut_point:
            return label
    return HIGHEST_BAND


@dataclass(frozen=True)
class CompetitivePeer:
    id: str
    classification: str
    launch_or_expected_date: date
    same_moa: bool = False
    same_route: bool = False


@dataclass(frozen=True)
class CompetitiveSnapshot:
    indication_id: str
    launch_date: date
    geography: str
    formula_version: str
    direct_count: int
    indirect_count: int
    substitutable_count: int
    near_term_phase3_count: int
    same_moa_count: int
    same_route_count: int
    marketed_peer_count: int
    raw_score: float
    peer_ids: list[str]
    category: str | None = None
    cohort_size: int = 0
    low_coverage: bool = False
    not_assessed_reason: str | None = None


@dataclass(frozen=True)
class RegistryEntry:
    product_id: str
    disease: str
    lot: str
    setting: str
    geography: str
    approval_or_launch_date: date
    classification: str
    same_moa: bool = False
    same_route: bool = False


def _in_universe(
    entry: RegistryEntry,
    *,
    disease: str,
    lot: str,
    setting: str,
    geography: str,
) -> bool:
    """Whether a registry entry describes the same indication universe.

    The one place the universe predicate is written, so the roster of peers and
    the question "does the registry cover this universe at all" cannot end up
    answering different questions.
    """
    return (
        entry.disease.casefold() == disease.casefold()
        and entry.lot == lot
        and entry.setting.casefold() == setting.casefold()
        and entry.geography.casefold() == geography.casefold()
    )


def count_universe_entries(
    entries: list[RegistryEntry],
    *,
    disease: str,
    lot: str,
    setting: str,
    geography: str,
) -> int:
    """How many registry entries fall in this indication universe, at any date.

    The target's own entry counts, and so does an entry that launched after the
    date being scored. The question is whether the registry knows this corner
    of the market, not who was selling in it.
    """
    return sum(
        1
        for entry in entries
        if _in_universe(entry, disease=disease, lot=lot, setting=setting, geography=geography)
    )


def build_launch_peers(
    entries: list[RegistryEntry],
    *,
    target_product_id: str,
    disease: str,
    lot: str,
    setting: str,
    geography: str,
    launch_date: date,
) -> list[CompetitivePeer]:
    peers = [
        CompetitivePeer(
            id=item.product_id,
            classification=item.classification,
            launch_or_expected_date=item.approval_or_launch_date,
            same_moa=item.same_moa,
            same_route=item.same_route,
        )
        for item in entries
        if item.product_id != target_product_id
        and _in_universe(item, disease=disease, lot=lot, setting=setting, geography=geography)
        and item.approval_or_launch_date <= launch_date
    ]
    return sorted(peers, key=lambda item: (item.launch_or_expected_date, item.id))


def calculate_competitive_snapshot(
    *,
    indication_id: str,
    launch_date: date,
    geography: str,
    peers: list[CompetitivePeer],
    universe_entry_count: int,
) -> CompetitiveSnapshot:
    """Count the peers, and refuse where the registry does not cover the market.

    ``universe_entry_count`` is required rather than defaulted: a caller that
    does not know whether the registry covers the universe cannot be handed a
    band, and a default would be the absence turning back into a value.
    """
    counts = {
        classification: sum(1 for peer in peers if peer.classification == classification)
        for classification in WEIGHTS
    }
    score = sum(counts[classification] * weight for classification, weight in WEIGHTS.items())
    uncovered = universe_entry_count <= 0
    return CompetitiveSnapshot(
        indication_id=indication_id,
        launch_date=launch_date,
        geography=geography,
        formula_version=FORMULA_VERSION,
        direct_count=counts["direct"],
        indirect_count=counts["indirect"],
        substitutable_count=counts["substitutable"],
        near_term_phase3_count=counts["near_term_phase3"],
        same_moa_count=sum(peer.same_moa for peer in peers),
        same_route_count=sum(peer.same_route for peer in peers),
        marketed_peer_count=sum(
            counts[classification] for classification in MARKETED_CLASSIFICATIONS
        ),
        raw_score=score,
        peer_ids=sorted(peer.id for peer in peers),
        low_coverage=uncovered,
        not_assessed_reason=UNCOVERED_UNIVERSE if uncovered else None,
    )


def categorize_snapshots(snapshots: list[CompetitiveSnapshot]) -> list[CompetitiveSnapshot]:
    """Band each snapshot on its own peer count, leaving refusals unbanded.

    The cohort is recorded but not consulted. Two products with the same peer
    roster get the same band, and a product's band does not move because
    something else was scored beside it.
    """
    cohort_size = len(snapshots)
    return [
        replace(
            item,
            category=(
                None
                if item.not_assessed_reason
                else band_for_peer_count(item.marketed_peer_count)
            ),
            cohort_size=cohort_size,
        )
        for item in snapshots
    ]
