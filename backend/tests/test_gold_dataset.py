import ast
import csv
import importlib.util
import json
import re

import pytest
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


def load_jsonl(name: str) -> list[dict]:
    path = GOLD / name
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def seed_names() -> set[str]:
    with (REPO_ROOT / "seed" / "example_drugs.csv").open(newline="") as handle:
        return {row["drug_name"] for row in csv.DictReader(handle)}


def test_gold_builder_has_no_application_or_pipeline_imports():
    tree = ast.parse(BUILDER_PATH.read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    forbidden = ("app", "pipeline", "orchestrator", "llm")
    assert not [name for name in imports if name.startswith(forbidden)]


def test_every_target_product_is_benchmarked_or_evidence_backed_excluded():
    """Three ways to be accounted for, not two.

    A product used to be either peaked or excluded. Adempas is neither: it has
    a complete, citable quarterly series that begins after launch, so it is a
    real benchmark series whose maximum says nothing about a lifetime peak.
    Collapsing that back into "excluded" would throw away usable data; leaving
    it out of the accounting would hide a product.
    """
    manifest = json.loads((GOLD / "manifest.json").read_text())
    peaks = load_jsonl(manifest["peak_sales_file"])
    excluded = load_jsonl(manifest["excluded_products_file"])
    coverage = load_jsonl(manifest["coverage_file"])
    peaked = {row["drug_name"] for row in peaks}
    excluded_names = {row["drug_name"] for row in excluded}
    series_without_peak = {row["drug_name"] for row in coverage} - peaked

    assert peaked.isdisjoint(excluded_names)
    assert series_without_peak.isdisjoint(excluded_names)
    # Comparators from other therapy areas are in the dataset but outside the
    # catalog this claim is about, so they are subtracted before comparing.
    # They are additive: they can never make a seed product look accounted for.
    comparators = set(manifest.get("gold_completeness_comparators", [])) or set(
        json.loads((GOLD / "build_report.json").read_text())["gold_completeness"][
            "comparator_products"
        ]
    )
    assert comparators.isdisjoint(seed_names())
    accounted = (peaked | excluded_names | series_without_peak) - comparators
    assert accounted == seed_names()
    assert manifest["target_product_count"] == len(seed_names())
    assert all(row["benchmark_eligible"] for row in peaks)
    # A series without a peak must be one that starts after launch, not an
    # oversight: anything else with a full span should still be peaked.
    for drug_name in series_without_peak:
        series = next(row for row in coverage if row["drug_name"] == drug_name)
        assert series.get("series_start_reason"), drug_name


def test_every_quarterly_benchmark_series_has_exact_full_coverage():
    builder = load_builder()
    manifest = json.loads((GOLD / "manifest.json").read_text())
    revenue = load_jsonl(manifest["reported_rows_file"])
    coverage = load_jsonl(manifest["coverage_file"])
    by_drug = {}
    for row in revenue:
        by_drug.setdefault(row["drug_name"], set()).add(row["period"])
    for row in coverage:
        # Against the series' own end, not the dataset's as-of quarter: a
        # bounded series legitimately stops earlier, and checking it against
        # as_of would fail it for quarters it never claimed to cover.
        expected = set(
            builder.quarter_range(
                row["commercial_start_quarter"],
                row.get("series_end_quarter", row["as_of_quarter"]),
            )
        )
        assert by_drug[row["drug_name"]] == expected
        assert row["observed_quarters"] == row["expected_quarters"] == len(expected)
        assert not row["missing_quarters"]
        assert row["coverage_pct"] == manifest["quarterly_coverage_pct"]


def test_gold_rows_have_independent_provenance_and_citations():
    builder = load_builder()
    manifest = json.loads((GOLD / "manifest.json").read_text())
    rows = (
        load_jsonl(manifest["reported_rows_file"])
        + load_jsonl(manifest["annual_rows_file"])
        + load_jsonl(manifest["excluded_products_file"])
    )
    assert len({row["gold_id"] for row in rows}) == len(rows)
    for row in rows:
        assert row["source_url"].startswith("https://")
        assert row["source_quote"]
        assert row["extraction_method"] == builder.PROVENANCE
        assert "pipeline" not in row["extraction_method"]
        if "value_reported" in row:
            cited_value = float(row.get("source_value_reported", row["value_reported"]))
            assert builder.quote_contains_number(row["source_quote"], cited_value), row["gold_id"]


def test_quarterly_rows_are_unique_and_preserve_reported_units():
    manifest = json.loads((GOLD / "manifest.json").read_text())
    rows = load_jsonl(manifest["reported_rows_file"])
    keys = {(row["drug_name"], row["period"]) for row in rows}
    assert len(keys) == len(rows)
    for row in rows:
        assert row["period_type"] == "quarterly"
        assert row["currency"] == "USD"
        assert row["unit"] == "millions"
        assert row["source_unit"] in {"units", "thousands", "millions"}
        assert row["sources"]
        # test_gold_rows_have_independent_provenance_and_citations only checks
        # that source_quote contains source_value_reported (the pre-conversion
        # number) — it never checks that value_reported is source_value_reported
        # scaled correctly by source_unit. A wrong source_unit (e.g. UTHR's 2016
        # rows, once misclassified as "thousands" when the exhibit had already
        # switched to millions) would pass that check while shipping a value
        # 1000x too small. Guard the actual scale relationship here instead.
        # "units" is what the pipeline calls the currency's base unit, the
        # scale a filing uses for an amount too small to print in millions:
        # Remodulin's first quarter on sale was "$205,000". It needs its own
        # divisor here or it would be read as 205,000 million.
        scale = {"units": 1_000_000, "thousands": 1000, "millions": 1}
        expected = row["source_value_reported"] / scale[row["source_unit"]]
        assert row["value_reported"] == round(expected, 6), row["gold_id"]


def test_annual_and_peak_rows_are_usd_normalized():
    manifest = json.loads((GOLD / "manifest.json").read_text())
    annual = load_jsonl(manifest["annual_rows_file"])
    peaks = load_jsonl(manifest["peak_sales_file"])

    for row in annual:
        assert row["value_normalized_usd_millions"] is not None, row["gold_id"]
        if row["currency"] == "USD":
            assert row["value_normalized_usd_millions"] == row["value_reported"], row["gold_id"]
            assert row["fx_rate_to_usd"] is None
            assert row["fx_rate_source"] is None
        else:
            assert row["fx_rate_to_usd"] is not None, row["gold_id"]
            assert row["fx_rate_source"], row["gold_id"]

    for row in peaks:
        if row["peak_status"] == "observed":
            assert row["currency"] == "USD", row["gold_id"]


def test_peaks_rebuild_from_independent_gold_builder():
    builder = load_builder()
    manifest = json.loads((GOLD / "manifest.json").read_text())
    quarterly = load_jsonl(manifest["reported_rows_file"])
    annual = load_jsonl(manifest["annual_rows_file"])
    stored = load_jsonl(manifest["peak_sales_file"])
    rebuilt = builder.build_peaks(quarterly, annual)
    assert stored == rebuilt
    observed = [row for row in stored if row["peak_status"] == "observed"]
    growing = [row for row in stored if row["peak_status"] == "not_yet_observed"]
    assert len(observed) == json.loads((GOLD / "build_report.json").read_text())["observed_peaks"]
    assert len(growing) == json.loads((GOLD / "build_report.json").read_text())["not_yet_observed_peaks"]
    assert all(row["numeric_peak_available"] and row["peak_value"] is not None for row in observed)
    assert all(not row["numeric_peak_available"] and row["peak_value"] is None for row in growing)


def test_legacy_pipeline_gold_writers_are_removed():
    assert not (REPO_ROOT / "scripts" / "build_gold_web_search.py").exists()
    assert not (REPO_ROOT / "scripts" / "audit_fill_gold.py").exists()
    assert not (REPO_ROOT / "scripts" / "research_gold_from_filings.py").exists()
    assert not (REPO_ROOT / "scripts" / "rebuild_gold_lifecycle.py").exists()


def test_legacy_gold_artifacts_are_removed():
    legacy_scripts = (
        "build_lifecycle_gold.py",
        "build_gold_web_search.py",
        "audit_fill_gold.py",
        "research_gold_from_filings.py",
        "rebuild_gold_lifecycle.py",
    )
    for name in legacy_scripts:
        assert not (REPO_ROOT / "scripts" / name).exists()
        assert not (REPO_ROOT / "eval" / name).exists()

    legacy_gold_files = (
        "metadata.jsonl",
        "peak_cases.jsonl",
        "competitive_cases.jsonl",
        "uptake_cases.jsonl",
        "edge_cases.jsonl",
        "audit_report.json",
        "lifecycle.jsonl",
    )
    for name in legacy_gold_files:
        assert not (GOLD / name).exists(), name

    assert not (GOLD / "archive").exists()
    assert not (REPO_ROOT / "eval" / "lifecycle_gold").exists()
    assert not (REPO_ROOT / "eval" / "gold").exists()


def test_every_research_manifest_uses_https_sources():
    builder = load_builder()
    for path in builder.SOURCE_DIR.glob("*.csv"):
        for row in builder.read_csv(path):
            if "source_url" in row:
                assert row["source_url"].startswith("https://"), (path.name, row["source_url"])


def test_a_series_may_end_before_the_as_of_quarter_when_it_says_why():
    """A product whose issuer stopped reporting it separately still has a series.

    Requiring every series to run to the as-of quarter is what forced whole
    products out of the dataset over a late reporting change: Opsumit is
    reportable from 2013 through 2024Q4 and was excluded outright because J&J
    merged it into a combined line in 2025. A bounded series keeps the years
    that are real, provided it states where it stops and why.
    """
    builder = load_builder()
    bounded = {
        "benchmark_identity": "test_bounded",
        "commercial_start_quarter": "2024Q1",
        "series_end_quarter": "2024Q3",
        "series_end_reason": "issuer merged the product into a combined line",
        "revenue_scope": "Worldwide",
        "geography": "Worldwide",
    }
    rows = [
        {"drug_name": "Bounded", "period": period}
        for period in ("2024Q1", "2024Q2", "2024Q3")
    ]

    original = builder.PRODUCT_METADATA
    builder.PRODUCT_METADATA = {"Bounded": bounded}
    try:
        coverage = builder.coverage_rows(rows)[0]
        assert coverage["benchmark_eligible"]
        assert coverage["series_end_quarter"] == "2024Q3"
        assert coverage["expected_quarters"] == 3
        assert coverage["series_end_reason"]

        # A value past the stated end is not part of the span, because the
        # basis changed there - it must not silently extend the series.
        beyond = builder.coverage_rows(rows + [{"drug_name": "Bounded", "period": "2025Q1"}])[0]
        assert not beyond["benchmark_eligible"]
        assert beyond["quarters_beyond_series_end"] == ["2025Q1"]

        # Ending early without saying why is refused outright.
        builder.PRODUCT_METADATA = {
            "Bounded": {**bounded, "series_end_reason": ""},
        }
        try:
            builder.coverage_rows(rows)
        except ValueError as exc:
            assert "series_end_reason" in str(exc)
        else:
            raise AssertionError("a short series with no stated reason must fail")
    finally:
        builder.PRODUCT_METADATA = original


def test_catalog_coverage_counts_every_seed_product_exactly_once():
    """Coverage over included products alone always reads 100% and hides the gap."""
    builder = load_builder()
    manifest = json.loads((GOLD / "manifest.json").read_text())
    coverage = load_jsonl(manifest["coverage_file"])
    exclusions = load_jsonl(manifest["excluded_products_file"])

    catalog = builder.catalog_coverage(coverage, exclusions)
    assert catalog["catalog_products"] == len(seed_names())
    # Flolan supplies annual context rows while being an excluded product; it
    # must not be counted in both buckets.
    assert not set(catalog["annual_only_products"]) & set(catalog["excluded_products"])
    # Comparators are outside the catalog and must not inflate its coverage: a
    # product added from another therapy area is not one of the twenty covered.
    comparators = set(catalog["comparator_products"])
    assert comparators.isdisjoint(seed_names())
    in_catalog = set(catalog["quarterly_series_products"]) - comparators
    assert (
        len(in_catalog)
        + len(catalog["annual_only_products"])
        + len(catalog["excluded_products"])
        == len(seed_names())
    )
    assert catalog["quarterly_series_pct"] == round(
        100 * len(in_catalog) / len(seed_names()), 1
    )


def test_an_excluded_product_may_carry_annual_context_but_never_a_benchmark():
    """Annual figures are evidence; they are not a series and not a peak.

    Actelion published per-product annual sales for Opsumit, Veletri and
    Ventavis, so each has a verified number rather than nothing at all. None of
    them has a citable launch-to-end quarterly series, so none may acquire a
    peak row or a coverage row on the strength of those annual figures - which
    is how a product with two years of data would otherwise start being scored
    as a benchmark it cannot support.
    """
    manifest = json.loads((GOLD / "manifest.json").read_text())
    annual = load_jsonl(manifest["annual_rows_file"])
    peaks = load_jsonl(manifest["peak_sales_file"])
    coverage = load_jsonl(manifest["coverage_file"])
    excluded = {row["drug_name"] for row in load_jsonl(manifest["excluded_products_file"])}

    with_annual = {row["drug_name"] for row in annual}
    context_only = with_annual & excluded
    assert context_only, "the annual-context case must stay represented in the dataset"

    peak_names = {row["drug_name"] for row in peaks}
    coverage_names = {row["drug_name"] for row in coverage}
    for drug_name in context_only:
        assert drug_name not in peak_names, drug_name
        assert drug_name not in coverage_names, drug_name
        rows = [row for row in annual if row["drug_name"] == drug_name]
        assert all(row["series_role"] == "partial_context" for row in rows), drug_name
        # Context rows are held to the same evidence bar as benchmark rows: a
        # real quote, a real source, and a comparable USD figure.
        for row in rows:
            assert row["source_quote"] and row["source_url"].startswith("https://")
            assert row["value_normalized_usd_millions"] is not None, row["gold_id"]


def test_opsumit_annual_series_matches_the_filings_it_cites():
    """Pins both eras of Opsumit's annual context rows.

    The series deliberately spans two issuers: Actelion in CHF from the 2013
    launch, J&J in USD from the 16 June 2017 acquisition. 2015-2017 is absent
    because no annual figure is citable there, which is the gap that keeps the
    product excluded - not an oversight to be quietly filled.

    The USD rows carry J&J's stated worldwide line, never US + International.
    For 2019 J&J prints 766 + 562 but states Worldwide 1,327, not 1,328: it
    sums unrounded figures and rounds once, and both filings carrying 2019
    agree. Deriving the total would put a value in the dataset that appears in
    no filing.
    """
    rows = {
        row["period"]: row
        for row in load_jsonl("annual_revenue.jsonl")
        if row["drug_name"] == "Opsumit"
    }
    assert {period: row["value_reported"] for period, row in rows.items()} == {
        "2013": 5.0,
        "2014": 180.0,
        "2018": 1215.0,
        "2019": 1327.0,
        "2020": 1639.0,
        "2021": 1819.0,
        "2022": 1783.0,
        "2023": 1973.0,
        "2024": 2184.0,
    }
    assert {row["currency"] for row in rows.values()} == {"CHF", "USD"}
    for period, row in rows.items():
        # The J&J era is USD read off SEC filings; the Actelion era is CHF and
        # has no SEC source, which is exactly why the middle years are missing.
        if row["currency"] == "USD":
            assert row["source_url"].startswith("https://www.sec.gov/")
            assert f"{int(row['value_reported']):,}" in row["source_quote"]
        assert row["series_role"] == "partial_context"


def test_catalog_counts_are_derived_not_hand_maintained():
    """A product may sit in both ANNUAL_METADATA and the exclusions.

    Four now do. The manifest used to subtract a hardcoded 1 for that overlap,
    which would have reported 23 products in a 20-product catalog.
    """
    manifest = json.loads((GOLD / "manifest.json").read_text())
    report = json.loads((GOLD / "build_report.json").read_text())
    catalog = report["catalog_coverage"]
    assert manifest["target_product_count"] == catalog["catalog_products"]
    assert manifest["annual_only_series_count"] == len(catalog["annual_only_products"])
    assert catalog["catalog_products"] == len(seed_names())


def test_quarters_the_issuer_states_outright_cite_the_filing_not_the_schedule():
    """Winrevair read 0% of its own span until these citations were fixed.

    Every Winrevair row cited one IR schedule whose quote carried a
    hand-written column legend - "(2025 Q1-Q4/FY = ...)". The values were right,
    but the provenance was a human decoding a column layout, so nothing in the
    series was independently readable and the product scored zero. Where Merck
    states the quarter outright in a 10-Q, the row now cites that sentence, and
    the extractor can be scored on it.
    """
    from app.extraction.prose import read_prose

    rows = {
        row["period"]: row
        for row in load_jsonl("quarterly_revenue.jsonl")
        if row["drug_name"] == "Winrevair"
    }
    stated_in_a_filing = {"2025Q1": 280.0, "2025Q2": 336.0, "2025Q3": 360.0, "2026Q1": 525.0}

    for period, value in stated_in_a_filing.items():
        row = rows[period]
        assert row["source_url"].startswith("https://www.sec.gov/"), period
        assert row["source_type"] == "sec_filing", period
        assert row["value_reported"] == value
        # The quote has to be text the extractor can actually read, not a
        # schedule row that needs a legend to interpret.
        read = {v.period: v.value_as_reported for v in read_prose(row["source_quote"], product="Winrevair")}
        assert read.get(period) == value, f"{period} is cited but not readable"


def test_yutrepia_quarters_cite_their_own_filing_in_the_issuer_column_order():
    """Each quarter has to come from the filing that reports it as current.

    Yutrepia's rows previously shared one quote from the Q2 2026 10-Q with a
    hand-written legend naming the columns, which is a human decoding a layout.
    Liquidia's statement of operations already prints the standard order -
    quarter, prior-year quarter, year-to-date, prior-year year-to-date - so
    citing each quarter's own filing makes the row self-describing and drops
    the legend. Citing a later filing would put the quarter in the prior-year
    column, where the value is right but the alignment is not.
    """
    rows = {
        row["period"]: row
        for row in load_jsonl("quarterly_revenue.jsonl")
        if row["drug_name"] == "Yutrepia"
    }
    filing_year = {
        "2025Q2": "20250630",
        "2025Q3": "20250930",
        "2026Q1": "20260331",
        "2026Q2": "20260630",
    }
    for period, stamp in filing_year.items():
        row = rows[period]
        assert row["source_url"].startswith("https://www.sec.gov/"), period
        assert stamp in row["source_url"], f"{period} does not cite its own filing"
        # A unit note like "(USD thousands)" is a declaration; a legend naming
        # which column is which period is the thing that has to be gone.
        assert not re.search(r"\(([^)]*)(?:Q[1-4]\s*\d{4}|\d{4}\s*Q[1-4]|H[12]\s*\d{4})", row["source_quote"]), (
            f"{period} still carries a column legend"
        )

    # 2025Q3 was a gold-side derivation until Liquidia's own Q3 10-Q was read;
    # the filing states 51,669 exactly, matching what the arithmetic produced.
    assert rows["2025Q3"]["derivation"] == "direct_reported"
    assert rows["2025Q3"]["value_reported"] == 51.669


def test_the_gold_dataset_is_complete_on_its_own_terms():
    """Complete means every catalog product accounted for, no series with a hole.

    This is deliberately not the number ``scripts/eval_completeness.py`` prints.
    That script scores the *pipeline* against this dataset, and a shortfall
    there is a capability the pipeline lacks. The oracle itself has to be whole,
    or every score computed from it is meaningless.
    """
    report = json.loads((GOLD / "build_report.json").read_text())
    completeness = report["gold_completeness"]

    assert completeness["unaccounted_products"] == []
    assert completeness["series_missing_quarters"] == []
    assert completeness["accounted_for"] == completeness["catalog_products"]
    assert completeness["catalog_products"] == len(seed_names())
    assert completeness["complete"] is True

    # Every included quarterly series covers its whole commercial span.
    for series in load_jsonl("series_coverage.jsonl"):
        assert series["coverage_pct"] == 100.0, series["drug_name"]
        assert series["missing_quarters"] == [], series["drug_name"]
        assert series["benchmark_eligible"], series["drug_name"]

    # And each of the three ways a product can be accounted for is populated,
    # so "complete" cannot be reached by quietly emptying a category.
    assert completeness["complete_quarterly_series"] > 0
    assert completeness["annual_benchmark_series"] > 0
    assert completeness["evidence_backed_exclusions"] > 0


def test_adempas_is_a_scoped_series_that_never_earns_a_peak():
    """The only territory-split product in the catalog, and the reason it is here.

    Bayer commercialises Adempas in the Americas; Merck records only its own
    territories as product sales and Bayer's as a separate alliance-revenue
    line. Merck blended the two into one figure until 2020Q1, which is what
    made this product unusable before - the old exclusion was right about the
    blend and wrong to conclude no series existed after the split.

    The series starts after launch, so it is a scope-and-format benchmark, not
    an uptake curve, and it must never acquire a peak row: its maximum is only
    the largest value inside the window it happens to cover.
    """
    coverage = {row["drug_name"]: row for row in load_jsonl("series_coverage.jsonl")}
    rows = [r for r in load_jsonl("quarterly_revenue.jsonl") if r["drug_name"] == "Adempas"]
    peaks = {row["drug_name"] for row in load_jsonl("peak_sales.jsonl")}
    excluded = {row["drug_name"] for row in load_jsonl("excluded_products.jsonl")}

    assert "Adempas" in coverage and "Adempas" not in excluded
    assert "Adempas" not in peaks, "a series that starts after launch cannot locate a peak"

    series = coverage["Adempas"]
    assert series["coverage_pct"] == 100.0 and series["missing_quarters"] == []
    # A start later than launch has to be declared and explained.
    assert series["launch_quarter"] == "2013Q4"
    assert series["commercial_start_quarter"] == "2024Q1"
    assert "series_start_reason" in series and series["series_start_reason"]

    assert {r["revenue_scope"] for r in rows} == {"Merck marketing territories"}
    assert {r["geography"] for r in rows} == {"International"}
    assert {r["value_reported"] for r in rows} == {70.0, 72.0, 73.0, 68.0, 80.0, 82.0, 83.0, 78.0}


def test_a_series_starting_after_launch_must_say_why():
    """The guard, mirrored from the one on series_end_reason."""
    builder = load_builder()
    meta = dict(builder.PRODUCT_METADATA["Adempas"])
    meta.pop("series_start_reason")
    rows = [
        {"drug_name": "Adempas", "period": "2024Q1", "value_reported": 70.0},
        {"drug_name": "Adempas", "period": "2024Q2", "value_reported": 72.0},
    ]
    original = builder.PRODUCT_METADATA["Adempas"]
    builder.PRODUCT_METADATA["Adempas"] = meta
    try:
        with pytest.raises(ValueError, match="series_start_reason"):
            builder.coverage_rows(rows)
    finally:
        builder.PRODUCT_METADATA["Adempas"] = original


def test_opsumit_is_a_series_bounded_at_both_ends():
    """The first product in the catalog whose series is cut short at both ends.

    Opsumit was excluded outright until its two boundaries could be stated.
    It launched in 2013Q4 under Actelion, which reported it in CHF and only for
    scattered quarters; J&J has reported it in USD since acquiring Actelion on
    16 June 2017, and from 2025Q1 folds it into a combined OPSUMIT / OPSYNVI
    line. What is left in between is a clean 30-quarter single-issuer series,
    and the point of this test is that both cuts are declared rather than
    silently applied - a reader who takes 2017Q3 for the launch, or reads past
    2024Q4 into the combined line, gets a wrong answer from a right-looking
    series.
    """
    coverage = {row["drug_name"]: row for row in load_jsonl("series_coverage.jsonl")}
    rows = [r for r in load_jsonl("quarterly_revenue.jsonl") if r["drug_name"] == "Opsumit"]
    peaks = {row["drug_name"] for row in load_jsonl("peak_sales.jsonl")}
    excluded = {row["drug_name"] for row in load_jsonl("excluded_products.jsonl")}

    assert "Opsumit" in coverage
    assert "Opsumit" not in excluded, "the exclusion was superseded by the J&J-era series"
    assert "Opsumit" not in peaks, "2015-2017 is missing, so 2024 is not a lifetime peak"

    series = coverage["Opsumit"]
    assert series["coverage_pct"] == 100.0 and series["missing_quarters"] == []
    assert series["commercial_start_quarter"] == "2016Q1"
    assert series["launch_quarter"] == "2013Q4"
    assert series["series_end_quarter"] == "2024Q4"
    assert series["quarters_beyond_series_end"] == []
    for field in ("series_start_reason", "series_end_reason"):
        assert series[field], f"a bounded series must state its {field}"

    assert len(rows) == 36
    assert {r["geography"] for r in rows} == {"Worldwide"}
    assert {r["currency"] for r in rows} == {"USD"}
    assert {r["manufacturer"] for r in rows} == {"Johnson & Johnson"}

    # 2017Q2 straddles the acquisition and is the only quarter no single issuer
    # reports; it must carry the dated parts that make the sum checkable.
    bridge = next(r for r in rows if r["period"] == "2017Q2")
    assert bridge["derivation"] == "acquisition_bridge_sum"
    assert [c["covers"] for c in bridge["bridge_components"]] == [
        "2017-04-01/2017-06-15",
        "2017-06-16/2017-07-02",
    ]


def test_opsumit_quarters_sum_to_the_full_year_jnj_states():
    """The arithmetic that makes this series trustworthy.

    Each quarter is read from a different document, so a mis-keyed or
    mis-aligned figure would not show up as an inconsistency anywhere inside
    the series. It does show up here: J&J states a full-year worldwide total in
    each Q4 schedule, and every complete year has to reproduce it exactly.
    2017 is excluded because J&J owned the product for only part of that year.
    """
    stated_full_year = {
        # Actelion's own schedule, republished by J&J in US dollars.
        2016: 844.0,
        2018: 1215.0,
        2019: 1327.0,
        2020: 1639.0,
        2021: 1819.0,
        2022: 1783.0,
        2023: 1973.0,
        2024: 2184.0,
    }
    rows = [r for r in load_jsonl("quarterly_revenue.jsonl") if r["drug_name"] == "Opsumit"]
    by_year: dict[int, list[float]] = {}
    for row in rows:
        by_year.setdefault(row["calendar_year"], []).append(row["value_reported"])

    for year, total in stated_full_year.items():
        quarters = by_year[year]
        assert len(quarters) == 4, f"{year} has {len(quarters)} quarters"
        assert sum(quarters) == total, (
            f"{year} quarters sum to {sum(quarters)}, J&J states {total}"
        )
    # 2017 is the acquisition year, and the one year with no full-year figure
    # to check against: Actelion reports through 15 June and J&J from 16 June,
    # and neither states a twelve-month total. All four quarters are present -
    # 2017Q2 by assembling the two halves - but the year can only be checked
    # against its parts, not against a published total.
    assert sorted(by_year) == sorted({2017, *stated_full_year})
    assert len(by_year[2017]) == 4
    assert sum(by_year[2017]) == 244 + 261 + 259 + 269


def test_opsumit_quarterly_and_annual_series_are_not_the_same_benchmark():
    """Two Opsumit series exist on purpose, and must not be compared.

    The annual rows splice Actelion's CHF years onto J&J's USD years to give
    context for a product older than either; the quarterly rows are the single
    issuer, single currency series J&J actually reports. Giving them one
    identity would invite an uptake curve drawn across an acquisition and a
    currency change.
    """
    quarterly = {
        r["benchmark_identity"]
        for r in load_jsonl("quarterly_revenue.jsonl")
        if r["drug_name"] == "Opsumit"
    }
    annual = {
        r["benchmark_identity"]
        for r in load_jsonl("annual_revenue.jsonl")
        if r["drug_name"] == "Opsumit"
    }
    assert quarterly == {"jnj_opsumit_worldwide_reported"}
    assert annual == {"actelion_jnj_opsumit_worldwide_partial"}
    assert not quarterly & annual


def test_opsumit_worldwide_is_never_summed_from_us_and_international():
    """Three quarters where summing the parts gives the wrong answer.

    J&J rounds the US, International and Worldwide lines independently, so the
    parts differ from the stated worldwide figure by 1 in 2019Q1, 2021Q2 and
    2024Q1. Gold carries what the issuer states. The US and International
    figures ride along in the row's notes precisely so this stays checkable.
    """
    rows = {
        r["period"]: r
        for r in load_jsonl("quarterly_revenue.jsonl")
        if r["drug_name"] == "Opsumit"
    }
    for period, parts, stated in (
        ("2019Q1", (172, 133), 306.0),
        ("2021Q2", (290, 172), 463.0),
        ("2024Q1", (356, 169), 524.0),
    ):
        row = rows[period]
        assert row["value_reported"] == stated
        assert sum(parts) != stated, f"{period} would not demonstrate anything"
        for part in parts:
            assert str(part) in row["gold_notes"]


def test_every_quarterly_row_is_reachable_by_the_pipeline():
    """The end-to-end claim: no row sits in gold that nothing can reproduce.

    A benchmark row the pipeline can neither read nor compute measures nothing.
    Each of the four routes below is a real disclosure shape, and every row has
    to take one of them - a row taking none is a gap, whatever its provenance.
    """
    rows = load_jsonl("quarterly_revenue.jsonl")
    reproducible = {
        "direct_reported",
        "direct_prior_year_column",
        "direct_prior_year_schedule",
        "direct_reported_rounded",
        "direct_retrospective_table",
        "direct_jnj_retrospective_table",
        "annual_less_reported_first_nine_months",
        "full_year_less_other_reported_quarters",
        "year_to_date_less_reported_quarters",
        "identity_normalization_pre_dpi",
        "acquisition_bridge_sum",
    }
    unreachable = sorted(
        {(row["drug_name"], row["period"], row["derivation"]) for row in rows}
        - {
            (row["drug_name"], row["period"], row["derivation"])
            for row in rows
            if row["derivation"] in reproducible
        }
    )
    assert not unreachable, f"rows with no reproduction route: {unreachable}"


def test_a_bridged_quarter_carries_parts_that_tile_it():
    """Both acquisition bridges, checked against the pipeline's own assembler.

    The row records a sum; these are the parts it is a sum of. Checking them
    here means a bridge cannot be quietly edited to any convenient number.
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from app.extraction.derive import assemble_split_ownership_quarter

    bridged = [
        row
        for row in load_jsonl("quarterly_revenue.jsonl")
        if row["derivation"] == "acquisition_bridge_sum"
    ]
    # Every bridge in the catalog is the same quarter: the one J&J's Actelion
    # acquisition split. If a bridge ever appears in another quarter it is
    # either a new acquisition or a mistake, and either deserves a look.
    assert {row["period"] for row in bridged} == {"2017Q2"}
    assert {row["drug_name"] for row in bridged} >= {"Opsumit", "Tracleer", "Uptravi"}
    for row in bridged:
        components = row["bridge_components"]
        assert len(components) == 2
        assert {c["issuer"] for c in components} == {"Actelion", "Johnson & Johnson"}
        assembled = assemble_split_ownership_quarter(row["period"], components)
        assert assembled == row["value_reported"], row["gold_id"]


def test_remodulin_fourth_quarters_reconcile_to_the_annual_totals_they_cite():
    """Seven Q4s United Therapeutics never stated on their own.

    Each is a full year less the three quarters that were stated. Before these
    annual totals were sourced, the Q4 rows quoted a sentence that restated
    their own value - which looks like a citation and proves nothing. The test
    is that the arithmetic closes against a figure read from a filing.
    """
    annual = {
        row["period"]: row["value_reported"]
        for row in load_jsonl("annual_revenue.jsonl")
        if row["drug_name"] == "Remodulin"
    }
    quarters: dict[int, dict[int, float]] = {}
    for row in load_jsonl("quarterly_revenue.jsonl"):
        if row["drug_name"] == "Remodulin":
            quarters.setdefault(row["calendar_year"], {})[row["calendar_quarter"]] = row[
                "value_reported"
            ]

    assert set(annual) == {"2002", "2003", "2004", "2005", "2006", "2007", "2008"}
    for year_text, total in annual.items():
        year = int(year_text)
        stated = quarters[year]
        assert len(stated) == 4, f"{year} has {len(stated)} quarters"
        assert round(sum(stated.values()), 3) == total, year


def test_winrevair_2024_quarters_cite_the_filings_that_state_them():
    """The pair that used to need a hand-written legend to interpret.

    Merck's IR schedule prints 2025 Q1-Q4 then 2024 Q2-Q4, an order that
    encodes the March 2024 approval date rather than anything on the page. The
    10-Q states each quarter in the ordinary way, so the legend is gone.
    """
    rows = {
        row["period"]: row
        for row in load_jsonl("quarterly_revenue.jsonl")
        if row["drug_name"] == "Winrevair"
    }
    for period, value in (("2024Q2", 70.0), ("2024Q3", 149.0)):
        row = rows[period]
        assert row["value_reported"] == value
        assert row["source_url"].startswith("https://www.sec.gov/")
        assert row["derivation"] == "direct_reported"
        assert "|" in row["source_quote"], "must be readable as a table row"


def test_tracleer_is_the_only_series_observed_wholly_in_decline():
    """Every other series here rises. This one does not, and that is the point.

    A benchmark built only from growing products never catches a reader that
    assumes the last value is the largest, or that a series maximum is a
    lifetime peak. Tracleer's window opens fifteen years after launch and four
    years after the product peaked, so its highest quarter is its first.
    """
    rows = sorted(
        (r for r in load_jsonl("quarterly_revenue.jsonl") if r["drug_name"] == "Tracleer"),
        key=lambda r: r["period"],
    )
    coverage = {r["drug_name"]: r for r in load_jsonl("series_coverage.jsonl")}["Tracleer"]
    peaks = {r["drug_name"] for r in load_jsonl("peak_sales.jsonl")}

    assert len(rows) == 16
    assert rows[0]["period"] == "2016Q1" and rows[-1]["period"] == "2019Q4"
    assert rows[0]["value_reported"] == max(r["value_reported"] for r in rows)
    assert rows[-1]["value_reported"] < rows[0]["value_reported"] / 4

    # It still appears in peak_sales, but from the annual CHF series, which is
    # the only place the real 2011 peak exists.
    assert "Tracleer" in peaks
    assert coverage["series_end_basis"] == "issuer_stopped_reporting"


def test_letairis_series_exists_because_the_table_says_what_the_prose_does_not():
    """The exclusion read the narrative and stopped at the aggregate.

    Gilead's releases fold Letairis into a sentence about "Other product
    sales", and this catalog excluded it on that basis. The PRODUCT SALES
    SUMMARY table in the same document states the line on its own.
    """
    rows = [r for r in load_jsonl("quarterly_revenue.jsonl") if r["drug_name"] == "Letairis"]
    excluded = {r["drug_name"] for r in load_jsonl("excluded_products.jsonl")}
    coverage = {r["drug_name"]: r for r in load_jsonl("series_coverage.jsonl")}["Letairis"]

    assert len(rows) == 16
    assert "Letairis" not in excluded
    assert {r["manufacturer"] for r in rows} == {"Gilead"}
    assert {r["geography"] for r in rows} == {"United States"}
    assert all(r["source_url"].startswith("https://www.gilead.com/") for r in rows)

    stated_full_year = {2016: 819.0, 2017: 887.0, 2018: 943.0, 2019: 618.0}
    by_year: dict[int, list[float]] = {}
    for row in rows:
        by_year.setdefault(row["calendar_year"], []).append(row["value_reported"])
    for year, total in stated_full_year.items():
        assert len(by_year[year]) == 4
        assert sum(by_year[year]) == total, year

    assert coverage["series_end_basis"] == "sourcing_boundary"


def test_a_series_says_whether_its_end_is_the_issuer_or_the_sourcing():
    """Two different facts that look identical in a coverage row.

    A series that ends because the issuer stopped publishing the line is
    finished - no amount of work extends it. A series that ends because
    sourcing stopped is an open invitation. Collapsing them into one
    "series_end_reason" string makes the second look like the first, and the
    dataset then reads as more complete than it is.
    """
    coverage = load_jsonl("series_coverage.jsonl")
    bounded = [row for row in coverage if "series_end_reason" in row]
    assert bounded, "the catalog has bounded series; this test is not vacuous"

    for row in bounded:
        assert row["series_end_basis"] in {"issuer_stopped_reporting", "sourcing_boundary"}
        assert row["series_end_reason"]
    # Both kinds are present, so neither branch is untested.
    assert {row["series_end_basis"] for row in bounded} == {
        "issuer_stopped_reporting",
        "sourcing_boundary",
    }


def test_no_single_issuer_dominates_the_catalog():
    """A concentration ceiling, so the benchmark cannot quietly become one company.

    This was 80% when United Therapeutics was three quarters of these rows,
    which was a bound loose enough to be nearly free. Two comparator blocks
    later - eleven J&J products and nine Gilead antivirals - UTHR is at 40.0%,
    and the ceiling is set just above that so the gain cannot be given back.
    It is a ratchet, not the target: `concentration` in build_report.json
    carries the target, which is stricter and not yet met.
    """
    rows = load_jsonl("quarterly_revenue.jsonl")
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["manufacturer"]] = counts.get(row["manufacturer"], 0) + 1
    top_issuer, top_count = max(counts.items(), key=lambda kv: kv[1])
    share = top_count / len(rows)
    assert share < 0.45, f"{top_issuer} is {share:.1%} of the catalog"
    assert len(counts) >= 6, f"only {len(counts)} issuers: {sorted(counts)}"


def test_the_concentration_report_describes_the_rows_it_claims_to():
    """The balance metrics have to be computed from the dataset, not asserted.

    A report that says "largest issuer 40.0%" while the rows say something else
    is worse than no report: it is a number people will quote. This recomputes
    all three shares from quarterly_revenue.jsonl and requires the published
    block to agree, and requires the block's own pass/fail flags to follow from
    its own numbers rather than being written down separately.
    """
    rows = load_jsonl("quarterly_revenue.jsonl")
    report = json.loads((GOLD / "build_report.json").read_text())
    balance = report["concentration"]
    assert balance["quarters"] == len(rows)

    for field, key in (
        ("manufacturer", "largest_issuer"),
        ("drug_name", "largest_product"),
        ("therapeutic_area", "largest_therapeutic_area"),
    ):
        counts: dict[str, int] = {}
        for row in rows:
            counts[row[field]] = counts.get(row[field], 0) + 1
        name, count = max(counts.items(), key=lambda kv: kv[1])
        assert balance[key] == name, key
        assert balance[f"{key}_share"] == round(100 * count / len(rows), 1), key

    areas = {row["therapeutic_area"] for row in rows}
    assert balance["therapeutic_area_count"] == len(areas)
    flags = [value for key, value in balance.items() if key.endswith("_within_target")]
    assert balance["balanced"] == all(flags)


def test_the_two_largest_jnj_series_match_an_independent_disclosure():
    """Two series checked against a document they do not cite.

    Every J&J quarter here comes from the quarterly Sales of Key
    Products/Franchises exhibit, and every internal check - the year-to-date
    columns, the stated full years - is drawn from that same family of
    documents. Consistent and wrong is a real outcome, so this anchors the two
    largest series to something else entirely: J&J's Form 10-K, which states
    what share of total revenue its biggest products were, in a different
    document, for a different purpose, rounded to a tenth of a percent.

    From the 10-K for fiscal 2022 (filed February 2023): STELARA
    "approximately 10.2%" and DARZALEX "approximately 8.4%" of total revenues.
    From the 10-K for fiscal 2023: 12.8% and 11.4%. Total revenues are J&J's
    own, 94,943 for 2022 and 85,159 for 2023 - the latter lower because
    Consumer Health had been separated.

    If a quarter in either series is ever restated or misread badly enough to
    matter, the share moves off the published tenth and this fails.
    """
    rows = load_jsonl("quarterly_revenue.jsonl")
    total_revenue = {2022: 94_943, 2023: 85_159}
    stated_share = {
        ("Stelara", 2022): 10.2,
        ("Darzalex", 2022): 8.4,
        ("Stelara", 2023): 12.8,
        ("Darzalex", 2023): 11.4,
    }
    for (drug, year), share in stated_share.items():
        quarters = [
            row["value_normalized_usd_millions"]
            for row in rows
            if row["drug_name"] == drug and row["calendar_year"] == year
        ]
        assert len(quarters) == 4, f"{drug} {year}: {len(quarters)} quarters"
        got = round(100 * sum(quarters) / total_revenue[year], 1)
        assert got == share, f"{drug} {year}: {got}% of revenue, 10-K says {share}%"


def test_the_catalog_is_balanced():
    """The four thresholds, asserted rather than reported.

    "Balanced" started as a judgement held outside the repo: I could say the
    dataset was 67% one issuer, but nothing would notice if the next hundred
    rows made it 75% again. These are the bounds the catalog is held to, and
    they are all met - largest issuer under 40%, largest product under 10%,
    largest therapeutic area under 60%, at least six areas.

    The neighbouring test checks the report describes the rows; this one
    checks the rows clear the bar. Both are needed: a correct report of a
    lopsided dataset would pass the first and fail this one.
    """
    report = json.loads((GOLD / "build_report.json").read_text())
    balance = report["concentration"]
    over = {
        key: balance[key.replace("_within_target", "_share")]
        for key, value in balance.items()
        if key.endswith("_within_target") and not value
    }
    assert not over, f"above target: {over}"
    assert balance["therapeutic_areas_within_target"], balance["therapeutic_area_count"]
    assert balance["balanced"]


def test_the_independent_audit_finds_nothing():
    """scripts/audit_gold.py, run in CI.

    The audit checks a different list from this suite: it reads the published
    artifacts as a stranger would and looks for the defect classes that have
    actually shipped here - a stale derivation label, a quote that restates its
    own value, a year that stops reconciling, a series whose scope drifts. Each
    of its checks has been mutation-tested; running it here means a regression
    fails the build rather than waiting for someone to run the script.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "audit_gold.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout


def test_the_catalog_spans_more_than_one_therapeutic_area():
    """A benchmark drawn from one disease measures one disease's paperwork.

    Pulmonary hypertension products share conventions - the same handful of
    issuers, the same schedule shapes, the same slow curves. Comparators from
    other areas break that: hepatitis C collapses as it cures its own market,
    an antifungal holds a plateau for two decades, an angina drug loses 94% of
    a quarter to generic entry. None of those shapes exists in the PAH catalog.
    """
    report = json.loads((GOLD / "build_report.json").read_text())
    areas = set(report["gold_completeness"]["therapeutic_areas"])
    assert len(areas) >= 4, sorted(areas)
    assert "Pulmonary hypertension" in areas


def test_comparators_are_additive_and_never_count_as_catalog_coverage():
    """The completeness claim is about the seed catalog, and only that.

    Before comparators existed, the catalog was defined as "whatever was
    accounted for", which made completeness true by construction. It now reads
    seed/example_drugs.csv, and products outside that file are reported
    separately so they cannot flatter the coverage percentage.
    """
    report = json.loads((GOLD / "build_report.json").read_text())
    completeness = report["gold_completeness"]
    comparators = set(completeness["comparator_products"])

    assert comparators, "this test is vacuous without at least one comparator"
    assert comparators.isdisjoint(seed_names())
    assert completeness["catalog_products"] == len(seed_names())
    assert completeness["accounted_for"] == len(seed_names())
    assert completeness["unaccounted_products"] == []
    # The percentage counts catalog members only.
    catalog = report["catalog_coverage"]
    in_catalog = set(catalog["quarterly_series_products"]) - comparators
    assert catalog["quarterly_series_pct"] == round(
        100 * len(in_catalog) / len(seed_names()), 1
    )


def test_harvoni_is_the_collapse_the_catalog_otherwise_lacks():
    """Twelve quarters from 3,017 to 232, and why it stops there.

    Hepatitis C is curative, so the treatable population shrinks as the drug
    works. No pulmonary hypertension series behaves like this, and a pipeline
    that has only ever seen slow growth has never been asked to read one.
    """
    rows = sorted(
        (r for r in load_jsonl("quarterly_revenue.jsonl") if r["drug_name"] == "Harvoni"),
        key=lambda r: r["period"],
    )
    coverage = {r["drug_name"]: r for r in load_jsonl("series_coverage.jsonl")}["Harvoni"]

    assert len(rows) == 12
    assert rows[0]["value_reported"] == 3017.0
    assert rows[-1]["value_reported"] == 232.0
    assert rows[-1]["value_reported"] < rows[0]["value_reported"] / 10

    # It ends because the line stops meaning Harvoni, not because sourcing did.
    assert coverage["series_end_quarter"] == "2018Q4"
    assert coverage["series_end_basis"] == "issuer_stopped_reporting"
    assert "Asegua" in coverage["series_end_reason"]


def test_every_comparator_year_reconciles_to_its_stated_full_year():
    """The same arithmetic bar the PAH series are held to."""
    stated = {
        ("Ranexa", 2016): 677.0, ("Ranexa", 2017): 717.0,
        ("Ranexa", 2018): 758.0, ("Ranexa", 2019): 216.0,
        ("AmBisome", 2016): 356.0, ("AmBisome", 2017): 366.0,
        ("AmBisome", 2018): 420.0, ("AmBisome", 2019): 407.0,
        ("Harvoni", 2016): 9081.0, ("Harvoni", 2017): 4370.0,
        ("Harvoni", 2018): 1222.0,
    }
    by_year: dict[tuple[str, int], list[float]] = {}
    for row in load_jsonl("quarterly_revenue.jsonl"):
        key = (row["drug_name"], row["calendar_year"])
        if key in stated:
            by_year.setdefault(key, []).append(row["value_reported"])
    assert set(by_year) == set(stated)
    for key, total in stated.items():
        assert len(by_year[key]) == 4, key
        assert sum(by_year[key]) == total, key
