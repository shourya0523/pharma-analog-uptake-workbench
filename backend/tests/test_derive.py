"""Quarter derivation from stated year-to-date figures, against invented filings."""

from __future__ import annotations

from app.retrieval.derive import derive_quarter, xbrl_quarter
from sec_fixtures import fake_sec, filing, inline

REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
CAL = {"srt:ProductOrServiceAxis": "acme:CalderonMember"}


def _year(q4_stated_nine_months: float = 229e6, quarters=(68e6, 80e6, 82e6), full_year: float = 312e6):
    q1, q2, q3 = quarters
    return [
        filing("10-Q", "2025-03-31", inline([(REV, q1, "2025-01-01", "2025-03-31", CAL)])),
        filing("10-Q", "2025-06-30", inline([(REV, q2, "2025-04-01", "2025-06-30", CAL),
                                             (REV, q1 + q2 - 1e6, "2025-01-01", "2025-06-30", CAL)])),
        filing("10-Q", "2025-09-30", inline([(REV, q3, "2025-07-01", "2025-09-30", CAL),
                                             (REV, q4_stated_nine_months, "2025-01-01", "2025-09-30", CAL)])),
        filing("10-K", "2025-12-31", inline([(REV, full_year, "2025-01-01", "2025-12-31", CAL)])),
    ]


def test_q4_is_the_year_less_the_stated_nine_months_not_the_summed_quarters():
    get = fake_sec(_year())
    result = derive_quarter("Calderon", "Q4 2025", "Acme Pharma", get=get)
    assert result.status == "derived" and result.method == "full_year_less_nine_months"
    assert result.value == 83e6  # 312 - 229; the quarters sum to 230
    assert [i["period_end"][:10] for i in result.inputs] == ["2025-12-31", "2025-09-30"]


def test_q4_falls_back_to_summed_quarters_and_says_so():
    filings = _year()
    filings[2]["content"] = inline([(REV, 82e6, "2025-07-01", "2025-09-30", CAL)])  # no nine-month fact
    result = derive_quarter("Calderon", "Q4 2025", "Acme Pharma", get=fake_sec(filings))
    assert result.status == "derived" and result.method == "full_year_less_quarters"
    assert result.value == 82e6


def test_q2_from_six_months_less_q1_when_the_three_months_is_untagged():
    filings = _year()
    filings[1]["content"] = inline([(REV, 147e6, "2025-01-01", "2025-06-30", CAL)])
    result = xbrl_quarter("Calderon", "Q2 2025", "Acme Pharma", get=fake_sec(filings))
    assert result.status == "derived" and result.method == "ytd_less_previous_ytd"
    assert result.value == 79e6


def test_a_tagged_quarter_is_read_not_derived():
    result = xbrl_quarter("Calderon", "Q3 2025", "Acme Pharma", get=fake_sec(_year()))
    assert result.status == "found" and result.value == 82e6


def test_inputs_on_different_lines_are_refused():
    filings = _year()
    other = {"srt:ProductOrServiceAxis": "acme:RespiratoryCalderonMember"}
    filings[3]["content"] = inline([(REV, 312e6, "2025-01-01", "2025-12-31", other)])
    assert derive_quarter("Calderon", "Q4 2025", "Acme Pharma", get=fake_sec(filings)).status == "inputs_differ"


def test_a_shrinking_year_to_date_is_refused_as_negative():
    get = fake_sec(_year(full_year=200e6))
    result = derive_quarter("Calderon", "Q4 2025", "Acme Pharma", get=get)
    assert result.status == "negative" and result.value == -29e6


def test_q1_and_a_missing_10k_are_missing_input():
    get = fake_sec(_year()[:3])
    assert derive_quarter("Calderon", "Q1 2025", "Acme Pharma", get=get).status == "missing_input"
    assert derive_quarter("Calderon", "Q4 2025", "Acme Pharma", get=get).status == "missing_input"


def test_a_year_tagged_at_coarse_precision_is_refused_as_imprecise():
    filings = _year()
    rounded = inline([(REV, 300e6, "2025-01-01", "2025-12-31", CAL)]).replace(b'scale="0"', b'scale="0" decimals="-8"')
    filings[3]["content"] = rounded
    result = derive_quarter("Calderon", "Q4 2025", "Acme Pharma", get=fake_sec(filings))
    assert result.status == "imprecise_input" and result.value == 71e6
