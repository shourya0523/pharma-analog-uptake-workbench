"""The fingerprint call asks OpenRouter for a JSON answer with bounded reasoning from a provider that honours it."""

from __future__ import annotations

import json

import httpx
import pytest

from app.llm.client import OpenRouterClient


@pytest.mark.asyncio
async def test_chat_json_sends_reasoning_effort_and_provider_requirements(monkeypatch):
    seen: dict = {}

    async def fake_post(self, url, headers=None, json=None):  # noqa: A002
        seen.update(json)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"regions\": []}"}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = OpenRouterClient()
    client.settings.openrouter_api_key = "test"
    out = await client.chat_json(model="z-ai/glm-5.3-flash", system="s", user="u", max_tokens=50,
                                 temperature=0.0, reasoning_effort="low", require_parameters=True)
    assert out == {"regions": []}
    assert seen["reasoning"] == {"effort": "low"}
    assert seen["provider"] == {"require_parameters": True}
    assert seen["response_format"] == {"type": "json_object"}
    assert json.dumps(seen)  # serialisable payload


@pytest.mark.asyncio
async def test_chat_json_omits_the_optional_fields_by_default(monkeypatch):
    seen: dict = {}

    async def fake_post(self, url, headers=None, json=None):  # noqa: A002
        seen.update(json)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = OpenRouterClient()
    client.settings.openrouter_api_key = "test"
    await client.chat_json(model="m", system="s", user="u")
    assert "reasoning" not in seen and "provider" not in seen


@pytest.mark.asyncio
async def test_chat_json_fills_the_usage_sink_from_the_router_accounting(monkeypatch):
    seen: dict = {}

    async def fake_post(self, url, headers=None, json=None):  # noqa: A002
        seen.update(json)
        body = {"choices": [{"message": {"content": "{\"regions\": []}"}}],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "cost": 0.00017}}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = OpenRouterClient()
    client.settings.openrouter_api_key = "test"
    usage: dict = {}
    await client.chat_json(model="m", system="s", user="u", usage=usage)
    assert seen["usage"] == {"include": True}
    assert usage == {"prompt_tokens": 1200, "completion_tokens": 300, "cost": 0.00017}
