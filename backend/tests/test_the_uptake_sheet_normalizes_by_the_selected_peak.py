"""The peak and uptake sheets follow the stated rule, over the quarters the workbook carries.

`scripts/export_gold_workbook.py` writes Peak Benchmarks and Peak-Normalized
Uptake as Excel formulas, and stores their values by recalculating the file.
A formula can be wrong in a way that still produces a plausible number, so
every stored peak status, benchmark and percentage is recomputed here from
gold and the published estimates, independently of the formulas.

The rule, restated: the peak is the highest rolling four-quarter total of the
carried quarters (of equal totals, the earliest). It is observed once two later
totals exist; a series that stopped before then ended at its high; a withheld
quarter in, just before or after the highest window leaves the peak unknown;
an unreported quarter beside it, or a live series short of the later totals,
is not yet peaked; and a series missing launch quarters with no lower window
before the high is not judged. The benchmark is the observed high for an
observed peak, and otherwise the median of the comparable published estimates
where that is higher. Uptake is a quarter over a quarter of the benchmark,
limited to 0-100%, and 100% from the peak window's last quarter on for an
observed peak.

The check on estimate sources is rule 3 in the shape this sheet could break
it: an estimate sourced from a document gold itself cites is not an outside
view of the product, it is the answer key arriving as a forecast.
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys
from collections import Counter, defaultdict
from statistics import median

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = REPO / "seed" / "gold"
ESTIMATES = REPO / "seed" / "consensus_peak_estimates.csv"
WORKBOOK = REPO / "exports" / "pah_gold_dataset.xlsx"
PEAKS = "Peak Benchmarks"
UPTAKE = "Peak-Normalized Uptake"
sys.path.insert(0, str(REPO / "scripts"))
from uptake_workbook import (  # noqa: E402
    LINE_ENDED, NOT_JUDGED, NOT_YET, PEAK_OBSERVED, RESTRICTED, STATUSES, is_withheld,
)

PEAK_ESTIMATE = "peak_annual_sales"
FREE_TO_READ = "free_to_read"
CONSENSUS = "consensus estimate"
OBSERVED = "observed rolling 4Q high"
CONFIRMING = 2
UPTAKE_LEAD = 7


def quarter_index(period):
    return int(period[:4]) * 4 + int(period[5]) - 1


def label(index):
    return f"{index // 4}Q{index % 4 + 1}"


def grid(title):
    openpyxl = pytest.importorskip("openpyxl")
    assert WORKBOOK.exists(), (
        f"{WORKBOOK.relative_to(REPO)} is missing; regenerate it with python scripts/export_gold_workbook.py")
    book = openpyxl.load_workbook(WORKBOOK, read_only=True, data_only=True)
    rows = [[cell.value for cell in row] for row in book[title].iter_rows()]
    book.close()
    return rows


@pytest.fixture(scope="module")
def peaks():
    rows = grid(PEAKS)
    header = rows[1]
    return {"note": rows[0][0],
            "rows": [dict(zip(header, row)) for row in rows[2:] if row and row[0] is not None]}


@pytest.fixture(scope="module")
def uptake():
    rows = grid(UPTAKE)
    header = rows[1]
    return {"labels": header[UPTAKE_LEAD:],
            "rows": {row[1]: dict(zip(header[:UPTAKE_LEAD], row[:UPTAKE_LEAD]), curve=row[UPTAKE_LEAD:])
                     for row in rows[2:] if row and row[0] is not None}}


@pytest.fixture(scope="module")
def gold():
    rows = [json.loads(line) for line in
            (GOLD / "quarterly_revenue.jsonl").read_text().splitlines() if line.strip()]
    coverage = {json.loads(line)["drug_name"]: json.loads(line)
                for line in (GOLD / "series_coverage.jsonl").read_text().splitlines() if line.strip()}
    values, withheld, geography = defaultdict(dict), defaultdict(set), defaultdict(set)
    for row in rows:
        identity = row["benchmark_identity"]
        geography[identity].add(row["geography"])
        if is_withheld(row):
            withheld[identity].add(quarter_index(row["period"]))
        else:
            values[identity][quarter_index(row["period"])] = row["value_normalized_usd_millions"]
    as_of = json.loads((GOLD / "manifest.json").read_text())["as_of_quarter"]
    return {"values": values, "withheld": withheld, "geography": geography, "coverage": coverage, "as_of": as_of}


@pytest.fixture(scope="module")
def published():
    with ESTIMATES.open(newline="") as handle:
        grouped = defaultdict(list)
        for row in csv.DictReader(handle):
            grouped[row["product"].strip()].append(row)
    assert grouped, "no external estimates; every selection check below would be vacuous"
    kinds = {row["estimate_kind"] for rows in grouped.values() for row in rows}
    assert kinds - {PEAK_ESTIMATE}, (
        "every estimate on file is a peak estimate, so the check that a non-peak one is set aside cannot fail")
    return grouped


def usable_for(estimates, geography):
    return [row for row in estimates
            if row["estimate_kind"] == PEAK_ESTIMATE
            and row["estimate_geography"] == geography
            and row["source_access"] == FREE_TO_READ]


def expected_peak(drug, identity, gold):
    """(status, observed high, index of the window's last quarter), by the rule in the module docstring."""
    values, withheld = gold["values"][identity], gold["withheld"][identity]
    cov = gold["coverage"].get(drug, {})
    windows = {end: sum(values[end - k] for k in range(4))
               for end in values if all(end - k in values for k in range(4))}
    end = max(windows, key=lambda k: (windows[k], -k)) if windows else None
    high = windows[end] if windows else None
    if cov.get("peak_judgement") == "launch_history_missing":
        return NOT_JUDGED, high, end
    if not windows:
        return (RESTRICTED if withheld else NOT_YET), None, None
    if any(k >= end - 4 for k in withheld):
        return RESTRICTED, high, end
    known = set(values) | withheld
    if (end - 4 >= min(known) and end - 4 not in known) or (end + 1 <= max(known) and end + 1 not in known):
        return NOT_YET, high, end
    missing = (cov.get("launch_gap_quarters") or 0) > 0 or any(k < end - 4 for k in withheld)
    if missing and not any(value < high for k, value in windows.items() if k < end):
        return NOT_JUDGED, high, end
    if sum(1 for k in windows if k > end) >= CONFIRMING:
        return PEAK_OBSERVED, high, end
    live = (cov.get("series_end_quarter") or gold["as_of"]) == gold["as_of"]
    return (NOT_YET if live else LINE_ENDED), high, end


def test_every_peak_status_and_high_is_the_rule_applied_to_carried_quarters(peaks, gold):
    seen = Counter()
    for row in peaks["rows"]:
        drug, identity = row["drug_name"], row["benchmark_identity"]
        status, high, end = expected_peak(drug, identity, gold)
        assert row["peak_status"] == status, drug
        if high is None:
            assert row["observed_peak_4q_usd_m"] in (None, ""), drug
        else:
            assert row["observed_peak_4q_usd_m"] == pytest.approx(high), drug
            assert row["peak_window_end"] == label(end), drug
        seen[status] += 1
    assert set(seen) == set(STATUSES), f"not every status occurs, so a check on one cannot fail: {dict(seen)}"


def test_the_benchmark_is_the_observed_high_or_a_higher_comparable_consensus(peaks, gold, published):
    for row in peaks["rows"]:
        drug, identity = row["drug_name"], row["benchmark_identity"]
        geography = " | ".join(sorted(gold["geography"][identity]))
        assert row["geography"] == geography, drug
        usable = usable_for(published.get(drug, []), geography)
        consensus = median(float(item["estimate_usd_millions"]) for item in usable) if usable else None
        assert row["consensus_estimate_count"] == len(usable), drug
        assert row["consensus_peak_usd_m"] == consensus, drug

        status, high, _ = expected_peak(drug, identity, gold)
        if status == PEAK_OBSERVED:
            selected, basis = high, OBSERVED
        elif consensus is not None and (high is None or consensus > high):
            selected, basis = consensus, CONSENSUS
        else:
            selected, basis = high, (OBSERVED if high is not None else None)
        if selected is None:
            assert row["selected_peak_usd_m"] in (None, ""), drug
            continue
        assert row["selected_peak_usd_m"] == pytest.approx(selected), drug
        assert row["peak_basis"] == basis, drug
        assert row["quarterly_benchmark_usd_m"] == pytest.approx(selected / 4), drug


def test_both_benchmark_bases_are_exercised(peaks):
    bases = {row["peak_basis"] for row in peaks["rows"]}
    assert {CONSENSUS, OBSERVED} <= bases, f"only {bases} occur, so the comparison is never the thing deciding"


def test_an_estimate_is_used_or_set_aside_with_a_reason(peaks, published):
    """Nothing on file for a product vanishes between the file and the row."""
    for row in peaks["rows"]:
        on_file = published.get(row["drug_name"], [])
        if not on_file:
            assert not row["estimates_set_aside"], row["drug_name"]
            continue
        used = usable_for(on_file, row["geography"])
        set_aside = [item for item in on_file if item not in used]
        text = row["estimates_set_aside"] or ""
        assert len(text.split(" | ")) == len(set_aside) if set_aside else not text, row["drug_name"]
        for item in set_aside:
            assert item["estimate_as_stated"] in text, (row["drug_name"], item["source_url"])
            if item["estimate_kind"] != PEAK_ESTIMATE:
                assert item["estimate_kind"].replace("_", " ") in text, row["drug_name"]
            elif item["estimate_geography"] != row["geography"]:
                assert item["estimate_geography"] in text and row["geography"] in text, row["drug_name"]
            else:
                assert item["source_access"].replace("_", " ") in text, row["drug_name"]
        for item in used:
            assert item["source_url"] in (row["consensus_sources"] or ""), row["drug_name"]


def test_an_estimate_no_series_can_use_is_named_in_the_note(peaks, published):
    used = {row["drug_name"] for row in peaks["rows"] if row["consensus_estimate_count"]}
    unusable = sorted(set(published) - used)
    assert unusable, "every estimate on file reaches a series, so this check cannot fail"
    named = peaks["note"].rsplit("Estimates on file that no series can use:", 1)[-1]
    for product in unusable:
        assert product in named, product
    for product in used:
        assert product not in named, product


def test_every_percentage_is_a_carried_quarter_over_the_benchmark_limited_and_flat_after_peak(peaks, uptake, gold):
    flat = capped = 0
    for row in peaks["rows"]:
        identity = row["benchmark_identity"]
        curve = uptake["rows"][identity]
        anchor = quarter_index(curve["launch_anchor_quarter"])
        bench = row["quarterly_benchmark_usd_m"]
        status, _, end = expected_peak(row["drug_name"], identity, gold)
        values = gold["values"][identity]
        found = {}
        for heading, value in zip(uptake["labels"], curve["curve"]):
            if value in (None, ""):
                continue
            year, quarter = heading.split()[1], heading.split()[2]
            found[anchor + (int(year) - 1) * 4 + int(quarter[1:]) - 1] = value
        if bench in (None, ""):
            assert found == {}, row["drug_name"]
            continue
        assert set(found) == {k for k in values if k >= anchor}, row["drug_name"]
        for index, share in found.items():
            if status == PEAK_OBSERVED and index >= end:
                expected = 1
                flat += 1
            else:
                expected = min(1, max(0, values[index] / bench))
                capped += values[index] / bench > 1
            assert share == pytest.approx(expected), (row["drug_name"], label(index))
    assert flat and capped, "neither the flat extension nor the 100% limit is exercised"


def test_no_estimate_is_sourced_from_a_document_gold_cites():
    """An estimate is an outside view, or it is the answer key in a new hat."""
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
        "peak estimates sourced from documents gold itself cites:\n  " + "\n  ".join(overlap))
