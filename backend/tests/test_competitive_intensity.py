from datetime import date

from app.analytics.competitive_intensity import (
    CompetitivePeer,
    calculate_competitive_snapshot,
    categorize_snapshots,
)


def test_v1_score_uses_stored_peer_classifications():
    peers = [
        CompetitivePeer("direct", "direct", date(2020, 1, 1)),
        CompetitivePeer("indirect", "indirect", date(2019, 1, 1)),
        CompetitivePeer("generic", "substitutable", date(2021, 1, 1)),
        CompetitivePeer("trial", "near_term_phase3", date(2022, 1, 1)),
    ]
    snapshot = calculate_competitive_snapshot(
        indication_id="i1",
        launch_date=date(2022, 2, 1),
        geography="U.S.",
        peers=peers,
    )

    assert snapshot.formula_version == "competitive_intensity_v1"
    assert snapshot.raw_score == 2.5
    assert snapshot.peer_ids == ["direct", "generic", "indirect", "trial"]


def test_small_cohort_uses_provisional_thresholds_and_low_coverage():
    snapshots = [
        calculate_competitive_snapshot(
            indication_id=f"i{index}",
            launch_date=date(2022, 1, 1),
            geography="U.S.",
            peers=[
                CompetitivePeer(f"p{index}-{peer}", "direct", date(2020, 1, 1))
                for peer in range(index)
            ],
        )
        for index in (1, 3, 6)
    ]
    categorized = categorize_snapshots(snapshots)

    assert [item.category for item in categorized] == ["low", "medium", "high"]
    assert all(item.low_coverage for item in categorized)
    assert all(item.cohort_size == 3 for item in categorized)


def test_six_launch_cohort_uses_stable_percentile_categories():
    snapshots = [
        calculate_competitive_snapshot(
            indication_id=f"i{count}",
            launch_date=date(2022, 1, 1),
            geography="U.S.",
            peers=[
                CompetitivePeer(f"p{count}-{peer}", "direct", date(2020, 1, 1))
                for peer in range(count)
            ],
        )
        for count in range(6)
    ]
    categorized = categorize_snapshots(snapshots)
    assert [item.category for item in categorized].count("low") == 2
    assert [item.category for item in categorized].count("high") == 2
    assert not any(item.low_coverage for item in categorized)

