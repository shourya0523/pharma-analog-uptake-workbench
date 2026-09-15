"""A model reply that arrives as an array is still an answer.

`_parse_json_content` is annotated as returning a mapping and did not always
return one: asked for `{"spans": [...]}` a model sometimes answers `[...]`,
`json.loads` handed back a list, and the caller's `.get` raised. That killed
the whole job at whatever stage received it - identity for one product,
extraction for another - rather than costing that one call its answer.

Keeping the array moved the same failure one step along. `listed` returns the
entries as they arrived, which is what a list of aliases needs and what a
caller that reads fields off each entry cannot survive: the entries are
strings, and `.get` raises again. The bare array is also returned under every
key, so strings meant for one question are what the next `listed` call hands
back. `mappings` is `listed` for the callers that need objects.
"""

from __future__ import annotations

from app.llm.client import BARE_LIST, _parse_json_content, listed, mappings


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


def test_a_bare_array_of_strings_is_no_list_of_objects():
    parsed = _parse_json_content('["Calderon net sales were $483.3 million"]')
    assert listed(parsed, "candidates") == ["Calderon net sales were $483.3 million"], (
        "the entries arrive as the model sent them"
    )
    assert mappings(parsed, "candidates") == [], (
        "and none of them is a candidate, which needs a period, a value and a quote"
    )


def test_mappings_keeps_the_objects_and_drops_what_is_not_one():
    parsed = _parse_json_content(
        '{"resolved": [{"winner_id": "a"}, "483.3", 7, null, ["b"]]}'
    )
    assert mappings(parsed, "resolved") == [{"winner_id": "a"}]
    assert all(hasattr(item, "get") for item in mappings(parsed, "resolved"))


def test_mappings_leaves_a_keyed_reply_of_objects_alone():
    parsed = _parse_json_content('{"conflicts": [{"period": "2021Q4"}, {"period": "2022Q1"}]}')
    assert [c["period"] for c in mappings(parsed, "conflicts")] == ["2021Q4", "2022Q1"]
    assert mappings(parsed, "resolved") == [], "a key the reply does not carry is empty"


def test_a_list_of_strings_is_still_read_as_strings_where_that_is_the_answer():
    parsed = _parse_json_content('["Calderon XR", "Nebulized Calderon"]')
    assert listed(parsed, "aliases") == ["Calderon XR", "Nebulized Calderon"], (
        "aliases are strings; mappings is for the callers that read fields"
    )
