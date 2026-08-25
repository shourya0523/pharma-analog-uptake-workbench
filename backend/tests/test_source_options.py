"""Retrieval must honour sec_filings and earnings_releases independently.

Primary 10-K/10-Q documents are large inline-XBRL filings holding annual totals, while
8-K exhibit 99.x releases are small and hold the quarterly product breakouts, so a
caller after quarterly revenue needs to be able to ask for exhibits alone.
"""

import inspect
from datetime import date

from app.connectors.sources import SECConnector, parse_filing_date
from app.domain.models import ExtractionOptions
from app.pipeline.orchestrator import PipelineOrchestrator


def test_extraction_options_expose_earnings_releases():
    options = ExtractionOptions()
    assert options.earnings_releases is True
    assert options.sec_filings is True
    assert ExtractionOptions(sec_filings=False).earnings_releases is True


def test_sec_retrieve_accepts_independent_primary_and_earnings_switches():
    params = inspect.signature(SECConnector.retrieve).parameters
    assert params["include_primary"].default is True
    # None defers to the sec_earnings_exhibits setting
    assert params["include_earnings"].default is None


def test_orchestrator_maps_both_options_into_retrieval():
    source = inspect.getsource(PipelineOrchestrator._retrieve)
    assert 'options.get("sec_filings", True)' in source
    assert 'options.get("earnings_releases", True)' in source
    assert "include_primary=want_primary" in source
    assert "include_earnings=want_earnings" in source


def test_parse_filing_date_handles_edgar_and_caller_values():
    assert parse_filing_date("2024-05-01") == date(2024, 5, 1)
    assert parse_filing_date(date(2024, 5, 1)) == date(2024, 5, 1)
    # EDGAR occasionally carries a timestamp suffix
    assert parse_filing_date("2024-05-01T00:00:00") == date(2024, 5, 1)
    assert parse_filing_date(None) is None
    assert parse_filing_date("") is None
    assert parse_filing_date("not-a-date") is None


def test_earnings_window_is_optional_and_plumbed_end_to_end():
    options = ExtractionOptions()
    assert options.earnings_since is None and options.earnings_until is None
    assert ExtractionOptions(earnings_since="2024-01-01").earnings_since == date(2024, 1, 1)

    params = inspect.signature(SECConnector.retrieve).parameters
    assert params["earnings_since"].default is None
    assert params["earnings_until"].default is None

    source = inspect.getsource(PipelineOrchestrator._retrieve)
    assert 'earnings_since=parse_filing_date(options.get("earnings_since"))' in source
    assert 'earnings_until=parse_filing_date(options.get("earnings_until"))' in source

    exhibits = inspect.getsource(SECConnector._retrieve_earnings_exhibits)
    assert "filed_on < since" in exhibits
    assert "filed_on > until" in exhibits


def test_earnings_release_satisfies_the_filing_check_for_search_fallback():
    """An exhibit-only run already has issuer filings, so no LLM search fallback."""
    source = inspect.getsource(PipelineOrchestrator._retrieve)
    marker = source.split("sec_ok = any(", 1)[1].split(")", 1)[0]
    assert "EARNINGS_RELEASE" in marker
    assert "SEC_FILING" in marker
