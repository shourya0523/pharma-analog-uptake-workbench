import ast
import csv
import importlib.util
import json
import re
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
    manifest = json.loads((GOLD / "manifest.json").read_text())
    peaks = load_jsonl(manifest["peak_sales_file"])
    excluded = load_jsonl(manifest["excluded_products_file"])
    included_names = {row["drug_name"] for row in peaks}
    excluded_names = {row["drug_name"] for row in excluded}
    assert included_names.isdisjoint(excluded_names)
    assert included_names | excluded_names == seed_names()
    assert manifest["target_product_count"] == len(seed_names())
    assert all(row["benchmark_eligible"] for row in peaks)


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
        assert row["source_unit"] in {"thousands", "millions"}
        assert row["sources"]
        # test_gold_rows_have_independent_provenance_and_citations only checks
        # that source_quote contains source_value_reported (the pre-conversion
        # number) — it never checks that value_reported is source_value_reported
        # scaled correctly by source_unit. A wrong source_unit (e.g. UTHR's 2016
        # rows, once misclassified as "thousands" when the exhibit had already
        # switched to millions) would pass that check while shipping a value
        # 1000x too small. Guard the actual scale relationship here instead.
        expected = (
            row["source_value_reported"] / 1000
            if row["source_unit"] == "thousands"
            else row["source_value_reported"]
        )
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
    assert (
        len(catalog["quarterly_series_products"])
        + len(catalog["annual_only_products"])
        + len(catalog["excluded_products"])
        == len(seed_names())
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
