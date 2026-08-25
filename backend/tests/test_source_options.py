"""Retrieval must honour sec_filings and earnings_releases independently.

Primary 10-K/10-Q documents are large inline-XBRL filings holding annual totals, while
8-K exhibit 99.x releases are small and hold the quarterly product breakouts, so a
caller after quarterly revenue needs to be able to ask for exhibits alone.
"""

import inspect
import json
from datetime import date

from app.connectors.sources import SECConnector, parse_filing_date
from app.domain.models import ExtractionOptions
from app.main import _create_run
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


def test_table_reading_is_not_limited_by_the_llm_source_budget():
    """Table reading costs nothing, so llm_max_extract_sources must not truncate it."""
    source = inspect.getsource(PipelineOrchestrator._extract_revenue)
    assert "llm_source_ids" in source
    assert "use_llm = src.source_id in llm_source_ids" in source
    # The LLM call is conditional, while table extraction runs for every source
    assert "if use_llm:" in source
    llm_call_index = source.index("self.llm.extract_revenue")
    table_call_index = source.index("extract_revenue_rows")
    assert table_call_index > llm_call_index
    assert "over_source_budget" in source


def test_options_with_a_date_window_are_json_storable():
    """options_json is a JSON column, so a date-bearing option must serialise."""
    options = ExtractionOptions(earnings_since="2024-04-01", earnings_until="2025-03-01")
    payload = options.model_dump(mode="json")
    assert json.dumps(payload)
    assert payload["earnings_since"] == "2024-04-01"
    # and the orchestrator's parser round-trips the stored string
    assert parse_filing_date(payload["earnings_until"]) == date(2025, 3, 1)


def test_run_creation_serialises_options_for_the_json_column():
    assert 'options.model_dump(mode="json")' in inspect.getsource(_create_run)


def test_earnings_release_satisfies_the_filing_check_for_search_fallback():
    """An exhibit-only run already has issuer filings, so no LLM search fallback."""
    source = inspect.getsource(PipelineOrchestrator._retrieve)
    marker = source.split("sec_ok = any(", 1)[1].split(")", 1)[0]
    assert "EARNINGS_RELEASE" in marker
    assert "SEC_FILING" in marker
