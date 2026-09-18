"""One fetcher against one host, and it keeps the host's pace.

A rate limit belongs to the endpoint, not to the caller, so two fetchers
against sec.gov - one pacing itself and one not - is one fetcher that does
not pace itself. `fetch_page` was the second one: the page a person or a
model points us at is as often an EDGAR archive URL as anything else.

The pace is the SEC's, and only the SEC's. A refusal from an
investor-relations site must not slow down EDGAR, and EDGAR's own pace must
not be waited out before asking a site that never set it.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.connectors import sources as module
from app.connectors.sources import get_with_backoff, is_sec_host


class _Client:
    """A client with a script of status codes or exceptions, one per call."""

    def __init__(self, script: list[int | Exception]) -> None:
        self.script = list(script)
        self.urls: list[str] = []

    async def get(self, url: str) -> httpx.Response:
        self.urls.append(url)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return httpx.Response(step, request=httpx.Request("GET", url), json={"ok": True})


def test_a_host_is_read_from_the_url_not_found_in_its_text():
    assert is_sec_host("https://www.sec.gov/Archives/edgar/data/1/2/x.htm")
    assert is_sec_host("https://data.sec.gov/submissions/CIK0000000001.json")
    assert is_sec_host("HTTPS://WWW.SEC.GOV/x")
    # A host that merely contains the string, and a URL that mentions it.
    assert not is_sec_host("https://sec.gov.example.com/x")
    assert not is_sec_host("https://example.com/redirect?to=www.sec.gov")
    assert not is_sec_host("https://investors.example.com/q1.htm")
    assert not is_sec_host("")


def test_the_pace_is_kept_for_the_sec_and_for_nobody_else(monkeypatch):
    paced: list[str] = []

    async def _throttle():
        paced.append("waited")

    async def _now(*_a, **_k):
        return None

    monkeypatch.setattr(module, "_sec_throttle", _throttle)
    monkeypatch.setattr(asyncio, "sleep", _now)

    client = _Client([200])
    asyncio.run(get_with_backoff(client, "https://www.sec.gov/Archives/x.htm"))
    assert paced == ["waited"]

    paced.clear()
    client = _Client([200])
    asyncio.run(get_with_backoff(client, "https://investors.example.com/q1.htm"))
    assert paced == []


def test_another_host_s_refusal_does_not_slow_the_sec(monkeypatch):
    async def _now(*_a, **_k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _now)
    monkeypatch.setattr(module, "_sec_pace", module._SEC_FLOOR_S)

    before = module.sec_pace()
    client = _Client([503, 503, 200])
    asyncio.run(get_with_backoff(client, "https://investors.example.com/q1.htm"))
    assert client.urls and module.sec_pace() == before, (
        "a site that is not the SEC is retried on the same budget and moves no pace"
    )


def test_a_refused_page_is_retried_rather_than_lost(monkeypatch):
    async def _now(*_a, **_k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _now)
    monkeypatch.setattr(module, "_sec_throttle", _now)

    client = _Client([httpx.ConnectError(""), 503, 200])
    response = asyncio.run(get_with_backoff(client, "https://investors.example.com/q1.htm"))
    assert response.status_code == 200 and len(client.urls) == 3

    # And a budget of nothing spends one attempt and raises what it got.
    client = _Client([httpx.ConnectError("")])
    with pytest.raises(httpx.ConnectError):
        asyncio.run(
            get_with_backoff(client, "https://investors.example.com/q1.htm", budget_s=0)
        )
    assert len(client.urls) == 1
