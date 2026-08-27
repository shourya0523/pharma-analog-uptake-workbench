from io import StringIO

import pytest

from app.imports.peak_sales import PeakImportError, read_peak_sales_csv


def test_cited_consensus_import_contract():
    rows = read_peak_sales_csv(
        StringIO(
            "product,estimate_type,value,currency,geography,revenue_scope,as_of_date,source_url\n"
            "Example,consensus,750,USD,Worldwide,Product family,2026-08-01,https://example.test/report\n"
        )
    )
    assert rows[0].estimate_type == "consensus"
    assert rows[0].source_url == "https://example.test/report"


def test_import_rejects_missing_provenance_and_scope():
    with pytest.raises(PeakImportError, match="source_url"):
        read_peak_sales_csv(
            StringIO(
                "product,estimate_type,value,currency,geography,revenue_scope,as_of_date,source_url\n"
                "Example,consensus,750,USD,Worldwide,Product family,2026-08-01,\n"
            )
        )

