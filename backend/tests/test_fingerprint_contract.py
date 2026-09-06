"""The fingerprint contract: what the model is shown, and what of its answer is kept.

The parser is grounding, not interpretation: a described row is kept only
when the document prints that label on that row; a described grid only when
it was shown; closed vocabularies are enforced; nothing here reads a
document's meaning.
"""

from __future__ import annotations

from app.domain.models import ParsedDocument, ParsingStatus
from app.fingerprint.llm import (
    GEOGRAPHIES,
    LINE_KINDS,
    STATEMENTS,
    ground_label,
    parse_fingerprint,
    sketch_document,
    squash,
)


def _doc(tables, text="") -> ParsedDocument:
    return ParsedDocument(source_id="t", text_blocks=[text], tables=tables, parsing_status=ParsingStatus.SUCCESS)


GRID = [
    ["Net Revenues (in millions)"],
    ["Quarter Ended September 30, 2025", "% Change"],
    ["U.S.", "Int'l.", "Total", "U.S.", "Int'l.", "Total"],
    ["Immunology", "6,263", "1,622", "7,885", "9.6", "21.7", "11.9"],
    ["Skyrizi", "4,085", "623", "4,708", "47.0", "45.9", "46.8"],
    ["Rinvoq", "1,559", "625", "2,184", "33.3", "40.7", "35.3"],
    ["(1) Products acquired on June 16, 2017"],
]


def test_sketch_shows_every_row_with_its_index_and_marks_unlabelled_rows():
    tables = [GRID, [["Balance sheet"], ["Total assets", "1", "2"]], [["<x>"], ["1,000", "2,000"]]]
    parts = sketch_document(_doc(tables, "Skyrizi net revenues were $4.708 billion."), aliases=["Skyrizi"])
    assert len(parts) == 1
    part = parts[0]
    assert part.grid_indexes == (0,)
    assert "r4: Skyrizi | 4,085 | 623 | 4,708 | 47.0 | 45.9 | 46.8" in part.grids_text
    assert "r6: (1) Products acquired on June 16, 2017" in part.grids_text
    assert "[passage @" in part.prose_text and "$4.708 billion" in part.prose_text
    # A grid nobody would describe is not shown; the balance sheet says nothing about sales.
    assert "Total assets" not in part.grids_text


def test_sketch_splits_long_documents_into_parts_without_splitting_a_grid():
    big = [[f"Product {i}", str(i), str(i + 1)] for i in range(130)]
    tables = [[["Revenues"]] + big + [["Skyrizi", "1", "2"]], GRID]
    parts = sketch_document(_doc(tables), aliases=["Skyrizi"], part_max_chars=400, max_rows_per_part=110)
    assert len(parts) == 2
    assert parts[0].grid_indexes == (0,) and parts[1].grid_indexes == (1,)
    assert all(p.total == 2 for p in parts)
    # A very long product grid shows the product rows in context and marks what it omits.
    assert "omitted" in parts[0].grids_text and "r131: Skyrizi" in parts[0].grids_text


def test_ground_label_gives_the_label_width_and_rejects_a_label_the_row_does_not_print():
    assert ground_label("Skyrizi", GRID[4]) == 1
    assert ground_label("Repatha ®", ["Repatha", "®", "442", "352"]) == 2
    assert ground_label("Repatha", ["Repatha ®", "442"]) == 1
    assert ground_label("", ["1,029", "1,032"]) == 0
    assert ground_label("Rinvoq", GRID[4]) is None
    assert squash("Int’l. Total") == squash("intl.total")


def _payload(rows, **grid_overrides):
    grid = {
        "kind": "grid", "grid_index": 0, "unit": "millions", "unit_source": "caption", "currency": "USD",
        "grid_geography": None,
        "columns": [
            {"kind": "value", "period": "2025Q3", "period_type": "quarterly", "geography": "United States"},
            {"kind": "value", "period": "2025Q3", "period_type": "quarterly", "geography": "International"},
            {"kind": "value", "period": "2025Q3", "period_type": "quarterly", "geography": "Worldwide"},
            {"kind": "change", "geography": "United States"},
            {"kind": "change", "geography": "International"},
            {"kind": "change", "geography": "Worldwide"},
        ],
        "coverage": [],
        "sections": [{"row_index": 3, "heading_as_printed": "Immunology", "kind": "revenue"}],
        "rows": rows,
        "why": "key product revenues",
    }
    grid.update(grid_overrides)
    return {"regions": [grid]}


def test_parser_keeps_rows_the_document_prints_and_rejects_the_rest():
    payload = _payload([
        {"row_index": 4, "label_as_printed": "Skyrizi", "product": "Skyrizi", "geography": None, "line": "own_revenue"},
        {"row_index": 5, "label_as_printed": "Skyrizi", "product": "Skyrizi", "geography": None, "line": "own_revenue"},
        {"row_index": 3, "label_as_printed": "Immunology", "product": "Skyrizi", "geography": None, "line": "expenses"},
    ])
    fp = parse_fingerprint(payload, doc=_doc([GRID]), shown={0})
    assert len(fp.grids) == 1
    rows = fp.grids[0].rows
    assert [r.row_index for r in rows] == [4]
    assert rows[0].label_width == 1 and rows[0].line == "own_revenue"
    assert any("index_adjusted_to_4" in r for r in fp.adjusted) and any("described_twice" in r for r in fp.rejected)
    assert any("line_kind(expenses)" in r for r in fp.rejected)
    assert fp.grids[0].sections[0].heading_as_printed == "Immunology"
    assert [c.geography for c in fp.grids[0].layout.columns] == [
        "United States", "International", "Worldwide", "United States", "International", "Worldwide",
    ]


