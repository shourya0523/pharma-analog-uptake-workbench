"""The whole route order, with every route faked."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.retrieval.derive import Derived
from app.retrieval.gemini_search import QuotaExceeded
from app.retrieval.pipeline import parent_names, quarter_revenue
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


def _no_ir(product, period, issuer):
    return SimpleNamespace(status="no_archive", value=None)


BASE = dict(describe_aliases=lambda product, end: [], parent_brand=lambda product: [], usd=_usd, confirm=None, ir=_no_ir,
            layout=lambda url, product, period: SimpleNamespace(status="not_found"),
            sellers=lambda product, period: SimpleNamespace(status="unknown", sellers=[], evidence=[]))


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

    asked = []

    def earnings(name, period, issuer, **kwargs):
        asked.append(kwargs)
        return SimpleNamespace(status="not_found", readings=[])

    result = quarter_revenue("Calderon", "Q2 2023", "Acme", scope="us", xbrl=_miss, earnings=earnings, search=boom,
                             **BASE)
    assert result.status == "not_found" and [a["route"] for a in result.attempts] == ["xbrl", "earnings"]
    assert asked == [{"scope": "us"}]


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


def _seller(filer, cik="1"):
    return SimpleNamespace(filer=filer, cik=cik, start=None, end=None, note=None)


def test_with_no_issuer_the_ownership_timeline_names_whose_figure_to_read():
    seen = []

    def xbrl(name, period, issuer, **kwargs):
        seen.append(issuer)
        return _xbrl_found(name, period, issuer)
    owners = lambda p, per: SimpleNamespace(status="single", sellers=[_seller("ACME PHARMA (CIK 1)")], evidence=["q"])
    result = quarter_revenue("Calderon", "Q2 2023", xbrl=xbrl, **{**BASE, "sellers": owners})
    assert seen == ["ACME PHARMA (CIK 1)"] and result.issuer == "ACME PHARMA (CIK 1)" and result.status == "found"


def test_a_quarter_in_which_the_product_changed_hands_goes_to_review():
    owners = lambda p, per: SimpleNamespace(status="split", sellers=[_seller("ACME (CIK 1)"), _seller("BETA (CIK 2)", "2")],
                                            evidence=[])
    result = quarter_revenue("Calderon", "Q2 2017", xbrl=_xbrl_found, **{**BASE, "sellers": owners})
    assert result.status == "needs_review" and len(result.attempts[0]["sellers"]) == 2


def test_the_layout_reader_answers_after_the_earnings_reader_declines():
    reading = SimpleNamespace(status="not_found", filer="ACME PHARMA (CIK 1)", url="https://sec.example/ex99.htm",
                              value=None, currency=None, row_quote=None)
    declined = SimpleNamespace(status="not_found", readings=[reading], filer=None, later=False)
    layout = lambda url, product, period: SimpleNamespace(status="accepted", value=954e6, currency="USD", url=url,
                                                          row_label="Calderon")
    result = quarter_revenue("Calderon", "Q4 2007", "Acme Pharma", xbrl=_miss, earnings=lambda *a: declined,
                             **{**BASE, "layout": layout})
    assert (result.route, result.value) == ("layout", 954e6)


def test_the_issuers_own_archive_is_read_only_when_it_files_nothing_with_the_sec():
    calls = []

    def ir(product, period, issuer):
        calls.append(issuer)
        return SimpleNamespace(status="found", value=148e6, currency="CHF", url="https://ir.example/q2.pdf",
                               method="direct", evidence="Calderon 148")
    result = quarter_revenue("Calderon", "Q2 2011", "Beta AG", xbrl=_miss, earnings=_miss, **{**BASE, "ir": ir})
    assert (result.route, result.currency) == ("ir_archive:direct", "CHF") and calls == ["Beta AG"]

    on_sec = SimpleNamespace(status="not_found", filer=None, later=False,
                             readings=[SimpleNamespace(status="not_found", filer="BETA AG (CIK 9)", url="u", value=None,
                                                       currency=None, row_quote=None)])
    calls.clear()
    quarter_revenue("Calderon", "Q2 2011", "Beta AG", xbrl=_miss, earnings=lambda *a: on_sec, allow_search=False,
                    **{**BASE, "ir": ir})
    assert calls == []


def test_booked_by_names_the_filer_whose_figure_is_wanted():
    seen = []

    def xbrl(name, period, issuer, **kwargs):
        seen.append(issuer)
        return _xbrl_found(name, period, issuer)
    quarter_revenue("Calderon", "Q2 2023", "Acme Pharma", booked_by="Beta Bio", xbrl=xbrl, **BASE)
    assert seen == ["Beta Bio"]


def test_a_form_the_issuer_prints_only_under_its_brand_is_read_from_that_line():
    def earnings(name, period, issuer):
        if name == "Calderon":
            return SimpleNamespace(status="found", value=139e6, currency="USD", url="https://x/6k.htm",
                                   scope="Calderon 139", filer="ACME", later=False, readings=[])
        return SimpleNamespace(status="not_found", readings=[])

    base = {**BASE, "parent_brand": parent_names}
    result = quarter_revenue("Calderon XR Pen", "Q2 2023", "Acme", xbrl=_miss, earnings=earnings, allow_search=False,
                             **base)
    assert (result.status, result.match, result.value, result.route) == ("found", "parent_brand", 139e6,
                                                                         "earnings:parent_brand")
    assert [a["route"] for a in result.attempts][-2:] == ["parent_brand", "parent_brand"]


def test_parent_names_are_the_shorter_leading_runs():
    assert parent_names("Calderon XR Pen") == ["Calderon XR", "Calderon"]
    assert parent_names("Calderon") == []


def test_a_combined_member_gives_way_to_the_products_own_printed_line():
    def xbrl(name, period, issuer, **kwargs):
        return SimpleNamespace(status="found", value=1675e6, unit="iso4217:USD", match="combined", candidates=[],
                               concept="c", member="m", filing_url="u", plus_minus=None)

    result = quarter_revenue("Calderon", "Q2 2023", "Acme", xbrl=xbrl, earnings=_earnings_found, **BASE)
    assert result.route == "earnings" and result.corroboration.startswith("xbrl:combined")


def test_an_unsplit_release_line_answers_a_us_question_unless_xbrl_tags_places():
    def earnings(name, period, issuer, **kwargs):
        return SimpleNamespace(status="found", value=161e6, currency="USD", url="u", scope="Calderon 161",
                               later=False, readings=[], geography="no_geographic_split")

    result = quarter_revenue("Calderon", "Q2 2023", "Acme", scope="us", xbrl=_miss, earnings=earnings, **BASE)
    assert (result.status, result.match, result.value) == ("found", "no_geographic_split", 161e6)

    def xbrl(name, period, issuer, **kwargs):
        return SimpleNamespace(status="no_us_part", candidates=[], readings=[], by_geography={"country:DE": 1.0})

    vetoed = quarter_revenue("Calderon", "Q2 2023", "Acme", scope="us", xbrl=xbrl, earnings=earnings, **BASE)
    assert vetoed.status == "not_found"
