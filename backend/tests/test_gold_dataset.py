import ast
import csv
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
        expected = set(builder.quarter_range(row["commercial_start_quarter"], row["as_of_quarter"]))
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
