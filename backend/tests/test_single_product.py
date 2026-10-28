"""Reading a single-product filer's revenue, against invented filings."""

from __future__ import annotations

import datetime as dt

from app.retrieval.single_product import Confirmation, judge, names_the_product, product_line
from app.retrieval.xbrl import QUARTER_DAYS, first_pass, parse_facts
from sec_fixtures import fake_sec, filing, inline

REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
Q = ("2025-04-01", "2025-06-30")
TEXT = "Net product revenue from sales of CALDERON was $40.0 million for the quarter."


def _line(facts):
    contexts, parsed = parse_facts(inline(facts))
    return product_line(contexts, parsed, dt.date(2025, 6, 30), QUARTER_DAYS)


def test_product_member_line_is_taken_over_the_total():
    line, parts, basis = _line([
        (REV, 40e6, *Q, {"srt:ProductOrServiceAxis": "us-gaap:ProductMember"}),
        (REV, 38e6, *Q, {"srt:ProductOrServiceAxis": "us-gaap:ProductMember", "srt:StatementGeographicalAxis": "country:US"}),
        (REV, 5e6, *Q, {"srt:ProductOrServiceAxis": "acme:CollaborationMember"}),
        (REV, 45e6, *Q, {}),
    ])
    assert (basis, line.value) == ("product_member", 40e6)
    assert [p.value for p in parts] == [38e6]


def test_total_revenue_is_the_line_only_without_other_kinds_of_revenue():
    line, _, basis = _line([(REV, 45e6, *Q, {})])
    assert (basis, line.value) == ("total_revenue", 45e6)
    line, _, basis = _line([(REV, 45e6, *Q, {}), ("us-gaap:LicenseRevenue", 5e6, *Q, {})])
    assert (basis, line) == ("no_product_line", None)


def test_names_the_product_accepts_its_forms_only():
    assert names_the_product("CALDERON® (calderon) cream 1%", "Calderon")
    assert names_the_product("Calderon XR", "Calderon")
    assert not names_the_product("NuVessa", "Calderon")


def test_judge_needs_a_verified_quote_and_no_other_product():
    assert judge("Calderon", [{"name": "CALDERON", "quote": TEXT}], TEXT).single
    assert not judge("Calderon", [{"name": "CALDERON", "quote": "invented sentence"}], TEXT).single
    verdict = judge("Calderon", [{"name": "CALDERON", "quote": TEXT}, {"name": "NuVessa", "quote": ""}], TEXT)
    assert not verdict.single and "NuVessa" in verdict.reason


def _single_filing(confirm_single: bool):
    content = inline([(REV, 40e6, *Q, {"srt:ProductOrServiceAxis": "us-gaap:ProductMember"})], text=TEXT)
    get = fake_sec([filing("10-Q", "2025-06-30", content)])
    seen = []

    def confirm(product, passages):
        seen.append(passages)
        return Confirmation(confirm_single, [{"name": "CALDERON", "quote": TEXT, "quote_found": True}])

    return get, confirm, seen


def test_a_confirmed_single_product_filer_yields_its_product_line():
    get, confirm, seen = _single_filing(True)
    answer = first_pass("Calderon", "Q2 2025", "Acme Pharma", get=get, confirm=confirm)
    assert answer.status == "found" and answer.match == "single_product" and answer.value == 40e6
    assert "CALDERON was $40.0 million" in seen[0]


def test_an_unconfirmed_filer_yields_no_value():
    get, confirm, _ = _single_filing(False)
    answer = first_pass("Calderon", "Q2 2025", "Acme Pharma", get=get, confirm=confirm)
    assert answer.status == "multi_product" and answer.value is None


def test_without_a_confirmer_or_an_issuer_the_single_product_reader_is_not_used():
    get, confirm, seen = _single_filing(True)
    assert first_pass("Calderon", "Q2 2025", "Acme Pharma", get=get).status == "no_member"
    assert first_pass("Calderon", "Q2 2025", get=get, confirm=confirm).status == "no_member"
    assert not seen


def test_a_member_carrying_no_revenue_does_not_stop_the_single_product_reader():
    content = inline([
        (REV, 40e6, *Q, {"srt:ProductOrServiceAxis": "us-gaap:ProductMember"}),
        ("acme:MilestoneReceived", 10e6, *Q, {"srt:ProductOrServiceAxis": "acme:CalderonMember"}),
    ], text=TEXT)
    get = fake_sec([filing("10-Q", "2025-06-30", content)])

    def confirm(product, passages):
        return Confirmation(True, [{"name": "CALDERON", "quote": TEXT, "quote_found": True}])

    answer = first_pass("Calderon", "Q2 2025", "Acme Pharma", get=get, confirm=confirm)
    assert answer.status == "found" and answer.match == "single_product" and answer.value == 40e6

    def refuse(product, passages):
        return Confirmation(False, [{"name": "NuVessa", "quote": "", "quote_found": False}])

    assert first_pass("Calderon", "Q2 2025", "Acme Pharma", get=get, confirm=refuse).status == "no_revenue_fact"
