"""A row label the reader cannot account for is a question, not a formulation.

Every label below names the product. What the reader must tell apart is the
product's own row, a region of it, a combined line over it and its siblings,
a total, and a label whose other words it cannot place - an inventory line,
an accrual, a cost. The last used to be read as a "formulation" of the
product and published; now it is held with its residue for a person.

The footnote a label cites is read as part of the label: "(1) includes
Nebulized Calderon" makes the line combined, "(1) for the period between the
acquisition date and quarter end" makes the figure a partial period.

Invented names throughout: Calderon, Calderon XR, Nebulized Calderon, NuVessa.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from app.extraction.candidates import extract_revenue_candidates
from app.extraction.extract import read_table
from app.parsing.documents import html_table_grid, table_footnotes
from app.parsing.labels import read_footnote, read_label
from app.quality.fast_judge import try_deterministic_judgment

PRODUCTS = ["Calderon", "Calderon XR", "Nebulized Calderon", "NuVessa"]
HEADER = [["($ in millions)", "Three Months Ended June 30,", ""], ["", "2024", "2023"]]


def _read(rows, product="Calderon", **kw):
    return read_table(HEADER + rows, product=product, products=PRODUCTS, **kw)


# --- the label alone ---------------------------------------------------------

def test_the_products_own_row_has_no_residue():
    reading = read_label("Calderon ® (1)", ["Calderon"], products=PRODUCTS)
    assert reading.whole and reading.residue == "" and reading.marks == ("1",)


def test_a_geography_is_a_scope_not_a_formulation():
    for label, scope in [
        ("Calderon - U.S.", "United States"),
        ("Calderon - Global", "Worldwide"),
        ("Calderon - Other International", "Rest of world"),
        ("Calderon Ex-U.S.", "International"),
        ("Calderon - Other", "Other"),
    ]:
        reading = read_label(label, ["Calderon"], products=PRODUCTS)
        assert reading.scope == scope, label
        assert reading.residue == "", label


def test_a_line_over_several_named_products_is_combined():
    reading = read_label("Calderon / Calderon XR / NuVessa", ["Calderon"], products=PRODUCTS)
    assert reading.combined_with == ("Calderon XR", "NuVessa")
    assert "combined_line" in reading.flags and reading.residue == ""


def test_an_and_joined_unknown_name_is_not_understood():
    reading = read_label("Calderon and Veltrexa", ["Calderon"], products=PRODUCTS)
    assert reading.residue == "veltrexa" and "label_not_understood" in reading.flags


def test_words_around_the_name_are_the_residue():
    for label, residue in [
        ("Calderon: Raw materials", "raw materials"),
        ("Accrual for settlement related to calderinol litigation (1)",
         "accrual settlement related litigation"),
        ("Alliance Revenue - Calderon", "alliance"),
    ]:
        reading = read_label(label, ["Calderon", "calderinol"], products=PRODUCTS)
        assert reading.residue == residue, label
        assert "label_not_understood" in reading.flags


def test_a_sibling_products_row_names_no_product_of_ours():
    """Calderon XR does not resolve to Calderon; Nebulized Calderon neither."""
    for label in ("Calderon XR", "Nebulized Calderon"):
        assert not read_label(label, ["Calderon"], products=PRODUCTS).names_product


def test_a_generic_named_family_row_is_the_brands():
    reading = read_label("Tiopronin products", ["Thiola", "tiopronin"], products=PRODUCTS)
    assert reading.whole


# --- the footnote ------------------------------------------------------------

def test_a_footnote_naming_a_sibling_makes_the_line_combined():
    note = read_footnote("includes Nebulized Calderon", ["Calderon"], products=PRODUCTS)
    assert note.names == ("Nebulized Calderon",) and note.flags == ()


def test_an_acquisition_footnote_is_a_partial_period_and_a_launch_is_not():
    note = read_footnote(
        "net product revenue is for the period between January 24, 2023 "
        "(date of acquisition) and March 31, 2023", ["Calderon"])
    assert note.flags == ("partial_period",)
    note = read_footnote(
        "net product revenue is for the period between March 13, 2024 "
        "(date of commercial launch) and March 31, 2024", ["Calderon"])
    assert note.flags == ()


def test_a_footnote_about_one_column_says_nothing_about_the_others():
    """"+ ... for the six months ended June 30, 2023 is for the period between
    the acquisition date and June 30" qualifies the six-month figure. The
    quarter beside it is the quarter's own, is not flagged, and does not
    carry the note's "six months ended" into its quote, where the
    year-to-date veto would read it."""
    note = read_footnote(
        "net product revenue for the six months ended June 30, 2023 is for the "
        "period between January 24, 2023 (date of acquisition) and June 30, 2023",
        ["Calderon"])
    assert note.months == 6 and note.applies_to(6, "2023") and not note.applies_to(3, "2023Q2")
    rows = [
        ["", "Three Months Ended June 30,", "", "Six Months Ended June 30,", ""],
        ["", "2023", "2022", "2023", "2022"],
        ["Calderon+", "34.6", "—", "62.4", "—"],
    ]
    readout = read_table(rows, product="Calderon", context="(in millions)",
                         footnotes=["+ " + "Calderon net product revenue for the six months ended "
                                    "June 30, 2023 is for the period between January 24, 2023 "
                                    "(date of acquisition) and June 30, 2023"])
    by_period = {v.period: v for v in readout.values if v.value_as_reported}
    assert "partial_period" not in by_period["2023Q2"].flags
    assert "six months" not in by_period["2023Q2"].source_quote
    assert "partial_period" in by_period["2023"].flags
    assert "six months" in by_period["2023"].source_quote


def test_a_footnote_naming_a_period_qualifies_that_period_only():
    note = read_footnote("For Q1 2023, represents product revenue, net from the date "
                         "of acquisition of the product rights.", ["Calderon"])
    assert note.period == "2023Q1"
    assert note.applies_to(3, "2023Q1") and not note.applies_to(3, "2024Q1")


def test_a_note_saying_the_product_had_no_sales_makes_the_line_someone_elses():
    rows = [["Calderon and NuVessa (1)", "19.3", "16.1"]]
    notes = ["(1) There were no sales of NuVessa in the quarter, as promotion moved to Calderon"]
    for_nuvessa = _read(rows, product="NuVessa", footnotes=notes)
    assert for_nuvessa.values == []
    assert "footnote_says_no_sales" in (for_nuvessa.skipped_reason or "")
    for_calderon = _read(rows, footnotes=notes)
    assert for_calderon.values and all(v.combined_with == () for v in for_calderon.values), (
        "the line is Calderon's alone once the note says the other sold nothing"
    )


def test_a_question_does_not_silence_the_sentence_that_answers():
    prose = "Calderon net product sales were $25.8 million for the three months ended June 30, 2024."
    candidates, _f, _s = extract_revenue_candidates(
        [HEADER + [["Calderon and Veltrexa", "25.8", "15.9"]]], product="Calderon",
        products=PRODUCTS, prose=prose)
    by_method = {c["extraction_method"]: c for c in candidates if c["period"] == "2024Q2"}
    assert "prose_sentence" in by_method, "the sentence stated the quarter; the question did not"
    assert by_method["prose_sentence"]["revenue_scope"] == "Product family"


def test_footnotes_belong_to_the_table_above_them():
    markup = """
    <table><tr><td>Calderon (1)</td><td>10</td></tr></table>
    <p>(1) includes Nebulized Calderon</p>
    <p>* for the period between the acquisition date and quarter end</p>
    <table><tr><td>NuVessa</td><td>5</td></tr></table>
    <p>(1) a note about the second table</p>
    """
    first, second = BeautifulSoup(markup, "lxml").find_all("table")
    assert table_footnotes(first) == [
        "(1) includes Nebulized Calderon",
        "* for the period between the acquisition date and quarter end",
    ]
    assert table_footnotes(second) == ["(1) a note about the second table"]
    assert html_table_grid(first)


# --- the table read as a whole -----------------------------------------------

def test_an_inventory_line_is_held_with_its_residue_not_published_as_a_formulation():
    readout = _read([["Calderon: Raw materials", "12.0", "9.0"]])
    assert [v.flags for v in readout.values] == [("label_not_understood",)] * 2
    assert readout.values[0].residue == "raw materials"
    candidates, _findings, _skipped = extract_revenue_candidates(
        [HEADER + [["Calderon: Raw materials", "12.0", "9.0"]]], product="Calderon", products=PRODUCTS)
    assert all(c["revenue_scope"] == "Unknown" for c in candidates)
    assert all("label_not_understood" in c["label_flags"] for c in candidates)


def test_a_question_is_not_asked_beside_the_answer():
    readout = _read([["Calderon", "100.0", "90.0"], ["Calderon: Raw materials", "12.0", "9.0"]])
    assert {(v.product_label, v.value_as_reported) for v in readout.values} == {
        ("Calderon", 100.0), ("Calderon", 90.0),
    }


def test_a_combined_line_is_the_familys_figure_and_a_question_for_the_product():
    """"Calderon / Calderon XR / NuVessa" states the family's revenue. Which
    part is Calderon's is not on the page, so the row is published flagged,
    for a person, and never auto-passed as Calderon's own."""
    readout = _read([["Calderon / Calderon XR / NuVessa", "100.0", "90.0"]])
    assert readout.values and all(
        v.combined_with == ("Calderon XR", "NuVessa") and "combined_line" in v.flags
        for v in readout.values
    )
    candidates, _f, _s = extract_revenue_candidates(
        [HEADER + [["Calderon / Calderon XR / NuVessa", "100.0", "90.0"]]],
        product="Calderon", products=PRODUCTS)
    assert candidates[0]["revenue_scope"] == "Product family"
    assert candidates[0]["combined_with"] == ["Calderon XR", "NuVessa"]
    assert "combined_line" in candidates[0]["label_flags"]
    held = try_deterministic_judgment(
        product="Calderon", generic=None,
        candidate={"period": "2024Q2", "value_reported": 100.0, "period_type": "quarterly",
                   "revenue_scope": "Product family", "label_flags": ["combined_line"]},
        quote="Calderon / Calderon XR / NuVessa 100.0 90.0",
    )
    assert held and held["validation_status"] == "needs_review"


