"""Candidate fields holding something other than what they are read as.

A revenue candidate is an object a model produced, and the gates around it
read its fields as text, as a figure or as a list of labels. A reply is free
text, so any field may hold any JSON type, and the `or` that guarded these
reads catches an absent field without catching one that holds an array or an
object.

Each shape below ends a job somewhere different - `.strip()` in the verbatim
gate, `.lower()` in the hard vetoes, a format spec in the value check, an
unhashable span id used as a dict key - which is why the reading is one
function rather than a guard per call site.

A field of the wrong type now reads as absent, so the gates reach the verdict
they already reach for a candidate missing that field: no quote is no
evidence, and no scope is an unknown scope. A candidate that states its fields
is untouched, which is the half of this that a system dropping everything
would also pass.

Invented names: Calderon, NuVessa.
"""

from __future__ import annotations

import pytest

from app.domain.claims import stated_labels, stated_number, stated_text
from app.llm.client import apply_judge_hard_vetoes
from app.llm.grounding import (
    apply_structured_field_gates,
    enforce_verbatim_on_candidates,
)
from app.quality.candidate_filters import filter_revenue_candidates
from app.quality.checks import quote_contains_value
from app.quality.fast_judge import try_deterministic_judgment

QUOTE = "Calderon net sales were $483.3 million"
SPANS = [{"span_id": "s1", "span_text": QUOTE}]


def _candidate(**overrides):
    return {
        "period": "2021Q4", "period_type": "quarterly",
        "revenue_scope": "Product family", "value_reported": 483.3,
        "currency": "USD", "unit": "millions", "source_quote": QUOTE,
        "span_id": "s1", **overrides,
    }


# One of each JSON type a field may hold instead of the type it is read as.
NOT_TEXT = ([QUOTE], {"value": QUOTE}, 7, True)


def test_stated_text_takes_text_and_nothing_else():
    assert stated_text("Calderon") == "Calderon"
    assert stated_text("  Calderon  ") == "Calderon"
    assert [stated_text(v, "D") for v in NOT_TEXT] == ["D", "D", "D", "D"]
    assert stated_text("   ", "D") == "D", "blank is as absent as missing"


def test_stated_number_takes_a_figure_however_it_was_quoted():
    assert stated_number(483.3) == 483.3
    assert stated_number("483.3") == 483.3, "a figure quoted as text is that figure"
    assert stated_number("1,234") == 1234.0
    assert [stated_number(v) for v in (["483.3"], {"v": 1}, "x", None)] == [None] * 4
    assert stated_number(True) is None, "a bool is not a figure, though Python counts one"


def test_stated_labels_reads_a_sequence_and_a_lone_label():
    assert stated_labels(["combined_line", "partial_period"]) == ["combined_line", "partial_period"]
    assert stated_labels("partial_period") == ["partial_period"], "one label, not its characters"
    assert [stated_labels(v) for v in (7, {"partial_period": 1}, None)] == [[], [], []]


@pytest.mark.parametrize("field", ["source_quote", "revenue_scope", "span_id", "period_type"])
def test_the_gates_reach_a_verdict_whatever_type_a_field_holds(field):
    for wrong in NOT_TEXT:
        cand = _candidate(**{field: wrong})
        kept, _ = enforce_verbatim_on_candidates([cand], source_text=QUOTE, spans=SPANS)
        apply_structured_field_gates([cand])
        filter_revenue_candidates([cand], product="Calderon", source_text=QUOTE)
        apply_judge_hard_vetoes(
            product="Calderon", candidate=cand, quote=QUOTE, judgment={"issues": []},
        )
        try_deterministic_judgment(
            product="Calderon", generic=None, candidate=cand, quote=QUOTE,
        )
        if field == "source_quote":
            assert kept == [], "a quote that is not text is no quote to ground against"


def test_a_quote_that_is_not_text_is_dropped_for_having_no_quote():
    _, dropped = filter_revenue_candidates(
        [_candidate(source_quote=["Calderon net sales"])],
        product="Calderon", source_text=QUOTE,
    )
    assert [d["_drop_reason"] for d in dropped] == ["missing_quote"]


def test_a_scope_that_is_not_text_is_an_unknown_scope_not_a_product_one():
    kept, dropped = filter_revenue_candidates(
        [_candidate(revenue_scope={"scope": "Product family"},
                    source_quote="NuVessa net sales were $12.0 million")],
        product="Calderon", source_text="NuVessa net sales were $12.0 million",
    )
    assert kept == [], "the quote names another product, and no scope excuses it"
    assert dropped, "it is dropped, not passed through as an unreadable scope"


def test_a_value_that_is_not_a_figure_is_not_found_in_the_quote():
    assert quote_contains_value(QUOTE, 483.3) is True
    assert quote_contains_value(QUOTE, "483.3") is True, "quoted as text, still the figure"
    for wrong in (["483.3"], {"v": 483.3}, "four hundred"):
        assert quote_contains_value(QUOTE, wrong) is False


def test_a_candidate_that_states_its_fields_is_untouched():
    kept, dropped = enforce_verbatim_on_candidates(
        [_candidate()], source_text=QUOTE, spans=SPANS,
    )
    assert dropped == []
    assert kept[0]["_grounded_span_id"] == "s1", "the span id still keys the span"

    kept, dropped = filter_revenue_candidates(
        [_candidate()], product="Calderon", source_text=QUOTE,
    )
    assert [c["value_reported"] for c in kept] == [483.3]
    assert dropped == []
