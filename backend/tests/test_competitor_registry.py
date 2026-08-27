from datetime import date

from app.analytics.competitive_intensity import RegistryEntry, build_launch_peers
from app.connectors.clinicaltrials import deduplicate_phase3_programs


def test_registry_counts_only_approved_prelaunch_matching_indication_peers():
    entries = [
        RegistryEntry("before", "Disease A", "1L", "metastatic", "U.S.", date(2020, 1, 1), "direct"),
        RegistryEntry("after", "Disease A", "1L", "metastatic", "U.S.", date(2023, 1, 1), "direct"),
        RegistryEntry("wrong-lot", "Disease A", "2L+", "metastatic", "U.S.", date(2019, 1, 1), "direct"),
        RegistryEntry("other", "Disease B", "1L", "metastatic", "U.S.", date(2019, 1, 1), "direct"),
    ]
    peers = build_launch_peers(
        entries,
        target_product_id="target",
        disease="Disease A",
        lot="1L",
        setting="metastatic",
        geography="U.S.",
        launch_date=date(2022, 1, 1),
    )
    assert [peer.id for peer in peers] == ["before"]


def test_phase3_programs_deduplicate_sponsor_asset_across_trials():
    programs = deduplicate_phase3_programs(
        [
            {"nct_id": "NCT1", "sponsor": "Acme", "asset": "ABC-123"},
            {"nct_id": "NCT2", "sponsor": "ACME", "asset": "abc-123"},
            {"nct_id": "NCT3", "sponsor": "Other", "asset": "XYZ"},
        ]
    )
    assert len(programs) == 2
    assert programs[0]["trial_ids"] == ["NCT1", "NCT2"]

