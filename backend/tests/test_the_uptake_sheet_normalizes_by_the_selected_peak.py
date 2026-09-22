"""The uptake sheet's percentages are gold's own figures over a stated peak.

`scripts/export_gold_workbook.py` writes Peak-Normalized Uptake by dividing
each launch-aligned quarter by a peak benchmark, and like the two matrices
beside it the arithmetic is done in Python, so nothing recalculates it and
nothing else checks it. A benchmark taken from the wrong input, or a curve
re-indexed off by a quarter, reads exactly like a correct sheet.

Two of the checks are about which input won. The rule is that a published
estimate is used only where it is above everything the series has reported,
and a sheet that always took one side would pass a check that only looked at
the arithmetic.

The last one is rule 3 in the shape this sheet could break it: an estimate
sourced from a document gold itself cites is not an outside view of the
product, it is the answer key arriving as a forecast.
"""

from __future__ import annotations

import csv
import json
import pathlib
from collections import defaultdict
from statistics import median

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = REPO / "seed" / "gold"
ESTIMATES = REPO / "seed" / "consensus_peak_estimates.csv"
WORKBOOK = REPO / "exports" / "pah_gold_dataset.xlsx"
SHEET = "Peak-Normalized Uptake"

LEAD = [
    "drug_name", "benchmark_identity", "revenue_scope", "launch_anchor_quarter",
    "alignment_basis", "observed_peak_4q_usd_mm", "observed_peak_window",
    "consensus_peak_usd_mm", "consensus_estimate_count", "consensus_estimates_as_stated",
    "consensus_sources", "selected_peak_usd_mm", "peak_basis",
    "quarterly_benchmark_usd_mm", "scope_caveat",
]
CONSENSUS = "consensus median - above what the series has reported"
OBSERVED = "observed rolling four-quarter high"
NO_ESTIMATE = "observed rolling four-quarter high - no external estimate"


def quarter_index(period):
    return int(period[:4]) * 4 + int(period[5]) - 1


@pytest.fixture(scope="module")
def sheet():
    openpyxl = pytest.importorskip("openpyxl")
    assert WORKBOOK.exists(), (
        f"{WORKBOOK.relative_to(REPO)} is missing; regenerate it with "
        "python scripts/export_gold_workbook.py"
    )
    book = openpyxl.load_workbook(WORKBOOK, read_only=True, data_only=True)
    grid = [[cell.value for cell in row] for row in book[SHEET].iter_rows()]
    book.close()
    header = grid[1]
    assert header[:len(LEAD)] == LEAD, f"lead columns are {header[:len(LEAD)]}"
    rows = [row for row in grid[2:] if row and row[0] is not None]
    assert rows, "the sheet holds no series"
    return {
        "labels": header[len(LEAD):],
        "rows": [dict(zip(LEAD, row[:len(LEAD)]), curve=row[len(LEAD):]) for row in rows],
    }


@pytest.fixture(scope="module")
def gold():
    rows = [json.loads(line) for line in
            (GOLD / "quarterly_revenue.jsonl").read_text().splitlines() if line.strip()]
    series = defaultdict(dict)
    for row in rows:
        series[row["benchmark_identity"]][row["period"]] = row["value_normalized_usd_millions"]
    return series


@pytest.fixture(scope="module")
def published():
    with ESTIMATES.open(newline="") as handle:
        grouped = defaultdict(list)
        for row in csv.DictReader(handle):
            grouped[row["product"].strip()].append(float(row["estimate_usd_millions"]))
    assert grouped, "no external estimates; every selection check below would be vacuous"
    return grouped


def rolling_peak(values_by_period):
    """The highest total over four consecutive reported quarters."""
    indexed = {quarter_index(period): value for period, value in values_by_period.items()}
    windows = [
        sum(indexed[start + step] for step in range(4))
        for start in sorted(indexed)
        if all(start + step in indexed for step in range(4))
    ]
    return max(windows) if windows else None


