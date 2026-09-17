"""What the judge is shown about the figure it is asked to judge.

The candidate dict held seven keys - the period, the value, the period type,
the scope, the formulation and two label fields. Every other column the
pipeline had already filled was absent, so the judge was asked whether a quote
supports a figure without being told what the figure is denominated in, where
it was sold, or which reader produced it. And it was never told which other
products the filing reports, so "is this quote about a peer brand" - one of the
four questions its own prompt names - had nothing to answer with.

Invented names: Calderon, Calderon XR, NuVessa, Acme Pharma.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DatapointORM, DrugJobORM, ExtractionRunORM
from app.domain.models import ParsedDocument, ParsingStatus, ValidationStatus, new_id
from app.pipeline import orchestrator as orchestrator_module
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import LocalFileStore

# The filer's own product schedule, and a table that is not one. Only the
# first says anything about which products this issuer sells.
SCHEDULE = [
    ["", "Three Months Ended June 30,", ""],
    ["", "2024", "2023"],
    ["Calderon XR", "34,974", "22,209"],
    ["NuVessa", "12,088", "9,401"],
    ["Total product revenue, net", "47,062", "31,610"],
]
COVER_PAGE = [
    ["Delaware", "01-2345", "99-9999999"],
    ["(State of incorporation)", "(Commission file number)", "(IRS employer ID)"],
]

NOTE = ("Calderon XR net product revenue for the three months ended June 30, 2024 is for "
        "the period between May 20, 2024 (date of commercial launch) and June 30, 2024")


def _orchestrator(tmp_path, *, quote: str, **datapoint):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    job = DrugJobORM(id=new_id(), run_id=run.id, drug_name="Calderon XR",
                     manufacturer="Acme Pharma", status="running", quality_flags=[])
    db.add(job)
    fields = {
        "period": "2024Q2", "value_reported": 34974.0,
        "value_normalized_usd_millions": 34.974, "currency": "USD",
        "unit": "thousands", "period_type": "quarterly",
        "revenue_scope": "Product family", "geography": "United States",
        "extraction_method": "table", "confidence_score": 0.75,
        "source_url": "https://example.invalid/10q.htm",
        "validation_status": ValidationStatus.PENDING.value,
        "citation_json": {"source_url": "https://example.invalid/10q.htm",
                          "source_type": "quarterly_report"},
        "issue_flags": [],
    }
    fields.update(datapoint)
    row = DatapointORM(id=new_id(), job_id=job.id, source_id="s1",
                       source_quote=quote, **fields)
    db.add(row)
    db.commit()
    parsed = {"s1": ParsedDocument(source_id="s1", text_blocks=[quote],
                                   tables=[COVER_PAGE, SCHEDULE],
                                   parsing_status=ParsingStatus.SUCCESS)}
    return db, PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path))), job, row, parsed


def _spy(monkeypatch) -> dict:
    seen: dict = {}
    real = orchestrator_module.try_deterministic_judgment

    def recording(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(orchestrator_module, "try_deterministic_judgment", recording)
    return seen


@pytest.mark.asyncio
async def test_the_judge_is_given_every_column_the_row_states(tmp_path, monkeypatch):
    seen = _spy(monkeypatch)
    db, orch, job, row, parsed = _orchestrator(
        tmp_path, quote="Calderon XR 34,974 22,209")

    await orch._judge(job, [row], [], parsed, {})

    candidate = seen["candidate"]
    for key, value in (("unit", "thousands"), ("currency", "USD"),
                       ("geography", "United States"), ("extraction_method", "table"),
                       ("source_type", "quarterly_report")):
        assert candidate[key] == value, key
    # The seven it already had are still there.
    assert candidate["period"] == "2024Q2"
    assert candidate["value_reported"] == 34974.0
    assert candidate["period_type"] == "quarterly"
    assert candidate["revenue_scope"] == "Product family"


@pytest.mark.asyncio
async def test_the_peers_are_the_documents_own_rows_and_not_a_catalogue(tmp_path, monkeypatch):
    """The schedule our product is printed in is the filer's product list.

    A catalogue of brands answers for the brands it holds and waves every
    product it has not seen through, so what could be confused with ours is
    read off the document. The cover page is a table too and names no product.
    """
    seen = _spy(monkeypatch)
    db, orch, job, row, parsed = _orchestrator(
        tmp_path, quote="Calderon XR 34,974 22,209")

    await orch._judge(job, [row], [], parsed, {})

    assert seen["peer_names"] == ["nuvessa"], (
        "the sibling row, and neither our own row, the total, nor the cover page"
    )


@pytest.mark.asyncio
async def test_a_quote_that_is_only_the_sibling_row_is_vetoed(tmp_path, monkeypatch):
    """The veto the peers were for, which nothing had ever supplied them to."""
    seen = _spy(monkeypatch)
    db, orch, job, row, parsed = _orchestrator(
        tmp_path, quote="NuVessa 34,974 22,209")

    await orch._judge(job, [row], [], parsed, {})

    assert row.source_support == "misclassified"
    assert row.validation_status == ValidationStatus.NEEDS_REVIEW.value

    from app.llm.client import apply_judge_hard_vetoes
    judged = apply_judge_hard_vetoes(
        product="Calderon XR", candidate=seen["candidate"],
        quote="NuVessa 34,974 22,209", peer_names=seen["peer_names"],
        judgment={"support_classification": "supported",
                  "validation_status": "auto_pass", "issues": []},
    )
    assert "hard_veto:other_brand:nuvessa" in judged["issues"]
    # With nothing to compare against, the same quote raises no brand veto at
    # all - which is what every production call site supplied.
    blind = apply_judge_hard_vetoes(
        product="Calderon XR", candidate=seen["candidate"],
        quote="NuVessa 34,974 22,209",
        judgment={"support_classification": "supported",
                  "validation_status": "auto_pass", "issues": []},
    )
    assert not any("other_brand" in issue for issue in blind["issues"])


@pytest.mark.asyncio
async def test_the_footnote_travels_to_the_judge_as_a_field_of_its_own(tmp_path, monkeypatch):
    """The filer's note about the figure, not trailing text in the quote."""
    from app.parsing.labels import cite_footnote

    seen = _spy(monkeypatch)
    quote = "Calderon XR 34,974 22,209" + cite_footnote("*", NOTE)
    db, orch, job, row, parsed = _orchestrator(tmp_path, quote=quote)

    await orch._judge(job, [row], [], parsed, {})

    assert seen["candidate"]["footnote"] == NOTE