def test_a_combined_line_does_not_silence_the_products_own_row():
    readout = _read([["Calderon", "60.0", "50.0"], ["Calderon / Calderon XR / NuVessa", "100.0", "90.0"]])
    assert {(v.value_as_reported, v.flags) for v in readout.values} == {(60.0, ()), (50.0, ())}


def test_a_no_sales_note_naming_a_period_gives_that_column_alone_away():
    """"(1) no sales of NuVessa in Q2 2024" under a line combining both.

    For Q2 2024 the line is Calderon's alone; the prior-year column beside
    it is still both products', so it stays a combined line for either.
    """
    rows = [["Calderon and NuVessa (1)", "19.3", "16.1"]]
    notes = ["(1) There were no sales of NuVessa in Q2 2024 as promotion moved to Calderon"]
    for_calderon = {v.period: v for v in _read(rows, footnotes=notes).values}
    assert for_calderon["2024Q2"].combined_with == () and "combined_line" not in for_calderon["2024Q2"].flags
    assert for_calderon["2023Q2"].combined_with == ("NuVessa",) and "combined_line" in for_calderon["2023Q2"].flags
    for_nuvessa = _read(rows, product="NuVessa", footnotes=notes)
    assert [v.period for v in for_nuvessa.values] == ["2023Q2"], "nothing for the quarter it sold nothing in"
    assert "combined_line" in for_nuvessa.values[0].flags
    assert "footnote_says_no_sales" in (for_nuvessa.skipped_reason or "")


