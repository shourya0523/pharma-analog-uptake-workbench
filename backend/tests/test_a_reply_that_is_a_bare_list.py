"""A model reply that arrives as an array is still an answer.

`_parse_json_content` is annotated as returning a mapping and did not always
return one: asked for `{"spans": [...]}` a model sometimes answers `[...]`,
`json.loads` handed back a list, and the caller's `.get` raised. That killed
the whole job at whatever stage received it - identity for one product,
extraction for another - rather than costing that one call its answer.
"""

from __future__ import annotations

from app.llm.client import BARE_LIST, _parse_json_content, listed


def test_a_bare_array_is_kept_and_read_back_under_the_name_the_caller_expects():
    parsed = _parse_json_content('[{"span_text": "Calderon revenue was $10.0 million"}]')
    assert parsed[BARE_LIST][0]["span_text"].startswith("Calderon")
    assert listed(parsed, "spans") == parsed[BARE_LIST], (
        "the model answered the question and left off the name"
    )
    assert listed(parsed, "candidates") == parsed[BARE_LIST]


def test_a_keyed_reply_is_read_by_its_key():
    parsed = _parse_json_content('{"spans": [{"span_text": "a"}], "notes": "b"}')
    assert [s["span_text"] for s in listed(parsed, "spans")] == ["a"]
    assert listed(parsed, "candidates") == [], "a key the reply does not carry is empty"


def test_an_array_inside_a_fence_or_beside_prose_is_found():
    assert listed(_parse_json_content('```json\n[{"a": 1}]\n```'), "spans") == [{"a": 1}]
    assert listed(_parse_json_content('Here you go: [{"a": 1}] - hope that helps'), "x") == [{"a": 1}]


def test_a_reply_that_is_no_json_at_all_is_still_a_mapping():
    for content in ("", None, "I could not find anything.", "[unclosed", 7):
        parsed = _parse_json_content(content)
        assert isinstance(parsed, dict)
        assert listed(parsed, "spans") == []
