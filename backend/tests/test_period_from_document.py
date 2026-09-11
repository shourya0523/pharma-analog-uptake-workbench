"""A schedule that states no period of its own is dated by its document.

The table reader required each table to date itself - "Three Months Ended
September 30, 2005" - and skipped the rest, which is most of them: Pfizer heads
its product schedule by geography and then by year, and Lilly heads its
"First-Quarter" and never writes "months ended" at all.

`detect_period_context` already read the filing's own reporting period, and
handed it only to the model. These tests cover giving it to the reader, and the
one case where it must be refused.
"""

from __future__ import annotations

import pytest

from app.extraction.candidates import extract_revenue_candidates
from app.parsing.periods import PeriodContext

def test_a_table_stating_no_period_is_dated_by_its_document():
    """Lilly's product schedule is headed "First-Quarter", never "months ended".

    A reader that requires the table to date itself skipped it, and 140 of the
    held-out corpus's tables with it. The document says which quarter it covers;
    that is what dates the columns.
    """
    rows = [
        ["(Dollars in millions)", "First-Quarter", ""],
        ["Selected Products", "2025", "2024"],
        ["Mounjaro", "3,841.8", "1,806.5"],
    ]
    candidates, _findings, skips = extract_revenue_candidates(
        [rows],
        product="Mounjaro",
        context="(Dollars in millions)",
        captions=["(Dollars in millions)"],
        period_context=PeriodContext(months=3, month=3, year=2025),
    )
    by_period = {c["period"]: c["value_normalized_usd_millions"] for c in candidates}
    assert by_period.get("2025Q1") == pytest.approx(3841.8, abs=0.1), skips
    assert by_period.get("2024Q1") == pytest.approx(1806.5, abs=0.1), skips


def test_a_repeated_year_refuses_the_document_period():
    """Two bands over one year row means the columns are not divided by time.

    Pfizer heads its schedule WORLDWIDE / UNITED STATES / TOTAL INTERNATIONAL
    and prints 2026 and 2025 under each. Spreading one document period across
    all six would file three different values under the same quarter.
    """
    rows = [
        ["", "WORLDWIDE", "", "UNITED STATES", ""],
        ["(MILLIONS)", "2026", "2025", "2026", "2025"],
        ["Eliquis", "2,000", "1,800", "1,200", "1,100"],
    ]
    candidates, _findings, skips = extract_revenue_candidates(
        [rows],
        product="Eliquis",
        context="(MILLIONS)",
        captions=["(MILLIONS)"],
        period_context=PeriodContext(months=3, month=6, year=2026),
    )
    assert candidates == [], "an ambiguous table must stay unread"
    assert any("period_from_document_ambiguous" in reason for reason in skips), skips
