"""The uptake sheet's percentages are gold's own figures over a stated peak.

`scripts/export_gold_workbook.py` writes Peak-Normalized Uptake by dividing
each launch-aligned quarter by a peak benchmark, and like the two matrices
beside it the arithmetic is done in Python, so nothing recalculates it and
nothing else checks it. A benchmark taken from the wrong input, or a curve
re-indexed off by a quarter, reads exactly like a correct sheet.

Three of the checks are about which input won: a published estimate is used
only where it is comparable to the series and above everything that series has
reported. A sheet that always took one side would pass a check that only
looked at the arithmetic, and an estimate that is quietly dropped rather than
set aside with a reason leaves a row that cannot be argued with.

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
    "drug_name", "benchmark_identity", "revenue_scope", "series_geography",
    "launch_anchor_quarter", "alignment_basis", "observed_peak_4q_usd_mm",
    "observed_peak_window", "consensus_peak_usd_mm", "consensus_estimate_count",
    "consensus_estimates_as_stated", "consensus_sources", "estimates_set_aside",
    "selected_peak_usd_mm", "peak_basis", "quarterly_benchmark_usd_mm",
]
CONSENSUS = "consensus median - above what the series has reported"
OBSERVED = "observed rolling four-quarter high - above the consensus median"
NO_ESTIMATE = "observed rolling four-quarter high - no comparable estimate on file"
PEAK_ESTIMATE = "peak_annual_sales"


def quarter_index(period):
    return int(period[:4]) * 4 + int(period[5]) - 1


@pytest.fixture(scope="module")
def note():
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.load_workbook(WORKBOOK, read_only=True, data_only=True)
    text = next(book[SHEET].iter_rows(max_row=1))[0].value
    book.close()
    return text


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
    values = defaultdict(dict)
    geography = defaultdict(set)
    for row in rows:
        values[row["benchmark_identity"]][row["period"]] = row["value_normalized_usd_millions"]
        geography[row["benchmark_identity"]].add(row["geography"])
    return {"values": values, "geography": geography}


@pytest.fixture(scope="module")
def published():
    with ESTIMATES.open(newline="") as handle:
        grouped = defaultdict(list)
        for row in csv.DictReader(handle):
            grouped[row["product"].strip()].append(row)
    assert grouped, "no external estimates; every selection check below would be vacuous"
    kinds = {row["estimate_kind"] for rows in grouped.values() for row in rows}
    assert kinds - {PEAK_ESTIMATE}, (
        "every estimate on file is a peak estimate, so the check that a "
        "non-peak one is set aside cannot fail"
    )
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


def usable_for(estimates, geography):
    """The estimates a series of this geography may be normalized by."""
    return [row for row in estimates
            if row["estimate_kind"] == PEAK_ESTIMATE
            and row["estimate_geography"] == geography]


def test_the_peak_is_the_larger_of_the_two_comparable_inputs(sheet, gold, published):
    """Consensus only where it is comparable and above the observed high."""
    for row in sheet["rows"]:
        drug, identity = row["drug_name"], row["benchmark_identity"]
        geography = " | ".join(sorted(gold["geography"][identity]))
        assert row["series_geography"] == geography, drug

        observed = rolling_peak(gold["values"][identity])
        usable = usable_for(published.get(drug, []), geography)
        consensus = median(float(item["estimate_usd_millions"]) for item in usable) if usable else None

        assert row["consensus_estimate_count"] == len(usable), drug
        assert row["consensus_peak_usd_mm"] == consensus, drug
        assert row["observed_peak_4q_usd_mm"] == pytest.approx(round(observed, 1)), drug

        if consensus is not None and consensus > observed:
            assert row["peak_basis"] == CONSENSUS, drug
            assert row["selected_peak_usd_mm"] == pytest.approx(consensus), drug
        else:
            assert row["peak_basis"] == (OBSERVED if usable else NO_ESTIMATE), drug
            assert row["selected_peak_usd_mm"] == pytest.approx(round(observed, 1)), drug
        assert row["quarterly_benchmark_usd_mm"] == pytest.approx(
            round(row["selected_peak_usd_mm"] / 4, 1)), drug


def test_an_estimate_is_used_or_set_aside_with_a_reason(sheet, published):
    """Nothing on file for a product vanishes between the file and the row.

    An estimate that is not comparable is the interesting case - it is the
    one a reader would otherwise have to take on trust - so it has to arrive
    on the row saying which test it failed, not be filtered out upstream.
    """
    for row in sheet["rows"]:
        on_file = published.get(row["drug_name"], [])
        if not on_file:
            assert not row["estimates_set_aside"], row["drug_name"]
            continue
        used = usable_for(on_file, row["series_geography"])
        set_aside = [item for item in on_file if item not in used]
        text = row["estimates_set_aside"] or ""
        assert len(text.split(" | ")) == len(set_aside) if set_aside else not text, row["drug_name"]
        for item in set_aside:
            assert item["estimate_as_stated"] in text, (row["drug_name"], item["source_url"])
            wrong_kind = item["estimate_kind"] != PEAK_ESTIMATE
            assert (item["estimate_kind"].replace("_", " ") in text) if wrong_kind else (
                item["estimate_geography"] in text and row["series_geography"] in text
            ), row["drug_name"]
        for item in used:
            assert item["source_url"] in (row["consensus_sources"] or ""), row["drug_name"]


def test_an_estimate_no_series_can_use_is_named_in_the_note(note, sheet, published):
    """An estimate that reaches no row at all is still accounted for.

    The row-level check above cannot see a product with no series here, or
    one whose every estimate failed a test - there is no row to carry the
    reason. The sheet's note is where those land, and without this they would
    be in the file, absent from the sheet, and reported nowhere.
    """
    used = {row["drug_name"] for row in sheet["rows"] if row["consensus_estimate_count"]}
    unusable = sorted(set(published) - used)
    assert unusable, (
        "every estimate on file reaches a series, so this check cannot fail"
    )
    named = note.rsplit("Estimates on file that no series can use:", 1)[-1]
    for product in unusable:
        assert product in named, product
    for product in used:
        assert product not in named, product


def test_both_peak_bases_are_exercised(sheet):
    """A sheet that always took one input would pass the first check.

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
        reported = gold["values"][row["benchmark_identity"]]
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
    assert seen == sum(len(values) for values in gold["values"].values())


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