def test_the_deterministic_judge_runs_against_the_same_names_as_the_model_one():
    """It passed the brand string alone, so its vetoes asked a narrower question.

    A quote naming the product by its generic did not name it, and a quote
    naming the brand on the next row named nothing at all - on the path that
    decides without asking a model.
    """
    from app.quality.fast_judge import try_deterministic_judgment

    candidate = {"period": "2024Q2", "period_type": "quarterly",
                 "revenue_scope": "Product family", "value_reported": 34.974}

    # The generic reaches the veto: the quote names the product.
    generic = try_deterministic_judgment(
        product="Calderon XR", generic="calderonib", quote="Calderonib sales were $34.974 million",
        candidate=candidate,
    )
    assert generic and generic["validation_status"] == "auto_pass", generic

    # And so do the document's own siblings.
    sibling = try_deterministic_judgment(
        product="Calderon XR", generic="calderonib", quote="NuVessa sales were $34.974 million",
        candidate=candidate, peer_names=["nuvessa"],
    )
    assert sibling and sibling["support_classification"] == "misclassified"
    assert "hard_veto:other_brand:nuvessa" in sibling["issues"]


def test_a_note_dating_the_figure_inside_the_period_is_a_partial_period():
    """An eighteen-day stub is not a quarter of a launch.

    The note names the day selling began and the day the period ends, and the
    day it began is inside the period the row reports. The phrase pattern that
    used to answer this excluded any note naming a launch - which is the one
    shape that matters most, because a launch quarter is the first point of the
    ramp and layer 3 reads time to peak off it.
    """
    from app.parsing.labels import FLAG_PARTIAL, read_footnote
    from app.quality.fast_judge import try_deterministic_judgment

    reading = read_footnote(NOTE, ["calderon xr"])
    assert FLAG_PARTIAL in reading.flags
    assert reading.applies_to(3, "2024Q2") is True

    # A launch before the period is not a partial period: the product was on
    # sale for all of it.
    earlier = read_footnote(
        "Calderon XR was launched on May 20, 2024 and the three months ended "
        "September 30, 2024 reflect a full quarter of sales",
        ["calderon xr"],
    )
    assert FLAG_PARTIAL not in earlier.flags

    judged = try_deterministic_judgment(
        product="Calderon XR", generic=None, quote="Calderon XR 34,974",
        candidate={"period": "2024Q2", "period_type": "quarterly",
                   "revenue_scope": "Product family", "value_reported": 34974.0,
                   "label_flags": list(reading.flags)},
    )
    assert judged and judged["issues"] == ["deterministic:partial_period"]


@pytest.mark.asyncio
async def test_a_flag_is_a_code_and_the_model_s_reasoning_is_a_note(tmp_path, monkeypatch):
    """`issue_flags` is matched against by name, so it holds names.

    The model answering the judge writes its reasoning into the same list the
    vetoes write their codes into, and a reader downstream tested that list for
    a word. A sentence that happened to contain the word marked the row.

    Both answers: the code the judge raised is on the row's flags, and the
    sentence the model wrote is in the notes a person reads and nowhere else.
    """
    monkeypatch.setattr(orchestrator_module, "try_deterministic_judgment", lambda **_: None)
    _db, orch, job, row, parsed = _orchestrator(
        tmp_path, quote="Calderon XR 34,974 22,209")

    reasoning = (
        "The quote reports the figure in a column whose heading is in conflict "
        "with the period claimed."
    )

    async def judge(**_kwargs):
        return {
            "support_classification": "supported",
            "validation_status": "needs_review",
            "issues": ["hard_veto:quote_states_a_different_period", reasoning],
        }

    async def no_search(**_kwargs):
        return None

    monkeypatch.setattr(orch.llm, "judge", judge)
    monkeypatch.setattr(orch.llm, "judge_with_search", no_search)

    await orch._judge(job, [row], [], parsed, {})

    assert row.issue_flags == ["hard_veto:quote_states_a_different_period"]
    assert row.reviewer_notes == reasoning