def test_a_family_line_whose_footnote_includes_the_sibling_is_not_the_siblings():
    rows = [["Calderon (1)", "100.0", "90.0"]]
    notes = ["(1) includes Nebulized Calderon"]
    family = _read(rows, footnotes=notes)
    assert family.values and all(v.combined_with == ("Nebulized Calderon",) for v in family.values)
    # Asked for the sibling, with the family name among its aliases (as an
    # alias expander would supply it), the line is still not its own.
    sibling = _read(rows, product="Nebulized Calderon", extra_aliases=["Calderon"], footnotes=notes)
    assert sibling.values == []
    assert "family_line_includes_product" in (sibling.skipped_reason or "")


def test_a_partial_period_footnote_holds_the_figure():
    rows = [["Calderon*", "27.8", "—"]]
    notes = ["* Calderon revenues are for the approximately 2 months that we owned the U.S. rights"]
    readout = _read(rows, footnotes=notes)
    assert readout.values and all("partial_period" in v.flags for v in readout.values)
    assert "we owned" in readout.values[0].source_quote
    candidates, _f, _s = extract_revenue_candidates(
        [HEADER + rows], product="Calderon", products=PRODUCTS, footnotes=[notes])
    verdict = try_deterministic_judgment(
        product="Calderon", generic=None, candidate=candidates[0],
        quote=candidates[0]["source_quote"])
    assert verdict["validation_status"] == "needs_review"
    assert "deterministic:partial_period" in verdict["issues"]


