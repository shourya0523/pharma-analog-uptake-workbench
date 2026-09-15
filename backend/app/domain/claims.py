"""Reading the fields of an object a model produced.

A revenue candidate, a reconciler verdict, a missing period: each arrives as an
object whose fields the code goes on to read as text, as a number, or as a list
of labels. A reply is free text, so any field may hold any JSON type, and the
`or` that guarded these reads catches the field being absent without catching
it holding something else - a quote that is an array, a scope that is an
object, a period_type that is a number.

A field holding the wrong type is no answer, which is what an absent field
already means, so each function here returns what an absent field returns. The
callers need no new branch: a candidate whose quote is not text has no quote,
and the gate that drops a quoteless candidate drops it.

A number is the exception, because a figure quoted as text is still that
figure: `"483.3"` reads as 483.3, where `["483.3"]` and `True` do not.
"""

from __future__ import annotations

from typing import Any


def stated_text(value: Any, default: str = "") -> str:
    """The text a field holds, stripped, or the default where it holds none."""
    if not isinstance(value, str):
        return default
    return value.strip() or default


def stated_number(value: Any) -> float | None:
    """The figure a field holds, or None where it holds none.

    A bool is not a figure, though Python counts one as an int, so a field
    holding `true` reads as no value rather than as 1.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", ""))
        except ValueError:
            return None
    return None


def stated_labels(value: Any) -> list[str]:
    """The labels a field holds, or none where it holds something not a sequence.

    A string is one label rather than its characters, which is the reading that
    `for flag in value` gets wrong quietly instead of loudly.
    """
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [item for item in (stated_text(v) for v in value) if item]
    return []
