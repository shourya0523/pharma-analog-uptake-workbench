"""Guards for product profile fields.

Extraction models answer "not specified" rather than omitting a field, and those
strings were being stored as though they were sourced values.
"""

from __future__ import annotations

import re

MISSING_VALUES = {
    "",
    "-",
    "--",
    "n/a",
    "na",
    "none",
    "null",
    "nil",
    "tbd",
    "unknown",
    "not specified",
    "unspecified",
    "not available",
    "not applicable",
    "not disclosed",
    "not reported",
    "not stated",
    "not found",
    "no data",
    "not provided",
    "not mentioned",
}


def is_missing_value(value: object) -> bool:
    """True when a field value carries no information and should not be stored."""
    if value is None:
        return True
    text = re.sub(r"\s+", " ", str(value)).strip().strip(".").casefold()
    return text in MISSING_VALUES
