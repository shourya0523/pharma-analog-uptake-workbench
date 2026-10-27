"""The Gemini probe, exercised offline against interactions shaped like the SDK's."""

from __future__ import annotations

import json

from app.retrieval.gemini_search import ask_revenue, build_prompt, parse_interaction, parse_json_answer

ANSWER = {
    "product": "Calderon",
    "period": "Three Months Ended June 30, 2023",
    "value": 412.5,
    "unit": "millions",
    "currency": "USD",
    "geography": "worldwide",
    "source_url": "https://example.com/acme-10q.htm",
    "source_quote": "Calderon 412.5",
    "not_reported_reason": None,
}


def _interaction(text: str) -> dict:
    return {
        "id": "int-1",
        "steps": [
            {"type": "google_search_call", "id": "c1", "arguments": {"queries": ["Calderon revenue Q2 2023"]}},
            {"type": "google_search_result", "call_id": "c1", "result": [{"search_suggestions": "<div/>"}]},
            {
                "type": "model_output",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                        "annotations": [
                            {"type": "url_citation", "url": "https://example.com/acme-10q.htm",
                             "title": "acme.com", "start_index": 0, "end_index": 7},
                            {"type": "word_info"},
                        ],
                    }
                ],
            },
        ],
        "usage": {"total_tokens": 100},
    }


def test_parse_interaction_reads_answer_citations_and_queries():
    text = "```json\n" + json.dumps(ANSWER) + "\n```"
    answer = parse_interaction(_interaction(text), product="Calderon", period="Q2 2023", model="m")

    assert answer.parsed == ANSWER
    assert answer.search_queries == ["Calderon revenue Q2 2023"]
    assert [c.url for c in answer.citations] == ["https://example.com/acme-10q.htm"]
    assert answer.citations[0].cited_text == text[0:7]
    assert answer.interaction_id == "int-1"
    assert answer.usage == {"total_tokens": 100}


def test_parse_json_answer_takes_bare_or_fenced_and_refuses_prose():
    assert parse_json_answer("Here: " + json.dumps(ANSWER) + " done") == ANSWER
    assert parse_json_answer("```\n" + json.dumps(ANSWER) + "\n```") == ANSWER
    assert parse_json_answer("Calderon is not reported separately.") is None
    assert parse_json_answer("{not json}") is None


def test_ask_revenue_sends_google_search_tool_and_the_prompt():
    calls = []

    class Interactions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _interaction(json.dumps(ANSWER))

    class Client:
        interactions = Interactions()

    answer = ask_revenue("Calderon", "Q2 2023", "Acme Pharma", model="test-model", client=Client())

    assert calls[0]["tools"] == [{"type": "google_search"}]
    assert calls[0]["model"] == "test-model"
    assert calls[0]["input"] == build_prompt("Calderon", "Q2 2023", "Acme Pharma")
    assert "that Acme Pharma reported" in calls[0]["input"]
    assert answer.parsed["value"] == 412.5


def test_a_429_raises_quota_exceeded_instead_of_returning():
    import pytest

    from app.retrieval.gemini_search import QuotaExceeded

    class RateLimited(Exception):
        status_code = 429

    class Interactions:
        def create(self, **kwargs):
            raise RateLimited("You exceeded your current quota")

    class Client:
        interactions = Interactions()

    with pytest.raises(QuotaExceeded, match="exceeded your current quota"):
        ask_revenue("Calderon", "Q2 2023", model="m", client=Client())


def test_make_client_retries_overload_but_not_429():
    from app.retrieval.gemini_search import RETRY_STATUSES, make_client

    # The Interactions client keeps its own retry list; the override has to reach it.
    retry = make_client().interactions.sdk_configuration.retry_config
    assert retry.status_codes_override == [str(code) for code in RETRY_STATUSES]
    assert "429" not in retry.status_codes_override
    assert "503" in retry.status_codes_override


def test_calendar_periods_are_spelled_out_as_dates():
    from app.retrieval.gemini_search import describe_period

    assert describe_period("Q2 2020") == ("the three months ended June 30, 2020 (calendar Q2 2020)", True)
    assert describe_period("2024q1")[0].startswith("the three months ended March 31, 2024")
    assert describe_period("CY2010") == ("the twelve months ended December 31, 2010 (calendar year 2010)", True)
    assert describe_period("FY2021") == ("FY2021", False)


def test_the_fiscal_note_rides_only_with_dates():
    dated = build_prompt("Calderon", "Q3 2023")
    assert "September 30, 2023" in dated and "fiscal Q1" in dated
    assert "fiscal Q1" not in build_prompt("Calderon", "FY2023")
