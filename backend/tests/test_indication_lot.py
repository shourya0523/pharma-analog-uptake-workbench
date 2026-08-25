import pytest

from app.domain.models import LineOfTherapy
from app.parsing.indications import classify_approved_lot


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("indicated as first-line therapy for adults with disease X", LineOfTherapy.FIRST_LINE),
        ("for patients after one prior systemic regimen", LineOfTherapy.SECOND_LINE_PLUS),
        ("after at least two prior lines of therapy", LineOfTherapy.THIRD_LINE_PLUS),
        ("for previously treated adults", LineOfTherapy.SUBSEQUENT_UNSPECIFIED),
        ("indicated for treatment of disease X", LineOfTherapy.ALL_LINES_OR_UNSPECIFIED),
    ],
)
def test_explicit_label_language_maps_to_controlled_lot(text, expected):
    result = classify_approved_lot(text)
    assert result.value == expected
    assert result.source_quote == text


def test_label_silence_never_becomes_first_line():
    result = classify_approved_lot("")
    assert result.value == LineOfTherapy.UNRESOLVED


def test_conflicting_line_language_requires_review():
    result = classify_approved_lot("first-line treatment after at least two prior lines")
    assert result.value == LineOfTherapy.UNRESOLVED
    assert result.requires_review is True

