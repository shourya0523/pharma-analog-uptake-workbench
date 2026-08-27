from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

FORMULA_VERSION = "competitive_intensity_v1"
WEIGHTS = {
    "direct": 1.0,
    "indirect": 0.5,
    "substitutable": 0.75,
    "near_term_phase3": 0.25,
}


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
    raw_score: float
    peer_ids: list[str]
    cohort_percentile: float | None = None
    category: str | None = None
    cohort_size: int = 0
    low_coverage: bool = False


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
        and item.disease.casefold() == disease.casefold()
        and item.lot == lot
        and item.setting.casefold() == setting.casefold()
        and item.geography.casefold() == geography.casefold()
        and item.approval_or_launch_date <= launch_date
    ]
    return sorted(peers, key=lambda item: (item.launch_or_expected_date, item.id))


def calculate_competitive_snapshot(
    *,
    indication_id: str,
    launch_date: date,
    geography: str,
    peers: list[CompetitivePeer],
) -> CompetitiveSnapshot:
    counts = {
        classification: sum(1 for peer in peers if peer.classification == classification)
        for classification in WEIGHTS
    }
    score = sum(counts[classification] * weight for classification, weight in WEIGHTS.items())
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
        raw_score=score,
        peer_ids=sorted(peer.id for peer in peers),
    )


def categorize_snapshots(snapshots: list[CompetitiveSnapshot]) -> list[CompetitiveSnapshot]:
    cohort_size = len(snapshots)
    if cohort_size < 6:
        return [
            replace(
                item,
                category=(
                    "low"
                    if item.raw_score < 2
                    else "medium"
                    if item.raw_score < 5
                    else "high"
                ),
                cohort_size=cohort_size,
                low_coverage=True,
            )
            for item in snapshots
        ]

    ordered = sorted(snapshots, key=lambda item: (item.raw_score, item.indication_id))
    percentiles = {
        id(item): (index / (cohort_size - 1)) * 100
        for index, item in enumerate(ordered)
    }
    return [
        replace(
            item,
            cohort_percentile=percentiles[id(item)],
            category=(
                "low"
                if percentiles[id(item)] <= 33
                else "medium"
                if percentiles[id(item)] <= 67
                else "high"
            ),
            cohort_size=cohort_size,
            low_coverage=False,
        )
        for item in snapshots
    ]

