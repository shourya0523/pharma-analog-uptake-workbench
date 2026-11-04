"""The whole route order, with every route faked."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.retrieval.derive import Derived
from app.retrieval.gemini_search import QuotaExceeded
from app.retrieval.pipeline import quarter_revenue
from app.retrieval.verify import Verification


def _miss(*args, **kwargs):
    return SimpleNamespace(status="no_member", candidates=[], readings=[])


def _xbrl_found(name, period, issuer, **kwargs):
    return SimpleNamespace(status="found", value=412.5e6, unit="iso4217:USD", match="exact", concept="us-gaap:Revenues",
                           member="acme:CalderonMember", filing_url="https://sec.example/10q.htm", candidates=[])


def _earnings_found(name, period, issuer):
    return SimpleNamespace(status="found", value=98.0e6, currency="EUR", url="https://sec.example/ex99.htm",
                           scope="Calderon 98.0", later=False, readings=[], filer="ACME")


def _search(parsed):
    return lambda product, period, issuer: SimpleNamespace(parsed=parsed)


def _verify(status, needs_review=False):
    return lambda parsed, product: Verification(status, parsed.get("source_url"), True, needs_review=needs_review,
                                                near_text="calderon 55.0")


def _usd(value, currency, year, quarter):
    return value * {"USD": 1.0, "EUR": 1.1, "CHF": 1.2}[currency]


BASE = dict(describe_aliases=lambda product, end: [], usd=_usd, confirm=None)


def test_xbrl_answers_first_and_the_later_routes_do_not_run():
    def boom(*a, **k):
        raise AssertionError("should not run")
    result = quarter_revenue("Calderon", "Q2 2023", "Acme", xbrl=_xbrl_found, earnings=boom, search=boom, **BASE)
    assert (result.status, result.route, result.value_usd) == ("found", "xbrl", 412.5e6)


def test_earnings_answers_when_xbrl_does_not_and_usd_is_converted():
    result = quarter_revenue("Calderon", "Q2 2023", "Acme", xbrl=_miss, earnings=_earnings_found, **BASE)
    assert (result.route, result.currency, result.value_usd) == ("earnings", "EUR", pytest.approx(107.8e6))
    assert [a["route"] for a in result.attempts] == ["xbrl", "earnings"]


def test_search_is_kept_only_when_verified_and_a_refused_page_needs_review():
    parsed = {"value": 55.0, "unit": "millions", "currency": "USD", "source_url": "https://ir.example/q2.pdf"}
    found = quarter_revenue("Calderon", "Q2 2023", "Acme", xbrl=_miss, earnings=_miss, search=_search(parsed),
                            verify=_verify("verified"), **BASE)
    assert (found.status, found.route, found.value) == ("found", "search", 55e6)

    refused = quarter_revenue("Calderon", "Q2 2023", "Acme", xbrl=_miss, earnings=_miss, search=_search(parsed),
                              verify=_verify("refused_by_host", needs_review=True), **BASE)
    assert (refused.status, refused.value) == ("needs_review", 55e6)

    unverified = quarter_revenue("Calderon", "Q2 2023", "Acme", xbrl=_miss, earnings=_miss, search=_search(parsed),
                                 verify=_verify("value_absent"),
                                 printed=lambda *a: Derived("missing_input", "Calderon", "Q2 2023"), **BASE)
    assert unverified.status == "not_found" and unverified.value is None


def test_printed_years_to_date_answer_last():
    derived = Derived("derived", "Calderon", "Q4 2023", value=83e6, unit="CHF", method="full_year_less_nine_months",
                      inputs=[{"source_url": "https://ir.example/fy.pdf"}])
    result = quarter_revenue("Calderon", "Q4 2023", "Acme", xbrl=_miss, earnings=_miss,
                             search=_search({}), verify=_verify("no_value"), printed=lambda *a: derived, **BASE)
    assert (result.route, result.currency, result.value_usd) == ("printed:full_year_less_nine_months", "CHF",
                                                                 pytest.approx(99.6e6))


def test_a_spent_quota_is_recorded_and_the_next_route_still_runs():
    def spent(*a, **k):
        raise QuotaExceeded("monthly spending cap")
    result = quarter_revenue("Calderon", "Q2 2023", "Acme", xbrl=_miss, earnings=spent, search=_search({}),
                             verify=_verify("no_value"), printed=spent, **BASE)
    assert [a["status"] for a in result.attempts if a["route"] in ("earnings", "printed_years_to_date")] == [
        "quota_exceeded", "quota_exceeded"]
    assert result.status == "not_found"


def test_an_alias_answers_when_the_name_itself_does_not():
    def xbrl(name, period, issuer, **kwargs):
        return _xbrl_found(name, period, issuer) if name == "Calderon" else _miss()
    result = quarter_revenue("Nebulized Calderon", "Q2 2013", "Acme", xbrl=xbrl,
                             **{**BASE, "describe_aliases": lambda p, e: [{"name": "Calderon", "evidence": "NDA1"}]})
    assert result.status == "found" and result.identity == {"name": "Calderon", "evidence": "NDA1"}


def test_a_us_question_skips_the_routes_that_cannot_show_a_us_part():
    def boom(*a, **k):
        raise AssertionError("should not run")
    result = quarter_revenue("Calderon", "Q2 2023", "Acme", scope="us", xbrl=_miss, earnings=boom, search=boom, **BASE)
    assert result.status == "not_found" and [a["route"] for a in result.attempts] == ["xbrl"]


def test_an_alias_check_that_fails_leaves_the_route_its_own_answer():
    def failing(product, end):
        raise RuntimeError("descriptor check failed")
    result = quarter_revenue("Nebulized Calderon", "Q2 2013", "Acme", xbrl=_miss, earnings=_miss, allow_search=False,
                             **{**BASE, "describe_aliases": failing})
    assert result.status == "not_found"
    assert [a["route"] for a in result.attempts] == ["xbrl", "earnings"]
    assert "descriptor check failed" in result.attempts[0]["alias_error"]


def test_a_printed_quarter_within_the_derived_spread_is_the_answer():
    derived = Derived("derived", "Calderon", "Q4 2013", value=52.048e6, unit="iso4217:USD",
                      method="full_year_less_nine_months", inputs=[{"filing_url": "https://sec.example/10k.htm"}],
                      plus_minus=1000.0)
    printed_figure = SimpleNamespace(status="found", value=52.047e6, currency="USD", url="https://sec.example/ex99.htm",
                                     scope="Calderon 52,047", later=False, readings=[], filer="ACME")
    result = quarter_revenue("Calderon", "Q4 2013", "Acme", xbrl=lambda *a, **k: derived,
                             earnings=lambda *a: printed_figure, **BASE)
    assert (result.route, result.value) == ("earnings", 52.047e6)
    assert result.corroboration.startswith("xbrl:full_year_less_nine_months 52,048,000 ± 1,000")

    far = SimpleNamespace(**{**vars(printed_figure), "value": 60e6})
    result = quarter_revenue("Calderon", "Q4 2013", "Acme", xbrl=lambda *a, **k: derived, earnings=lambda *a: far, **BASE)
    assert (result.route, result.value, result.plus_minus) == ("xbrl:full_year_less_nine_months", 52.048e6, 1000.0)
