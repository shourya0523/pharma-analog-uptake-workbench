"""Profile fields are challenged by independent search, and corrections need citations.

openFDA's drugsFDA record for Tyvaso (NDA022387) reports route ORAL for an inhaled
product. A cited source value is therefore not assumed correct: the judge can replace
it, but only with an equally cited replacement.
"""

import inspect

from app.llm.client import load_prompt
from app.pipeline.orchestrator import PipelineOrchestrator
from app.quality.profile import (
    PRIORITY_JUDGE_FIELDS,
    SKIP_JUDGE_FIELDS,
    apply_profile_judgment,
    normalize_value,
    select_profile_fields_for_judgment,
    values_conflict,
)

CITED_CORRECTION = {
    "verdict": "contradicted",
    "corrected_value": "Inhalation",
    "source_url": "https://www.accessdata.fda.gov/drugsatfda_docs/label/2021/022387s017lbl.pdf",
    "source_quote": "TYVASO is a prostacyclin mimetic indicated for... administered by oral inhalation",
    "confidence": 0.9,
    "explanation": "The approved label describes oral inhalation.",
}


def test_registry_value_can_be_corrected_when_the_correction_is_cited():
    outcome = apply_profile_judgment("roa", "ORAL", CITED_CORRECTION)
    assert outcome.corrected is True
    assert outcome.value == "Inhalation"
    assert outcome.verdict == "contradicted"
    assert "corrected_by_search" in outcome.flags
    # The superseded registry value stays on the record
    assert outcome.correction["superseded_value"] == "ORAL"
    assert outcome.correction["source_url"].startswith("https://")
    assert outcome.correction["source_quote"]


def test_correction_without_a_citation_is_refused():
    for missing in ({"source_url": ""}, {"source_quote": ""}, {"source_url": "not-a-url"}):
        judgment = {**CITED_CORRECTION, **missing}
        outcome = apply_profile_judgment("roa", "ORAL", judgment)
        assert outcome.corrected is False, missing
        assert outcome.value == "ORAL"
        assert "correction_missing_citation" in outcome.flags


def test_low_confidence_correction_is_refused():
    outcome = apply_profile_judgment("roa", "ORAL", {**CITED_CORRECTION, "confidence": 0.3})
    assert outcome.corrected is False
    assert "correction_low_confidence" in outcome.flags
    # An explicit threshold can accept it
    accepted = apply_profile_judgment(
        "roa", "ORAL", {**CITED_CORRECTION, "confidence": 0.3}, min_confidence=0.2
    )
    assert accepted.corrected is True


def test_contradicted_without_a_replacement_keeps_the_value():
    outcome = apply_profile_judgment(
        "roa", "ORAL", {**CITED_CORRECTION, "corrected_value": "Not specified"}
    )
    assert outcome.corrected is False
    assert outcome.value == "ORAL"
    assert "contradicted_without_replacement" in outcome.flags


def test_supported_and_inconclusive_leave_the_value_alone():
    supported = apply_profile_judgment("roa", "ORAL", {"verdict": "supported", "confidence": 0.9})
    assert supported.corrected is False and supported.flags == ["search_corroborated"]

    inconclusive = apply_profile_judgment("roa", "ORAL", {"verdict": "inconclusive"})
    assert inconclusive.corrected is False and inconclusive.flags == ["search_inconclusive"]

    # An unavailable judge must not be read as a contradiction
    unjudged = apply_profile_judgment("roa", "ORAL", {})
    assert unjudged.verdict == "unjudged" and unjudged.value == "ORAL"


def test_a_restatement_of_the_same_value_is_not_a_correction():
    outcome = apply_profile_judgment("roa", "Inhalation", {**CITED_CORRECTION, "corrected_value": "inhalation"})
    assert outcome.corrected is False
    assert outcome.verdict == "supported"


def test_values_conflict_ignores_formatting_but_catches_real_disagreement():
    assert not values_conflict("TREPROSTINIL", "treprostinil")
    assert not values_conflict("Inhalation ", "inhalation")
    assert not values_conflict("Tyvaso", None)
    # Substring restatements are the same answer at different precision
    assert not values_conflict("Prostacyclin analogue", "prostacyclin")
    assert values_conflict("ORAL", "Inhalation")
    assert values_conflict("2002-05-21", "2009-07-30")


def test_normalize_value_strips_trailing_punctuation():
    assert normalize_value(" Inhalation. ") == "inhalation"
    assert normalize_value(None) == ""


def test_regulatory_fields_are_prioritised_for_judging():
    assert "roa" in PRIORITY_JUDGE_FIELDS
    assert "fda_approval_date" in PRIORITY_JUDGE_FIELDS
    assert "dosage_form" in PRIORITY_JUDGE_FIELDS
    assert "indication" in PRIORITY_JUDGE_FIELDS
    assert "moa" in PRIORITY_JUDGE_FIELDS


def test_select_profile_fields_judges_every_content_field():
    class Row:
        def __init__(self, field, value, conflict=False):
            self.field = field
            self.value = value
            self.citation_json = {"conflicting_source": {"value": "x"}} if conflict else {}

    rows = [
        Row("llm_aliases", '{"aliases":[]}'),
        Row("indication", "PAH"),
        Row("moa", "prostacyclin analogue"),
        Row("ticker", "UTHR"),
        Row("roa", "ORAL", conflict=True),
        Row("brand_name", "TYVASO"),
        Row("empty", "not specified"),
    ]
    selected = select_profile_fields_for_judgment(rows)
    names = [r.field for r in selected]
    assert "llm_aliases" not in names
    assert "empty" not in names
    assert names[0] == "roa"  # conflicts first
    assert set(names) == {"roa", "indication", "moa", "ticker", "brand_name"}
    # Optional budget still works for operators
    assert [r.field for r in select_profile_fields_for_judgment(rows, max_fields=2)] == [
        "roa",
        "moa",
    ]


def test_judge_prompt_requires_a_cited_correction():
    prompt = load_prompt("profile_field_judge")
    text = f"{prompt['system']}\n{prompt['user_template']}"
    assert "corrected_value" in text
    assert "source_url" in text and "source_quote" in text
    assert "inconclusive" in text
    # The prompt must not present the stated value as trustworthy
    assert "do not assume the stated value is correct" in prompt["system"].lower()


def test_conflicting_sources_are_recorded_and_judged_first():
    extract = inspect.getsource(PipelineOrchestrator._extract_metadata)
    # Disagreements are kept for adjudication rather than one source silently winning
    assert "values_conflict(written[name], field[\"value\"])" in extract
    assert "conflicting_source" in extract

    judge = inspect.getsource(PipelineOrchestrator._judge_profile)
    assert "select_profile_fields_for_judgment" in judge
    assert "profile_judge_max_fields" in judge
    assert "llm_aliases" in SKIP_JUDGE_FIELDS
    # Corrections always go back to a human
    assert "NEEDS_REVIEW" in judge
