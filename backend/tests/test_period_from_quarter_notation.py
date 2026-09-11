"""Dating a filing that writes "Q2 2024" instead of "three months ended...".

"Three months ended June 30, 2024" is a US convention. Outside it the same span
is written "Q2 2024", and the phrase never appears at all: Novartis, Sanofi and
Novo Nordisk use the quarter form 62, 55 and 59 times in the exhibits they file
and the phrase not once. Every one of their filings therefore had no detectable
period, which is what `fingerprint` refuses on and what left the model reader
guessing the quarter for figures it had already read correctly.

The hard part is not recognising the notation, it is choosing among what a
filing names. The counts below are the real ones.
"""

from __future__ import annotations

from app.parsing.periods import detect_period_context


def _doc(*parts: str) -> str:
    return "\n".join(parts)


def test_the_quarter_form_is_read():
    context = detect_period_context(_doc("Novartis First Quarter 2025", "Q1 2025 " * 4))
    assert (context.months, context.year, context.quarter) == (3, 2025, 1)


def test_the_year_first_form_is_read():
    context = detect_period_context(_doc("2024\nQ2 net sales", "2024 Q2 " * 3))
    assert (context.year, context.quarter) == (2024, 2)


def test_the_spelled_form_is_read():
    context = detect_period_context("third quarter of 2024 " * 3)
    assert (context.year, context.quarter) == (2024, 3)


def test_a_phrase_still_wins_where_a_filing_states_one():
    """This only runs where there was no answer at all, so a filing using both
    is dated by the phrase exactly as before."""
    both = _doc("Three Months Ended September 30, 2024", "Q1 2025 " * 9)
    context = detect_period_context(both)
    assert (context.months, context.year, context.quarter) == (3, 2024, 3)


class TestChoosingAmongWhatTheFilingNames:
    """A document states its own period over and over and refers to others once."""

    def test_a_single_mention_of_a_later_quarter_does_not_date_the_filing(self):
        """Novo Nordisk's Q1 2025 announcement expects a regulatory filing
        "during the first quarter of 2026" - one sentence, one mention, against
        45 for the quarter it is reporting."""
        doc = _doc(
            "Q1 2025 " * 45,
            "Q1 2024 " * 31,
            "expects to file for the first regulatory approval of CagriSema "
            "during the first quarter of 2026.",
        )
        assert (detect_period_context(doc).year, detect_period_context(doc).quarter) == (2025, 1)

    def test_next_years_guidance_does_not_date_a_full_year_release(self):
        """Sanofi's Q4 2024 release names 2024Q4 73 times and 2025Q2 three
        times. Preferring the latest year dated it 2025Q2 - and dated every
        full-year release into the next year, because that is where guidance
        lives."""
        doc = _doc("Q4 2024 " * 73, "Q4 2023 " * 20, "Q2 2025 " * 3, "Q4 2025")
        context = detect_period_context(doc)
        assert (context.year, context.quarter) == (2024, 4)

    def test_the_comparative_does_not_win_when_it_trails(self):
        """Novartis names its own quarter 31 times and the comparative 27."""
        doc = _doc("Q1 2025 " * 31, "Q1 2024 " * 27)
        context = detect_period_context(doc)
        assert (context.year, context.quarter) == (2025, 1)

    def test_a_genuine_tie_goes_to_the_later_period(self):
        doc = _doc("Q1 2024 " * 5, "Q3 2024 " * 5)
        assert detect_period_context(doc).quarter == 3
        doc = _doc("Q2 2023 " * 5, "Q2 2024 " * 5)
        assert detect_period_context(doc).year == 2024


class TestWhatItRefusesToInfer:
    def test_a_fiscal_year_label_is_not_a_calendar_quarter(self):
        """"Q1 FY2026" belongs to a filer whose year is not the calendar one,
        so the quarter number says nothing about which months it covers - and
        the months are exactly what is being inferred."""
        assert detect_period_context("Q1 FY2026 " * 8) is None

    def test_a_half_year_is_used_only_when_no_quarter_is_named(self):
        assert detect_period_context("H1 2024 " * 6).months == 6
        # Named beside a quarter, the quarter is what the filing reports.
        context = detect_period_context(_doc("H1 2024 " * 48, "Q2 2024 " * 32))
        assert (context.months, context.quarter) == (3, 2)

    def test_a_document_naming_no_period_is_still_undated(self):
        assert detect_period_context("Net sales rose strongly across regions.") is None
        assert detect_period_context("") is None
