"""Quarters derived from printed, verified year-to-date figures."""

from __future__ import annotations

from app.retrieval.gemini_search import RevenueAnswer, describe_period
from app.retrieval.printed_derive import derive_from_printed
from app.retrieval.verify import Verification

STATED = {"2025": 312.0, "9M 2025": 229.0, "6M 2025": 147.0, "Q1 2025": 68.0}


def _ask(currencies=None):
    def ask(product, label, issuer):
        parsed = {"value": STATED[label], "unit": "millions", "currency": (currencies or {}).get(label, "CHF"),
                  "source_url": f"https://example.com/{label}", "source_quote": f"Calderon {STATED[label]}"}
        return RevenueAnswer(product, label, "m", "", parsed)
    return ask


def _verify(status="verified", failing=None):
    def verify(parsed, product):
        bad = failing and parsed["source_url"].endswith(failing)
        return Verification("value_absent" if bad else status, parsed["source_url"], True)
    return verify


def test_year_to_date_periods_are_spelled_as_dates():
    assert describe_period("9M 2025")[0].startswith("the nine months ended September 30, 2025")
    assert describe_period("H1 2025")[0].startswith("the six months ended June 30, 2025")


def test_q4_is_the_printed_year_less_the_printed_nine_months():
    result = derive_from_printed("Calderon", "Q4 2025", "Acme", ask=_ask(), verify=_verify())
    assert (result.status, result.value, result.unit, result.method) == ("derived", 83e6, "CHF", "full_year_less_nine_months")


def test_q2_is_six_months_less_q1():
    assert derive_from_printed("Calderon", "Q2 2025", ask=_ask(), verify=_verify()).value == 79e6


def test_an_unverified_input_or_mixed_currencies_are_refused():
    assert derive_from_printed("Calderon", "Q4 2025", ask=_ask(), verify=_verify(failing="9M 2025")).status == "unverified_input"
    mixed = _ask({"9M 2025": "USD"})
    assert derive_from_printed("Calderon", "Q4 2025", ask=mixed, verify=_verify()).status == "inputs_differ"
    assert derive_from_printed("Calderon", "Q1 2025", ask=_ask(), verify=_verify()).status == "missing_input"


def test_a_failed_search_is_a_missing_input_not_a_crash():
    def broken(product, label, issuer):
        raise RuntimeError("server failed")
    result = derive_from_printed("Calderon", "Q4 2025", ask=broken, verify=_verify())
    assert result.status == "missing_input" and "RuntimeError" in result.reason
