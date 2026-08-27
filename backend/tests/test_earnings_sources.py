import json
from pathlib import Path

from app.connectors.sources import SECConnector, is_earnings_exhibit
from app.domain.models import (
    ParsedDocument,
    ParsingStatus,
    RetrievalStatus,
    RetrievedSource,
    SourceType,
)
from app.parsing.evidence import prioritize_sources_for_revenue

REPO_ROOT = Path(__file__).resolve().parents[2]


def _gold_source_filenames() -> set[str]:
    rows = [
        json.loads(line)
        for line in (REPO_ROOT / "seed" / "gold" / "quarterly_revenue.jsonl").read_text().splitlines()
        if line.strip()
    ]
    return {row["source_url"].rsplit("/", 1)[-1] for row in rows}


def test_earnings_exhibit_matches_every_gold_exhibit_filename():
    """Gold rows cite exhibit 99.x documents under several issuer naming conventions."""
    exhibits = {name for name in _gold_source_filenames() if "ex" in name.lower()}
    assert exhibits, "expected gold rows to cite exhibit documents"
    assert all(is_earnings_exhibit(name) for name in exhibits), sorted(
        name for name in exhibits if not is_earnings_exhibit(name)
    )


def test_earnings_exhibit_rejects_filing_boilerplate():
    # Primary 8-K document, XBRL viewer pages, and filing metadata are not earnings exhibits
    assert not is_earnings_exhibit("uthr-20240501.htm")
    assert not is_earnings_exhibit("R39.htm")
    assert not is_earnings_exhibit("FilingSummary.xml")
    assert not is_earnings_exhibit("0001082554-24-000027-index.html")
    assert not is_earnings_exhibit("ut_lungiconxredxlogo.jpg")
    assert not is_earnings_exhibit("")


def test_earnings_item_is_results_of_operations():
    assert SECConnector.EARNINGS_ITEM == "2.02"


def _source(source_id: str, source_type: SourceType, **kwargs) -> RetrievedSource:
    return RetrievedSource(
        source_id=source_id,
        source_type=source_type,
        url=f"https://www.sec.gov/{source_id}",
        retrieval_status=RetrievalStatus.SUCCESS,
        **kwargs,
    )


def _parsed(*source_ids: str) -> dict[str, ParsedDocument]:
    return {
        sid: ParsedDocument(
            source_id=sid,
            text_blocks=["Total Tyvaso net product sales $372.5 million"],
            parsing_status=ParsingStatus.SUCCESS,
        )
        for sid in source_ids
    }


def test_earnings_releases_outrank_annual_filings_for_revenue():
    tenk = _source("tenk", SourceType.SEC_FILING, filing_type="10-K")
    tenq = _source("tenq", SourceType.SEC_FILING, filing_type="10-Q")
    exhibit = _source("ex991", SourceType.EARNINGS_RELEASE, filing_type="8-K")
    ordered = prioritize_sources_for_revenue(
        [tenk, tenq, exhibit], _parsed("tenk", "tenq", "ex991"), max_sources=3
    )
    assert ordered[0].source_id == "ex991"


def test_earnings_releases_survive_the_primary_source_cut():
    """Two or more 10-K/10-Q filings previously discarded every earnings exhibit."""
    filings = [
        _source("tenk1", SourceType.SEC_FILING, filing_type="10-K"),
        _source("tenk2", SourceType.SEC_FILING, filing_type="10-K"),
        _source("tenq1", SourceType.SEC_FILING, filing_type="10-Q"),
    ]
    exhibits = [
        _source("ex1", SourceType.EARNINGS_RELEASE, filing_type="8-K"),
        _source("ex2", SourceType.EARNINGS_RELEASE, filing_type="8-K"),
    ]
    parsed = _parsed("tenk1", "tenk2", "tenq1", "ex1", "ex2")
    ordered = prioritize_sources_for_revenue(filings + exhibits, parsed, max_sources=4)
    selected = {s.source_id for s in ordered}
    assert {"ex1", "ex2"} <= selected


def test_openfda_still_excluded_from_revenue_sources():
    fda = _source("fda", SourceType.OPENFDA)
    exhibit = _source("ex991", SourceType.EARNINGS_RELEASE, filing_type="8-K")
    ordered = prioritize_sources_for_revenue([fda, exhibit], _parsed("fda", "ex991"), max_sources=4)
    assert [s.source_id for s in ordered] == ["ex991"]
