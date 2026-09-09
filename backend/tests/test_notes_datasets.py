"""The bulk tagged reader, on invented filers.

Every product here is made up. A test that passed because a real brand happened
to be spelled somewhere in the code would be measuring the catalogue rather than
the reader, which is the mistake this repository has already made once.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.extraction.bulk_tagged import candidates_from_notes
from app.parsing.notes_datasets import (
    _period_bounds,
    _segments_to_members,
    load_submissions,
)

SUB = "adsh\tcik\tname\tform\tperiod\tfiled\n"
NUM = "adsh\ttag\tversion\tddate\tqtrs\tuom\tdimh\tiprx\tvalue\tfootnote\tfootlen\tdimn\tcoreg\tdurp\tdatp\tdcml\n"
DIM = "dimhash\tsegments\tsegt\n"

ACC = "0000000001-26-000001"


def _write(tmp: Path, *, subs: list[str], dims: list[str], nums: list[str]) -> Path:
    (tmp / "sub.tsv").write_text(SUB + "".join(subs))
    (tmp / "dim.tsv").write_text(DIM + "".join(dims))
    (tmp / "num.tsv").write_text(NUM + "".join(nums))
    return tmp


def _num(dimh: str, value: str, *, tag="RevenueFromContractWithCustomerExcludingAssessedTax",
         version="us-gaap/2025", ddate="20250630", qtrs="1", uom="USD") -> str:
    return f"{ACC}\t{tag}\t{version}\t{ddate}\t{qtrs}\t{uom}\t{dimh}\t1\t{value}\t\t0\t1\t\t0\t0\t-6\n"


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    return _write(
        tmp_path,
        subs=[f"{ACC}\t9999\tCALDERA THERAPEUTICS\t10-Q\t20250630\t20250801\n"],
        dims=[
            "0xAA\tProductOrService=Fenwick;\t0\n",
            "0xBB\tGeographical=US;ProductOrService=Fenwick;\t0\n",
            "0xCC\tProductOrService=Marlow;\t0\n",
            "0xDD\t\t0\n",
        ],
        nums=[
            _num("0xAA", "4200000"),      # Fenwick, worldwide, one quarter
            _num("0xBB", "3100000"),      # Fenwick, US only
            _num("0xCC", "1700000"),      # a different product
            _num("0xDD", "9900000"),      # no product axis at all
        ],
    )


def test_segments_are_read_as_the_instance_spells_them():
    """DIM truncates the axis names; xbrl.py's rules key on the full spelling."""
    members = _segments_to_members("Geographical=US;ProductOrService=Fenwick;")
    assert members["srt:ProductOrServiceAxis"] == "Fenwick"
    assert members["srt:StatementGeographicalAxis"] == "US"


def test_a_quarter_is_bounded_from_its_end_and_its_length():
    start, end = _period_bounds("20250630", 1)
    assert (start.isoformat(), end.isoformat()) == ("2025-04-01", "2025-06-30")


def test_the_worldwide_fact_is_the_products_revenue(dataset: Path):
    """A fact with no geography on it is the whole product; the US line is not."""
    found, _notes = candidates_from_notes(
        dataset, product="Fenwick", issuer="Caldera", cik=9999, register={}
    )
    assert [c["value_reported"] for c in found] == [4200000.0]
    assert found[0]["period"] == "2025Q2"
    assert found[0]["value_normalized_usd_millions"] == 4.2
    assert found[0]["revenue_scope"] == "Product family"


def test_another_products_fact_is_not_this_products_revenue(dataset: Path):
    found, _notes = candidates_from_notes(
        dataset, product="Marlow", issuer="Caldera", cik=9999, register={}
    )
    assert [c["value_reported"] for c in found] == [1700000.0]


def test_the_citation_names_the_fact_not_a_sentence(dataset: Path):
    found, _ = candidates_from_notes(
        dataset, product="Fenwick", issuer="Caldera", cik=9999, register={}
    )
    citation = found[0]["source_quote"]
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" in citation
    assert "ProductOrServiceAxis=Fenwick" in citation
    assert found[0]["xbrl_accession"] == ACC
    assert ACC.replace("-", "") in found[0]["source_url"]


