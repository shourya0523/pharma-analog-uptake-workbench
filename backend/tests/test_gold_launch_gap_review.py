"""A series that starts after launch is judged only when few launch quarters are missing.

The review decides whether such a series can carry a peak: how many quarters
lie between launch and its first reported quarter, and whether its highest
year can be read as the peak. A highest year with no lower year before it is
not read as one when launch quarters are missing, because the true peak may
sit in them. The checks below use invented products, so they state the rule
rather than any one series in gold.
"""

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD = REPO_ROOT / "seed" / "gold"
BUILDER_PATH = REPO_ROOT / "scripts" / "build_independent_gold.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_independent_gold", BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BUILDER = load_builder()


def series(name, yearly, first_year=2010):
    """Quarterly rows whose four quarters each hold a quarter of the year's total."""
    rows = []
    for offset, total in enumerate(yearly):
        year = first_year + offset
        for quarter in range(1, 5):
            rows.append({
                "drug_name": name, "period": f"{year}Q{quarter}", "calendar_year": year,
                "calendar_quarter": quarter, "value_normalized_usd_millions": total / 4,
                "gold_id": f"{name}-{year}q{quarter}".lower(),
            })
    return rows


def review(yearly, launch, first_year=2010):
    rows = series("Calderon", yearly, first_year)
    return BUILDER.launch_gap_review("Calderon", rows, {"launch_quarter": launch}, None)


def test_a_rise_then_two_lower_years_is_a_peak_inside_a_small_gap():
    result = review([40, 80, 100, 90, 70], launch="2009Q3")
    assert result["launch_gap_band"] == "small"
    assert result["peak_judgement"] == BUILDER.PEAK_OBSERVED


def test_a_series_still_rising_is_judged_not_yet_peaked():
    result = review([40, 80, 100], launch="2009Q3")
    assert result["peak_judgement"] == BUILDER.NOT_YET_OBSERVED


def test_a_highest_first_year_is_not_a_peak_once_launch_quarters_are_missing():
    """The fall could be the tail of a peak the series never saw."""
    result = review([100, 90, 70], launch="2009Q1")
    assert result["launch_gap_quarters"] > 0
    assert result["peak_judgement"] == BUILDER.PEAK_MAY_PREDATE_SERIES


def test_a_highest_first_year_is_a_peak_when_no_launch_quarter_is_missing():
    result = review([100, 90, 70], launch="2010Q1")
    assert result["launch_gap_quarters"] == 0
    assert result["peak_judgement"] == BUILDER.PEAK_OBSERVED


def test_too_many_missing_launch_quarters_is_not_judged_whatever_the_shape():
    launch = f"{2010 - 3}Q1"
    result = review([40, 80, 100, 90, 70], launch=launch)
    assert result["launch_gap_quarters"] > BUILDER.SUBSTANTIAL_LAUNCH_GAP_QUARTERS
    assert result["peak_judgement"] == BUILDER.LAUNCH_HISTORY_MISSING


def test_without_a_stated_launch_quarter_the_gap_runs_from_the_approval_year():
    rows = series("NuVessa", [40, 80], first_year=2012)
    result = BUILDER.launch_gap_review("NuVessa", rows, {}, 2011)
    assert result["launch_reference_quarter"] == "2011Q4"
    assert result["launch_reference_basis"] == "approval_year_minimum"
    assert result["launch_gap_quarters"] == 1


def test_gold_peaks_after_launch_only_where_the_review_judged_the_series():
    peaks = [json.loads(line) for line in (GOLD / "peak_sales.jsonl").read_text().splitlines() if line.strip()]
    coverage = {row["drug_name"]: row for row in
                (json.loads(line) for line in (GOLD / "series_coverage.jsonl").read_text().splitlines() if line.strip())}
    after = [row for row in peaks if row.get("series_starts_after_launch")]
    assert {row["peak_status"] for row in after} == {"observed", "not_yet_observed"}, (
        "both judgements must be represented, or a check on one cannot fail")
    for row in after:
        assert coverage[row["drug_name"]]["peak_judgement"] in (BUILDER.PEAK_OBSERVED, BUILDER.NOT_YET_OBSERVED)
        assert row["launch_gap_quarters"] <= BUILDER.SUBSTANTIAL_LAUNCH_GAP_QUARTERS
    unjudged = {name for name, row in coverage.items()
                if row.get("series_starts_after_launch")
                and row["peak_judgement"] not in (BUILDER.PEAK_OBSERVED, BUILDER.NOT_YET_OBSERVED)}
    assert unjudged, "no series is left unjudged, so the exclusion below cannot fail"
    assert unjudged.isdisjoint({row["drug_name"] for row in peaks if row.get("series_starts_after_launch")})


def test_every_quarterly_peak_carries_its_rolling_four_quarter_benchmark():
    peaks = [json.loads(line) for line in (GOLD / "peak_sales.jsonl").read_text().splitlines() if line.strip()]
    quarterly = [row for row in peaks if row["benchmark_rolling_4q_usd_millions"] is not None]
    assert quarterly
    for row in quarterly:
        first, last = row["benchmark_window"].split("-")
        assert BUILDER.quarter_number(last) - BUILDER.quarter_number(first) == 3, row["drug_name"]
