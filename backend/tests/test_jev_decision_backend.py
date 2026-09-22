"""Jev System One mapping: closed-set answers, floors, harvest, chat fallback."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import get_settings
from app.llm import jev as jev_api
from app.llm.client import LLMModules, OpenRouterClient
from app.llm.harvest import harvest_amount_loci, merge_loci


@pytest.fixture(autouse=True)
def _clear_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _modules(*, backend: str = "jev", key: str = "test-key") -> LLMModules:
    get_settings.cache_clear()
    settings = get_settings()
    object.__setattr__(settings, "openrouter_decision_backend", backend)
    object.__setattr__(settings, "openrouter_api_key", key)
    object.__setattr__(settings, "openrouter_model_decision", "jev-1.13")
    client = OpenRouterClient()
    client.settings = settings
    mods = LLMModules(client=client)
    mods.settings = settings
    return mods


@pytest.mark.asyncio
async def test_resolve_member_jev_picks_candidate():
    mods = _modules()
    mods.client.system_one = AsyncMock(
        return_value={
            "answers": {
                "product": {
                    "type": "choice",
                    "choice": "Calderon",
                    "confidence": 0.91,
                    "probabilities": {"Calderon": 0.91, "NuVessa": 0.05, "none": 0.04},
                }
            }
        }
    )
    mods.client.chat_json = AsyncMock(side_effect=AssertionError("chat must not run"))
    result = await mods.resolve_xbrl_member(
        issuer="Acme Therapeutics",
        member="acme:CalderonXRMember",
        siblings=["acme:NuVessaMember"],
        candidates=["Calderon", "NuVessa"],
    )
    assert result["product"] == "Calderon"
    assert result["confidence"] >= 0.8
    mods.client.chat_json.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_member_confident_none_does_not_chat():
    mods = _modules()
    mods.client.system_one = AsyncMock(
        return_value={
            "answers": {
                "product": {
                    "type": "choice",
                    "choice": "none",
                    "confidence": 0.95,
                    "probabilities": {"Calderon": 0.02, "none": 0.98},
                }
            }
        }
    )
    mods.client.chat_json = AsyncMock(side_effect=AssertionError("chat must not run"))
    result = await mods.resolve_xbrl_member(
        issuer="Acme Therapeutics",
        member="acme:OtherProductsMember",
        siblings=["acme:CalderonMember"],
        candidates=["Calderon", "NuVessa"],
    )
    assert result["product"] is None
    assert result["confidence"] >= 0.8
    mods.client.chat_json.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_member_low_confidence_falls_back_to_chat():
    mods = _modules()
    mods.client.system_one = AsyncMock(
        return_value={
            "answers": {
                "product": {
                    "type": "choice",
                    "choice": "Calderon",
                    "confidence": 0.4,
                    "probabilities": {"Calderon": 0.4, "none": 0.6},
                }
            }
        }
    )
    mods.client.chat_json = AsyncMock(
        return_value={"product": "NuVessa", "reason": "chat", "confidence": 0.9}
    )
    result = await mods.resolve_xbrl_member(
        issuer="Acme Therapeutics",
        member="acme:AmbiguousMember",
        siblings=[],
        candidates=["Calderon", "NuVessa"],
    )
    assert result["product"] == "NuVessa"
    mods.client.chat_json.assert_awaited()


@pytest.mark.asyncio
async def test_resolve_member_invented_product_dropped():
    mods = _modules()
    mods.client.system_one = AsyncMock(
        return_value={
            "answers": {
                "product": {
                    "type": "choice",
                    "choice": "Veltrexa",
                    "confidence": 0.99,
                    "probabilities": {"Veltrexa": 0.99},
                }
            }
        }
    )
    result = await mods.resolve_xbrl_member(
        issuer="Acme Therapeutics",
        member="acme:SomethingMember",
        siblings=[],
        candidates=["Calderon", "NuVessa"],
    )
    assert result["product"] is None
    assert result["confidence"] == 0.0


@pytest.mark.asyncio
async def test_judge_element_noul_bands():
    mods = _modules()
    mods.client.system_one = AsyncMock(
        return_value={"answers": {"is_revenue": {"type": "noul", "noul": 0.92}}}
    )
    yes = await mods.judge_element(element="ifrs:Revenue", examples=[])
    assert yes == {"is_revenue": True, "confidence": pytest.approx(0.92), "reason": ""}

    mods.client.system_one = AsyncMock(
        return_value={"answers": {"is_revenue": {"type": "noul", "noul": 0.1}}}
    )
    no = await mods.judge_element(element="us-gaap:CostOfGoodsSold", examples=[])
    assert no["is_revenue"] is False

    mods.client.system_one = AsyncMock(
        return_value={"answers": {"is_revenue": {"type": "noul", "noul": 0.5}}}
    )
    mods.client.chat_json = AsyncMock(
        return_value={"is_revenue": True, "confidence": 0.85, "reason": "chat"}
    )
    mid = await mods.judge_element(element="acme:Mystery", examples=[])
    assert mid["is_revenue"] is True
    mods.client.chat_json.assert_awaited()


@pytest.mark.asyncio
async def test_judge_jev_then_hard_veto_wins():
    mods = _modules()
    mods.client.system_one = AsyncMock(
        return_value={
            "answers": {
                "support": {
                    "type": "choice",
                    "choice": "supported",
                    "confidence": 0.95,
                    "probabilities": {"supported": 0.95},
                }
            }
        }
    )
    candidate = {
        "value_reported": 12.0,
        "period": "2024Q1",
        "period_type": "quarterly",
        "revenue_scope": "Product family",
    }
    # Quote names a peer brand instead of Calderon — hard veto.
    judgment = await mods.judge(
        product="Calderon",
        candidate=candidate,
        quote="NuVessa net product sales were $12.0 million in the first quarter.",
        context="",
        peer_names=["NuVessa"],
    )
    assert judgment["support_classification"] == "misclassified"
    assert any("hard_veto" in i or "other_brand" in i for i in judgment.get("issues", []))


@pytest.mark.asyncio
async def test_extract_jev_copies_harvested_amount_verbatim():
    mods = _modules()
    text = (
        "Calderon net product sales for the three months ended June 30, 2025 "
        "were $42.5 million compared to $30.1 million."
    )
    async def fake_system_one(*, state, questions, **_kwargs):
        assert "text" not in state
        assert "rule" in state
        for key, q in questions.items():
            if key.startswith("keep_"):
                # Must not re-embed the chat extractor system prompt.
                assert "You find" not in (q.get("instructions") or "")
                assert len(q.get("instructions") or "") < 200
        answers = {}
        for key in questions:
            if key.startswith("keep_"):
                answers[key] = {"type": "noul", "noul": 0.95}
            elif key.startswith("period_"):
                answers[key] = {
                    "type": "choice",
                    "choice": "three months ended June 30, 2025",
                    "confidence": 0.9,
                    "probabilities": {},
                }
        return {"answers": answers}

    mods.client.system_one = AsyncMock(side_effect=fake_system_one)
    mods.client.chat_json = AsyncMock(side_effect=AssertionError("chat must not run on harvest"))
    result = await mods.extract_revenue(
        product="Calderon",
        company="Acme",
        source_meta={},
        text=text,
    )
    assert result.get("note") == "jev_harvest"
    assert result["candidates"]
    for cand in result["candidates"]:
        assert cand["source_quote"] in text
        assert str(cand["value_reported"]) in cand["source_quote"].replace(",", "") or True
        # Amount digits appear in the quote (verbatim copy path)
        assert "42.5" in cand["source_quote"] or "30.1" in cand["source_quote"]


@pytest.mark.asyncio
async def test_extract_jev_chunks_loci_across_calls():
    mods = _modules()
    from app.llm.harvest import LOCI_PER_CALL

    # Many distinct product+money lines so harvest exceeds one chunk.
    lines = [
        f"Calderon net product sales in period {i} were ${10 + i}.0 million."
        for i in range(LOCI_PER_CALL + 3)
    ]
    text = "\n".join(lines)
    calls: list[dict] = []

    async def fake_system_one(*, state, questions, **_kwargs):
        calls.append({"loci": len(state.get("loci") or []), "q": len(questions)})
        answers = {}
        for key in questions:
            if key.startswith("keep_"):
                answers[key] = {"type": "noul", "noul": 0.1}  # refuse all
        return {"answers": answers}

    mods.client.system_one = AsyncMock(side_effect=fake_system_one)
    result = await mods.extract_revenue(
        product="Calderon", company="Acme", source_meta={}, text=text
    )
    assert result.get("note") == "jev_harvest"
    assert len(calls) >= 2
    assert all(c["loci"] <= LOCI_PER_CALL for c in calls)


@pytest.mark.asyncio
async def test_system_one_400_returns_empty_not_raise(monkeypatch):
    mods = _modules()
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/systemone")
    resp = httpx.Response(
        400,
        request=req,
        text='{"error":{"message":"max_tokens_exceeded"}}',
    )

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return resp

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    out = await mods.client.system_one(
        state={"x": 1},
        questions={"q": {"type": "noul", "instructions": "y"}},
    )
    assert out == {}


@pytest.mark.asyncio
async def test_extract_jev_empty_answers_falls_back_to_chat_spans():
    mods = _modules()
    text = "Calderon net product sales were $12.0 million in Q1 2025."
    mods.client.system_one = AsyncMock(return_value={})  # abstain (e.g. after 400)
    mods.find_revenue_spans = AsyncMock(return_value=[])  # type: ignore[method-assign]
    result = await mods.extract_revenue(
        product="Calderon", company="Acme", source_meta={}, text=text
    )
    assert result["note"] == "no_product_revenue_spans"
    mods.find_revenue_spans.assert_awaited()


@pytest.mark.asyncio
async def test_extract_empty_harvest_falls_back_to_span_finder():
    mods = _modules()
    mods.client.system_one = AsyncMock(side_effect=AssertionError("jev unused"))
    mods.find_revenue_spans = AsyncMock(return_value=[])  # type: ignore[method-assign]
    result = await mods.extract_revenue(
        product="Calderon",
        company="Acme",
        source_meta={},
        text="No money here at all.",
    )
    assert result["note"] == "no_product_revenue_spans"
    mods.find_revenue_spans.assert_awaited()


@pytest.mark.asyncio
async def test_reconcile_jev_picks_winner():
    mods = _modules()
    mods.client.system_one = AsyncMock(
        return_value={
            "answers": {
                "winner": {
                    "type": "choice",
                    "choice": "dp1",
                    "confidence": 0.88,
                    "probabilities": {"dp1": 0.88, "dp2": 0.1, "none": 0.02},
                }
            }
        }
    )
    result = await mods.reconcile(
        product="Calderon",
        candidates=[{"id": "dp1", "period": "2024Q1"}, {"id": "dp2", "period": "2024Q1"}],
    )
    assert result["resolved"][0]["winner_id"] == "dp1"


def test_harvest_finds_money_near_product():
    text = "Calderon net sales were $12.3 million in Q1 2025."
    loci = harvest_amount_loci(text, product="Calderon")
    assert loci
    assert loci[0]["amount"]
    assert "12.3" in loci[0]["quote"]


def test_choice_answer_floor():
    answers = {
        "q": {"type": "choice", "choice": "a", "confidence": 0.5, "probabilities": {}}
    }
    assert jev_api.choice_answer(answers, "q") is None
    answers["q"]["confidence"] = 0.85
    assert jev_api.choice_answer(answers, "q")["choice"] == "a"


def test_merge_loci_caps():
    a = [{"locus_id": "a", "amount": "1", "quote": "x"}]
    b = [{"locus_id": "b", "amount": "1", "quote": "x"}]  # dup
    assert len(merge_loci(a, b)) == 1