def test_the_peak_is_the_larger_of_the_two_inputs(sheet, gold, published):
    """Consensus only where it is above the observed high; observed otherwise."""
    for row in sheet["rows"]:
        drug, identity = row["drug_name"], row["benchmark_identity"]
        observed = rolling_peak(gold[identity])
        estimates = published.get(drug, [])
        consensus = median(estimates) if estimates else None

        assert row["consensus_estimate_count"] == len(estimates), drug
        assert row["consensus_peak_usd_mm"] == consensus, drug
        assert row["observed_peak_4q_usd_mm"] == pytest.approx(round(observed, 1)), drug

        if consensus is not None and consensus > observed:
            assert row["peak_basis"] == CONSENSUS, drug
            assert row["selected_peak_usd_mm"] == pytest.approx(consensus), drug
        else:
            assert row["peak_basis"] == (OBSERVED if estimates else NO_ESTIMATE), drug
            assert row["selected_peak_usd_mm"] == pytest.approx(round(observed, 1)), drug
        assert row["quarterly_benchmark_usd_mm"] == pytest.approx(
            round(row["selected_peak_usd_mm"] / 4, 1)), drug


def test_both_peak_bases_are_exercised(sheet, published):
    """A sheet that always took one input would pass the check above.

    The rule only means something where both branches occur: a product whose
    published estimate is above anything it has reported, and a product that
    has overtaken the estimate written for it.
    """
    bases = {row["peak_basis"] for row in sheet["rows"]}
    assert CONSENSUS in bases, "no series is measured against a published estimate"
    assert OBSERVED in bases, (
        "no series has overtaken its published estimate, so the comparison is "
        "never the thing deciding"
    )


def test_every_percentage_is_a_gold_quarter_over_that_row_s_benchmark(sheet, gold):
    """Each cell is the quarter's own figure over the row's benchmark, and only there.

    Re-indexed the same way as the Launch-Aligned Matrix: offset 0 is the
    anchor quarter and reads Year 1 Q1, so a row anchored at 2019Q1 puts
    2019Q3 at Year 1 Q3.
    """
    seen = 0
    for row in sheet["rows"]:
        anchor = quarter_index(row["launch_anchor_quarter"])
        benchmark = row["quarterly_benchmark_usd_mm"]
        reported = gold[row["benchmark_identity"]]
        found = {}
        for label, value in zip(sheet["labels"], row["curve"]):
            if value is None:
                continue
            year, quarter = label.split()[1], label.split()[2]
            index = anchor + (int(year) - 1) * 4 + int(quarter[1:]) - 1
            found[f"{index // 4}Q{index % 4 + 1}"] = value
        assert set(found) == set(reported), row["drug_name"]
        for period, percentage in found.items():
            assert percentage == pytest.approx(round(reported[period] / benchmark * 100, 1)), (
                f"{row['drug_name']} {period}"
            )
        seen += len(found)
    assert seen == sum(len(values) for values in gold.values())


def test_no_estimate_is_sourced_from_a_document_gold_cites():
    """An estimate is an outside view, or it is the answer key in a new hat.

    A forecast read out of a filing gold already scores against is not
    independent of gold - it is gold's own evidence re-entering as the
    benchmark the pipeline's curves are normalized by.
    """
    cited = set()
    for path in sorted(GOLD.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            for key, value in row.items():
                if "url" in key.lower() and isinstance(value, str):
                    cited.add(value.strip())
            for source in row.get("sources") or []:
                cited.add(source["source_url"].strip())
    assert cited, "no gold source URLs found; this test would pass vacuously"

    with ESTIMATES.open(newline="") as handle:
        overlap = [row["source_url"].strip() for row in csv.DictReader(handle)
                   if row["source_url"].strip() in cited]
    assert not overlap, (
        "peak estimates sourced from documents gold itself cites:\n  " + "\n  ".join(overlap)
    )