def test_a_filer_extension_is_not_a_revenue_element(tmp_path: Path):
    """An extension is a measure the taxonomy has no name for, not revenue."""
    root = _write(
        tmp_path,
        subs=[f"{ACC}\t9999\tCALDERA THERAPEUTICS\t10-Q\t20250630\t20250801\n"],
        dims=["0xAA\tProductOrService=Fenwick;\t0\n"],
        nums=[_num("0xAA", "4200000", tag="GrossProfitExcludingOtherRevenue",
                   version="0000000001-26-000001")],
    )
    found, notes = candidates_from_notes(
        root, product="Fenwick", issuer="Caldera", cik=9999, register={}
    )
    assert found == []
    assert notes


def test_a_forecast_is_not_a_report(tmp_path: Path):
    root = _write(
        tmp_path,
        subs=[f"{ACC}\t9999\tCALDERA THERAPEUTICS\t10-Q\t20250630\t20250801\n"],
        dims=["0xEE\tProductOrService=Fenwick;Scenario=Forecast;\t0\n"],
        nums=[_num("0xEE", "5000000")],
    )
    found, _ = candidates_from_notes(
        root, product="Fenwick", issuer="Caldera", cik=9999, register={}
    )
    assert found == []


def test_a_half_year_is_not_a_quarter(tmp_path: Path):
    """qtrs=2 is the year-to-date block printed beside the quarter."""
    root = _write(
        tmp_path,
        subs=[f"{ACC}\t9999\tCALDERA THERAPEUTICS\t10-Q\t20250630\t20250801\n"],
        dims=["0xAA\tProductOrService=Fenwick;\t0\n"],
        nums=[_num("0xAA", "8800000", qtrs="2")],
    )
    found, _ = candidates_from_notes(
        root, product="Fenwick", issuer="Caldera", cik=9999, register={}
    )
    assert found == []


def test_the_same_figure_tagged_twice_is_one_answer(tmp_path: Path):
    """Filers tag a figure in the revenue note and again in the segment table."""
    root = _write(
        tmp_path,
        subs=[f"{ACC}\t9999\tCALDERA THERAPEUTICS\t10-Q\t20250630\t20250801\n"],
        dims=[
            "0xAA\tProductOrService=Fenwick;\t0\n",
            "0xAB\tProductOrService=Fenwick;\t0\n",
        ],
        nums=[_num("0xAA", "4200000"), _num("0xAB", "4200000")],
    )
    found, _ = candidates_from_notes(
        root, product="Fenwick", issuer="Caldera", cik=9999, register={}
    )
    assert len(found) == 1


def test_an_untagged_form_is_not_asked_for(tmp_path: Path):
    """An 8-K earnings exhibit carries no XBRL; asking would report a false gap."""
    root = _write(
        tmp_path,
        subs=[f"{ACC}\t9999\tCALDERA THERAPEUTICS\t8-K\t20250630\t20250801\n"],
        dims=["0xAA\tProductOrService=Fenwick;\t0\n"],
        nums=[_num("0xAA", "4200000")],
    )
    found, notes = candidates_from_notes(
        root, product="Fenwick", issuer="Caldera", cik=9999, register={}
    )
    assert found == []
    assert "no tagged filing" in notes[0]
    assert load_submissions(root, ciks={9999}, forms={"8-K"})


def test_a_member_resolves_without_the_register_having_seen_the_issuer(tmp_path: Path):
    """Structure places a member first, so an unseen filer is not a blank.

    The register covers the issuers somebody has run the builder against. If it
    were consulted first, this reader would work on exactly those and return
    nothing everywhere else.
    """
    root = _write(
        tmp_path,
        subs=[f"{ACC}\t9999\tCALDERA THERAPEUTICS\t10-Q\t20250630\t20250801\n"],
        dims=["0xAA\tRespiratoryProductsFenwick=x;ProductOrService=RespiratoryProductsFenwick;\t0\n"],
        nums=[_num("0xAA", "4200000")],
    )
    found, _ = candidates_from_notes(
        root, product="Fenwick", issuer="Caldera", cik=9999, register={}
    )
    assert [c["value_reported"] for c in found] == [4200000.0]
    assert found[0]["member_resolved_by"] == "suffix"
