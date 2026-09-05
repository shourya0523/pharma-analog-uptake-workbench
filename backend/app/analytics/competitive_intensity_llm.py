"""LLM-assessed competitive intensity at launch, scored against the gold rule.

The gold dataset derives intensity from a count: how many products in the same
indication were already approved. That is reproducible and auditable, and it is
also crude - it treats a generic of a molecule already on the roster as the same
competitive event as a new mechanism, and it cannot see that four products
spread across oral, inhaled and intravenous serve different patients.

This module asks a model to judge the roster instead of counting it. What it
must never do is write that judgement into gold. Gold is the oracle the pipeline
is measured against, and ``test_gold_builder_has_no_application_or_pipeline_imports``
exists to keep app code - this module included - out of it. An oracle that
contains model output cannot detect the model being wrong, because both sides of
the comparison fail together.

So the assessment lives here, carries its own provenance, and is *compared* to
the rule rather than replacing it. The disagreements are the point: they are
where a count and a judgement genuinely diverge, and they are worth a human
looking at.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.llm.client import load_prompt

PROMPT_NAME = "competitive_intensity_assessor"
ASSESSOR_VERSION = "llm_competitive_intensity_v1"

VALID_LABELS = ("low", "medium", "high")
VALID_CONFIDENCE = ("low", "medium", "high")
INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class Peer:
    drug_name: str
    moa_class: str
    route_of_administration: str
    first_approval_year: int


@dataclass(frozen=True)
class IntensityAssessment:
    drug_name: str
    label: str | None
    confidence: str | None
    directly_competing_peers: list[str]
    reasoning: str
    model: str
    assessor_version: str
    provenance: str = "llm_assessed"
    refusal_reason: str | None = None


@dataclass(frozen=True)
class IntensityComparison:
    drug_name: str
    rule_label: str
    rule_peer_count: int
    llm_label: str | None
    llm_confidence: str | None
    agrees: bool | None
    directly_competing_peers: list[str]
    reasoning: str


class ChatJSON(Protocol):
    async def chat_json(
        self, *, model: str, system: str, user: str
    ) -> dict[str, Any]:  # pragma: no cover - protocol
        ...


def _format_roster(peers: list[Peer]) -> str:
    if not peers:
        return "(none - this is the first approval in the indication)"
    return "\n".join(
        f"- {peer.drug_name}: {peer.moa_class}, {peer.route_of_administration}, "
        f"approved {peer.first_approval_year}"
        for peer in sorted(peers, key=lambda item: item.first_approval_year)
    )


def _refusal(drug_name: str, model: str, reason: str, reasoning: str = "") -> IntensityAssessment:
    return IntensityAssessment(
        drug_name=drug_name,
        label=None,
        confidence=None,
        directly_competing_peers=[],
        reasoning=reasoning,
        model=model,
        assessor_version=ASSESSOR_VERSION,
        refusal_reason=reason,
    )


async def assess_intensity(
    *,
    client: ChatJSON,
    model: str,
    profile: dict[str, Any],
    peers: list[Peer],
) -> IntensityAssessment:
    """Ask the model to judge the roster. Refuse rather than coerce a bad answer."""
    prompt = load_prompt(PROMPT_NAME)
    user = prompt["user_template"].format(
        drug_name=profile["drug_name"],
        indication_area=profile["indication_area"],
        moa=profile["moa"],
        moa_class=profile["moa_class"],
        route_of_administration=profile["route_of_administration"],
        first_approval_year=profile["first_approval_year"],
        peer_roster=_format_roster(peers),
    )
    response = await client.chat_json(model=model, system=prompt["system"], user=user)

    label = str(response.get("label", "")).strip().lower()
    reasoning = str(response.get("reasoning", "")).strip()

    if label == INCONCLUSIVE:
        return _refusal(profile["drug_name"], model, "model_returned_inconclusive", reasoning)
    if label not in VALID_LABELS:
        # A label outside the vocabulary is not something to coerce into the
        # nearest bucket - that would hide the model misbehaving.
        return _refusal(profile["drug_name"], model, "label_outside_vocabulary", reasoning)

    confidence = str(response.get("confidence", "")).strip().lower()
    if confidence not in VALID_CONFIDENCE:
        return _refusal(profile["drug_name"], model, "confidence_outside_vocabulary", reasoning)

    named = response.get("directly_competing_peers") or []
    known = {peer.drug_name for peer in peers}
    invented = [name for name in named if name not in known]
    if invented:
        # Citing a peer that is not on the roster means the answer is not
        # grounded in what it was given, so the label it came with is not
        # trustworthy either.
        return _refusal(
            profile["drug_name"],
            model,
            f"cited_peers_not_on_roster:{','.join(sorted(invented))}",
            reasoning,
        )

    return IntensityAssessment(
        drug_name=profile["drug_name"],
        label=label,
        confidence=confidence,
        directly_competing_peers=[name for name in named],
        reasoning=reasoning,
        model=model,
        assessor_version=ASSESSOR_VERSION,
    )


def compare_to_rule(
    assessment: IntensityAssessment, *, rule_label: str, rule_peer_count: int
) -> IntensityComparison:
    """Put the judgement beside the count without either overwriting the other."""
    return IntensityComparison(
        drug_name=assessment.drug_name,
        rule_label=rule_label,
        rule_peer_count=rule_peer_count,
        llm_label=assessment.label,
        llm_confidence=assessment.confidence,
        agrees=None if assessment.label is None else assessment.label == rule_label,
        directly_competing_peers=assessment.directly_competing_peers,
        reasoning=assessment.reasoning,
    )


def peers_at_launch(profiles: list[dict[str, Any]], target: dict[str, Any]) -> list[Peer]:
    """The roster a product actually faced: same indication, approved earlier.

    Formulation splits are excluded for the same reason the gold rule excludes
    them - Nebulized Tyvaso is Tyvaso's own approval recorded twice, so showing
    the model both would misstate the market.
    """
    return [
        Peer(
            row["drug_name"],
            row["moa_class"],
            row["route_of_administration"],
            row["first_approval_year"],
        )
        for row in profiles
        if row["indication_area"] == target["indication_area"]
        and row["first_approval_year"] < target["first_approval_year"]
        and row.get("peer_universe_role", "distinct_product") == "distinct_product"
    ]
