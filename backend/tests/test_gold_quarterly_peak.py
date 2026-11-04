"""Gold's peak is the highest rolling four-quarter total, confirmed by two later ones.

The checks use invented products and invented quarters, so they state the rule
rather than any one series in gold: a plateau is a peak, a tie goes to the
earlier window, a series that stops at its high is not a confirmed peak, and an
unreported quarter beside the high blocks the call.
"""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = REPO_ROOT / "scripts" / "build_independent_gold.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_independent_gold", BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BUILDER = load_builder()
START = BUILDER.quarter_number("2015Q1")


def rows(name, values, *, skip=()):
    """One row per quarter from 2015Q1; ``None`` or a skipped offset is unreported."""
    out = []
    for offset, value in enumerate(values):
        if value is None or offset in skip:
            continue
        index = START + offset
        out.append({
            "drug_name": name, "period": BUILDER.quarter_label(index),
            "calendar_year": index // 4, "calendar_quarter": index % 4 + 1,
            "value_normalized_usd_millions": value, "gold_id": f"{name}-{offset}".lower(),
        })
    return out


def peak(values, *, meta=None, skip=()):
    return BUILDER.quarterly_peak("Calderon", rows("Calderon", values, skip=skip), meta or {})


def test_two_later_totals_confirm_the_highest_one():
    result = peak([10, 20, 30, 40, 50, 60, 20, 20])
    assert result["peak_status"] == "observed"
    assert result["peak_window"] == "2015Q3-2016Q2"
    assert result["peak_value"] == 30 + 40 + 50 + 60
    assert result["quarters_after_peak"] == 2


def test_one_later_total_on_a_live_series_is_not_yet_a_peak():
    result = peak([10, 20, 30, 40, 50, 60, 55])
    assert result["peak_status"] == "not_yet_observed"
    assert result["peak_value"] is None


def test_a_plateau_is_a_peak_even_with_only_a_slight_decline():
    result = peak([10, 20, 30, 40, 40, 40, 40, 39.9, 39.9])
    assert result["peak_status"] == "observed"


def test_equal_totals_go_to_the_earlier_window_and_do_not_block_the_call():
    result = peak([10, 30, 30, 30, 30, 10, 30, 30, 30, 30, 0, 0])
    assert result["peak_window"] == "2015Q2-2016Q1"
    assert result["peak_status"] == "observed"


def test_a_series_that_stops_at_its_high_is_a_benchmark_not_a_confirmed_peak():
    result = peak([10, 20, 30, 40, 50, 60], meta={"series_end_quarter": "2016Q2"})
    assert result["peak_status"] == "line_ended_at_high"
    assert result["peak_value"] == 30 + 40 + 50 + 60
    assert result["numeric_peak_available"]


def test_the_same_shape_on_a_live_series_is_still_growing():
    assert peak([10, 20, 30, 40, 50, 60])["peak_status"] == "not_yet_observed"


def test_an_unreported_quarter_just_after_the_high_blocks_the_call():
    """The missing quarter could have made a higher window."""
    result = peak([10, 20, 30, 40, 50, 60, None, 30, 30, 30, 30, 30])
    assert result["peak_status"] == "not_yet_observed"
    assert result["selection_method"] == "maximum_beside_an_unreported_quarter"


def test_an_unreported_quarter_just_before_the_window_blocks_the_call():
    result = peak([10, 20, None, 40, 50, 60, 70, 30, 30, 30])
    assert result["peak_status"] == "not_yet_observed"
    assert result["selection_method"] == "maximum_beside_an_unreported_quarter"


def test_a_series_with_no_complete_window_has_no_peak():
    result = peak([10, None, 20, None, 30])
    assert result["peak_status"] == "not_yet_observed"
    assert result["selection_method"] == BUILDER.NO_COMPLETE_WINDOW
