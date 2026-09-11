"""A model that returns spans as strings must not fail the job.

`find_revenue_spans` asks for objects carrying `span_text`, and every reader
below calls `.get` on what comes back. On a 6-K exhibit
`_filter_hallucinated_spans` raised `AttributeError: 'str' object has no
attribute 'get'` - so a non-mapping element reached it, and a string is the
shape that produces that; the reply itself was not captured. Because
`orchestrator._extract_revenue` does not wrap its
`llm.extract_revenue` call, one oddly-shaped reply failed the whole drug job
rather than that single source.

Found by running the pipeline over non-US filings, which is the only thing that
has ever run this path over non-US filings.
"""

from __future__ import annotations

from app.llm.client import _filter_hallucinated_spans

SOURCE = "Calderon 956 627 52 56 and other text that the filing contains"


def test_a_string_span_is_read_as_one():
    got = _filter_hallucinated_spans(["Calderon 956 627 52 56"], SOURCE)
    assert [s["span_text"] for s in got] == ["Calderon 956 627 52 56"]
    assert got[0]["span_id"] == "s1"


def test_objects_still_work_and_keep_their_fields():
    got = _filter_hallucinated_spans(
        [{"span_id": "x9", "span_text": "Calderon 956 627 52 56", "why_relevant": "sales"}],
        SOURCE,
    )
    assert got[0]["span_id"] == "x9"
    assert got[0]["why_relevant"] == "sales"


def test_a_mixed_list_survives():
    got = _filter_hallucinated_spans(
        ["Calderon 956 627 52 56", {"span_text": "other text that the filing contains"}],
        SOURCE,
    )
    assert len(got) == 2


def test_the_verbatim_rule_still_applies_to_a_string_span():
    """Reading a looser shape must not read a looser claim: a span the source
    does not contain is still dropped."""
    assert _filter_hallucinated_spans(["Calderon 9,999 invented"], SOURCE) == []


def test_nothing_of_any_shape_is_survivable():
    assert _filter_hallucinated_spans([], SOURCE) == []
    assert _filter_hallucinated_spans(None, SOURCE) == []
    assert _filter_hallucinated_spans(["", {"span_text": ""}, {}], SOURCE) == []
