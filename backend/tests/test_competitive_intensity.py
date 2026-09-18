"""Tests for launch competitive intensity.

The property worth protecting is that the band belongs to the market: the same
peer roster gives the same band however many other products are scored beside
it. The second property is the refusal - an indication universe the registry
does not cover has no band at all, rather than the band an empty market earns.
"""

from datetime import date

from app.analytics.competitive_intensity import (
    CompetitivePeer,
    RegistryEntry,
    calculate_competitive_snapshot,
    categorize_snapshots,
    count_universe_entries,
)


def _snapshot(name: str, *, peers: int, covered: bool = True, classification: str = "direct"):
    return calculate_competitive_snapshot(
        indication_id=name,
        launch_date=date(2022, 1, 1),
        geography="U.S.",
        peers=[
            CompetitivePeer(f"{name}-{index}", classification, date(2020, 1, 1))
            for index in range(peers)
        ],
        universe_entry_count=(peers + 1) if covered else 0,
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
        universe_entry_count=5,
    )

    assert snapshot.raw_score == 2.5
    assert snapshot.peer_ids == ["direct", "generic", "indirect", "trial"]
    # The phase 3 programme is counted and weighted, but it is not selling yet,
    # so it does not enter the count the band is taken over.
    assert snapshot.near_term_phase3_count == 1
    assert snapshot.marketed_peer_count == 3


def test_bands_are_absolute_over_the_marketed_peer_count():
    snapshots = categorize_snapshots(
        [_snapshot("empty", peers=0), _snapshot("some", peers=3), _snapshot("crowded", peers=9)]
    )

    assert [item.category for item in snapshots] == ["low", "medium", "high"]
    # The registry covers all three universes, so none of them is a refusal and
    # none of them is flagged as thin coverage.
    assert not any(item.low_coverage for item in snapshots)
    assert not any(item.not_assessed_reason for item in snapshots)


def test_identical_inputs_give_identical_bands():
    cohort = categorize_snapshots([_snapshot(f"same{index}", peers=3) for index in range(6)])

    assert {item.category for item in cohort} == {"medium"}
    assert {item.raw_score for item in cohort} == {3.0}


def test_an_uncovered_indication_universe_is_not_banded_low():
    """Zero registry entries is unknown. An empty market is low. They differ.

    Both answers are here on purpose: a refusal that fires on everything would
    pass a test that only holds the refusing case.
    """
    uncovered, empty_market = categorize_snapshots(
        [_snapshot("uncovered", peers=0, covered=False), _snapshot("empty", peers=0)]
    )

    assert uncovered.category is None
    assert uncovered.not_assessed_reason == "indication_universe_not_in_registry"
    assert uncovered.low_coverage

    assert empty_market.category == "low"
    assert empty_market.not_assessed_reason is None


def test_the_band_does_not_change_with_cohort_size():
    alone = categorize_snapshots([_snapshot("subject", peers=3)])
    in_a_crowd = categorize_snapshots(
        [_snapshot("subject", peers=3)]
        + [_snapshot(f"other{index}", peers=index) for index in range(12)]
    )
    subject = next(item for item in in_a_crowd if item.indication_id == "subject")

    assert alone[0].category == subject.category
    assert alone[0].cohort_size == 1
    assert subject.cohort_size == 13


def test_the_universe_count_is_not_the_peer_roster():
    """A peer roster is who was selling; the universe is what the registry knows.

    An entry that launched after the date being scored keeps the universe
    covered without ever becoming a peer, which is what lets an empty market be
    told apart from an unread one.
    """
    entries = [
        RegistryEntry("later", "Calderon's disease", "1L", "metastatic", "U.S.", date(2024, 1, 1), "direct"),
    ]
    universe = dict(disease="Calderon's disease", lot="1L", setting="metastatic", geography="U.S.")

    assert count_universe_entries(entries, **universe) == 1
    assert count_universe_entries(entries, **{**universe, "disease": "NuVessa syndrome"}) == 0
