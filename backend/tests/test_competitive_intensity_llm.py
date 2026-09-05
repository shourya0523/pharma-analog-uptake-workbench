"""Tests for LLM-assessed competitive intensity.

Every test here runs against a stub client. The behaviour being protected is
what happens when the model misbehaves, and that has to be testable without a
network call or an API key.
"""

import json
from pathlib import Path

import pytest

from app.analytics.competitive_intensity_llm import (
    ASSESSOR_VERSION,
    Peer,
    assess_intensity,
    compare_to_rule,
    peers_at_launch,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD = REPO_ROOT / "seed" / "gold"

PEERS = [
    Peer("Flolan", "prostacyclin_pathway", "Intravenous", 1995),
    Peer("Tracleer", "endothelin_pathway", "Oral", 2001),
]

PROFILE = {
    "drug_name": "Remodulin",
    "indication_area": "Pulmonary arterial hypertension",
    "moa": "Prostacyclin (PGI2) analogue",
    "moa_class": "prostacyclin_pathway",
    "route_of_administration": "Parenteral",
    "first_approval_year": 2002,
}


class StubClient:
    def __init__(self, response):
        self.response = response
        self.last_user = None

    async def chat_json(self, *, model, system, user):
        self.last_user = user
        return self.response


@pytest.mark.asyncio
async def test_a_well_formed_answer_is_accepted_with_its_provenance():
    client = StubClient(
        {
            "label": "medium",
            "confidence": "high",
            "directly_competing_peers": ["Flolan"],
            "reasoning": "Flolan is the only other prostacyclin and is intravenous.",
        }
    )
    result = await assess_intensity(
        client=client, model="test/model", profile=PROFILE, peers=PEERS
    )

    assert result.label == "medium"
    assert result.confidence == "high"
    assert result.directly_competing_peers == ["Flolan"]
    assert result.provenance == "llm_assessed"
    assert result.assessor_version == ASSESSOR_VERSION
    assert result.refusal_reason is None


@pytest.mark.asyncio
async def test_the_roster_reaches_the_model_with_each_peers_attributes():
    """The whole point is judging the roster, so the roster has to be in the prompt."""
    client = StubClient({"label": "low", "confidence": "low", "reasoning": ""})
    await assess_intensity(client=client, model="test/model", profile=PROFILE, peers=PEERS)

    assert "Flolan" in client.last_user
    assert "prostacyclin_pathway" in client.last_user
    assert "approved 1995" in client.last_user
    # And the launching product's own position, or the model cannot judge fit.
    assert "Parenteral" in client.last_user


@pytest.mark.asyncio
async def test_a_peer_the_model_invented_voids_the_answer():
    """Citing something not on the roster means the label is not grounded either."""
    client = StubClient(
        {
            "label": "high",
            "confidence": "high",
            "directly_competing_peers": ["Flolan", "Uptravi"],
            "reasoning": "Crowded.",
        }
    )
    result = await assess_intensity(
        client=client, model="test/model", profile=PROFILE, peers=PEERS
    )

    assert result.label is None
    assert result.refusal_reason == "cited_peers_not_on_roster:Uptravi"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,expected",
    [
        ({"label": "inconclusive", "confidence": "low"}, "model_returned_inconclusive"),
        ({"label": "very high", "confidence": "high"}, "label_outside_vocabulary"),
        ({"label": "high", "confidence": "certain"}, "confidence_outside_vocabulary"),
    ],
)
async def test_bad_answers_refuse_rather_than_being_coerced(response, expected):
    client = StubClient(response)
    result = await assess_intensity(
        client=client, model="test/model", profile=PROFILE, peers=PEERS
    )

    assert result.label is None
    assert result.refusal_reason == expected


@pytest.mark.asyncio
async def test_a_refusal_compares_as_neither_agreement_nor_disagreement():
    client = StubClient({"label": "inconclusive", "confidence": "low"})
    result = await assess_intensity(
        client=client, model="test/model", profile=PROFILE, peers=PEERS
    )
    comparison = compare_to_rule(result, rule_label="medium", rule_peer_count=2)

    assert comparison.agrees is None
    assert comparison.rule_label == "medium"
    assert comparison.llm_label is None


@pytest.mark.asyncio
async def test_disagreement_is_recorded_not_resolved():
    """Neither side overwrites the other: the divergence is the finding."""
    client = StubClient(
        {"label": "low", "confidence": "high", "directly_competing_peers": [], "reasoning": "x"}
    )
    result = await assess_intensity(
        client=client, model="test/model", profile=PROFILE, peers=PEERS
    )
    comparison = compare_to_rule(result, rule_label="high", rule_peer_count=9)

    assert comparison.agrees is False
    assert comparison.rule_label == "high"
    assert comparison.llm_label == "low"


def test_peer_roster_is_built_from_gold_and_excludes_formulation_splits():
    profiles = [
        json.loads(line)
        for line in (GOLD / "product_profiles.jsonl").read_text().splitlines()
        if line.strip()
    ]
    by_name = {row["drug_name"]: row for row in profiles}
    peers = peers_at_launch(profiles, by_name["Adempas"])
    names = {peer.drug_name for peer in peers}

    assert "Flolan" in names and "Tracleer" in names
    # Same 2009 approval as Tyvaso, split only so revenue reports apart.
    assert "Nebulized Tyvaso" not in names
    # Nothing approved later than the launcher.
    assert all(peer.first_approval_year < by_name["Adempas"]["first_approval_year"] for peer in peers)
    # The roster must match the count gold published, or the model and the rule
    # are being shown different markets.
    assert len(peers) == by_name["Adempas"]["marketed_peers_at_launch"]


def test_the_assessor_never_writes_into_gold():
    """Gold stays free of model output, or it cannot detect the model being wrong."""
    for path in GOLD.glob("*.jsonl"):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            assert row.get("provenance") != "llm_assessed", path.name
            assert "llm_label" not in row, path.name
            if "competitive_intensity_basis" in row:
                assert row["competitive_intensity_basis"] in {
                    "marketed_peer_count_at_launch_v1",
                    "not_assessed_outside_catalog_universe",
                }, path.name