def test_parser_corrects_a_slipped_row_index_when_the_label_is_unique():
    payload = _payload([
        {"row_index": 3, "label_as_printed": "Skyrizi", "product": "Skyrizi", "geography": None, "line": "own_revenue"},
    ])
    fp = parse_fingerprint(payload, doc=_doc([GRID]), shown={0})
    assert fp.grids[0].rows[0].row_index == 4
    assert any("index_adjusted_to_4" in r for r in fp.adjusted) and not fp.rejected


def test_parser_rejects_grids_that_were_not_shown_and_bad_columns():
    payload = _payload([], grid_index=7)
    fp = parse_fingerprint(payload, doc=_doc([GRID]), shown={0})
    assert not fp.grids and "grid7:not_shown" in fp.rejected
    bad = _payload([], columns=[{"kind": "value", "period": "third quarter", "period_type": "quarterly"}])
    fp = parse_fingerprint(bad, doc=_doc([GRID]), shown={0})
    assert not fp.grids and "grid0:columns_unparseable" in fp.rejected


def test_a_geography_on_every_value_column_becomes_the_grids_geography():
    payload = _payload(
        [{"row_index": 4, "label_as_printed": "Skyrizi", "product": "Skyrizi", "geography": None, "line": "own_revenue"}],
        columns=[
            {"kind": "value", "period": "2025Q3", "period_type": "quarterly", "geography": "Worldwide"},
            {"kind": "value", "period": "2024Q3", "period_type": "quarterly", "geography": "Worldwide"},
            {"kind": "change"},
        ],
    )
    fp = parse_fingerprint(payload, doc=_doc([GRID]), shown={0})
    grid = fp.grids[0]
    assert grid.grid_geography == "Worldwide"
    assert all(c.geography is None for c in grid.layout.columns if c.kind == "value")


def test_prose_statements_and_scopes_are_kept_as_the_model_labelled_them():
    payload = {"regions": [
        {"kind": "prose", "product": "Skyrizi", "statement": "guidance", "scope": "product_own_revenue",
         "period": "2025", "period_type": "annual", "value": 17000, "unit": "millions", "currency": "USD",
         "geography": None, "quote": "we expect Skyrizi revenue of approximately $17 billion", "passage_offset": 12},
        {"kind": "prose", "product": "Skyrizi", "statement": "made_up", "scope": "product_own_revenue",
         "period": "2025Q3", "period_type": "quarterly", "value": 4708, "unit": "millions", "currency": "USD",
         "geography": "Worldwide", "quote": "Global Skyrizi net revenues were $4.708 billion"},
    ]}
    fp = parse_fingerprint(payload)
    assert [p.statement for p in fp.prose] == ["guidance", "other"]
    assert fp.prose[0].passage_offset == 12 and fp.prose[1].geography == "Worldwide"
    assert any("statement(made_up)" in r for r in fp.rejected)


def test_vocabularies_are_closed():
    assert "own_revenue" in LINE_KINDS and "actual" in STATEMENTS and "Other" in GEOGRAPHIES


def test_a_product_named_with_its_generic_is_the_listed_product():
    payload = _payload([
        {"row_index": 4, "label_as_printed": "Skyrizi", "product": "Skyrizi (risankizumab)", "geography": None, "line": "own_revenue"},
    ])
    payload["regions"].append({
        "kind": "prose", "product": "Skyrizi (risankizumab)", "statement": "actual", "scope": "product_own_revenue",
        "period": "2025H1", "period_type": "six_month", "value": 8500, "unit": "millions", "currency": "USD",
        "geography": None, "quote": "Skyrizi net revenues were $8.5 billion for the first half",
    })
    fp = parse_fingerprint(payload, doc=_doc([GRID]), shown={0})
    assert fp.grids[0].rows[0].product == "Skyrizi"
    assert fp.grids_for("Skyrizi", ["Skyrizi", "risankizumab"]) == fp.grids
    assert (fp.prose[0].product, fp.prose[0].period, fp.prose[0].period_type) == ("Skyrizi", "2025Q2", "six_month")
    assert not fp.rejected


def test_a_quarter_label_with_a_longer_period_type_is_the_span_ending_at_that_quarter():
    from app.fingerprint.llm import _period_parts

    assert _period_parts("2026Q2", "quarterly") == (3, 6, 2026)
    assert _period_parts("2026Q2", "six_month") == (6, 6, 2026)
    assert _period_parts("2025Q3", "nine_month") == (9, 9, 2025)
    assert _period_parts("2026Q1", "annual") == (12, 3, 2026), "a fiscal year ending in March"
    assert _period_parts("2025", "nine_month") == (9, 9, 2025)
    assert _period_parts("2026H1", "") == (6, 6, 2026)