def test_a_global_line_is_the_family_and_reconciles_with_a_tagged_twin():
    candidates, _f, _s = extract_revenue_candidates(
        [HEADER + [["Calderon - Global", "100.0", "90.0"]]], product="Calderon", products=PRODUCTS)
    assert candidates[0]["revenue_scope"] == "Product family"
    assert candidates[0]["formulation"] is None


def test_region_rows_take_their_region_and_the_labelled_total_is_the_product():
    rows = [
        ["Calderon - U.S.", "60.0", "50.0"],
        ["Calderon - Ex-U.S.", "40.0", "40.0"],
        ["Total Calderon", "100.0", "90.0"],
    ]
    readout = _read(rows)
    assert {(v.product_label, v.value_as_reported, v.scope) for v in readout.values} == {
        ("Total Calderon", 100.0, None), ("Total Calderon", 90.0, None),
    }


def test_region_rows_without_a_total_publish_in_their_own_scope():
    candidates, _f, _s = extract_revenue_candidates(
        [HEADER + [["Calderon - U.S.", "60.0", "50.0"], ["Calderon - Europe", "40.0", "40.0"]]],
        product="Calderon", products=PRODUCTS)
    assert {(c["revenue_scope"], c["geography"]) for c in candidates} == {
        ("U.S.", None), ("Regional", "Europe"),
    }
    assert all("region_rows_no_total" in c["label_flags"] for c in candidates)


def test_a_marker_in_its_own_span_still_begins_the_note():
    """A filer prints "(1)" and the sentence as two runs of one paragraph.

    Read a run at a time, the marker alone matched nothing and the sentence
    after it began with a letter, so the table had no notes and the line the
    note gave away was published to the product it said sold nothing.
    """
    markup = """
    <table><tr><td>Calderon and NuVessa (1)</td><td>19.3</td></tr></table>
    <div><span>______________</span></div>
    <div><span>(1)</span><span>There were no sales of NuVessa in the quarter.</span></div>
    <div><span>In the quarter all revenue was recognised at a point in time.</span></div>
    <div><span>(2) a note about nothing in this table</span></div>
    """
    table = BeautifulSoup(markup, "lxml").find("table")
    assert table_footnotes(table) == ["(1) There were no sales of NuVessa in the quarter."], (
        "the note is its paragraph; the prose after the notes is not the note"
    )


def test_a_marker_alone_in_its_paragraph_takes_the_next_one():
    markup = """
    <table><tr><td>Calderon (1)</td><td>19.3</td></tr></table>
    <div><div>(1)</div><div>includes Nebulized Calderon</div></div>
    """
    table = BeautifulSoup(markup, "lxml").find("table")
    assert table_footnotes(table) == ["(1) includes Nebulized Calderon"]


def test_every_question_flag_reaches_the_judge():
    """The flags that make a row a question travel to the judge as label
    flags; one left out of that set is read as an answer there, whatever
    the reader said."""
    from app.extraction.extract import QUESTION_FLAGS
    from app.pipeline.orchestrator import LABEL_FLAGS

    assert QUESTION_FLAGS <= LABEL_FLAGS
