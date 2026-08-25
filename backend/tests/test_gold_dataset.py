import csv
import json
from pathlib import Path

from app.domain.models import RevenueCandidate
from app.quality.candidate_filters import filter_revenue_candidates
from app.quality.checks import quote_contains_value, run_quality_checks

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_DIR = REPO_ROOT / "seed" / "gold"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _seed_drug_names() -> set[str]:
    with (REPO_ROOT / "seed" / "example_drugs.csv").open(newline="") as handle:
        return {row["drug_name"] for row in csv.DictReader(handle)}


def test_gold_revenue_rows_pass_production_validation():
    rows = _load_jsonl(GOLD_DIR / "quarterly_revenue.jsonl")

    assert {row["drug_name"] for row in rows} <= _seed_drug_names()
    assert len({row["gold_id"] for row in rows}) == len(rows)

    keys = set()
    for row in rows:
        key = (
            row["drug_name"],
            row["period"],
            row["revenue_scope"],
            row["geography"],
            row["formulation"],
        )
        assert key not in keys
        keys.add(key)

        candidate = RevenueCandidate.model_validate(row)
        assert quote_contains_value(candidate.source_quote, candidate.value_reported)

        kept, dropped = filter_revenue_candidates(
            [candidate.model_dump()],
            product=row["drug_name"],
            generic=row["generic_name"],
        )
        assert len(kept) == 1
        assert not dropped

        issues = run_quality_checks([row])
        assert not [issue for issue in issues if issue.severity == "high"]


def test_gold_unresolved_rows_are_explicit_non_disclosures():
    rows = _load_jsonl(GOLD_DIR / "unresolved_quarters.jsonl")

    assert {row["drug_name"] for row in rows} <= _seed_drug_names()
    assert len({row["gold_id"] for row in rows}) == len(rows)

    for row in rows:
        assert row["sources_checked"]
        assert row["recommended_next_step"]
        assert 0.0 <= row["confidence_that_unavailable"] <= 1.0
        assert "not a zero-revenue label" in row["gold_notes"]
        assert all(source["source_url"].startswith("https://") for source in row["sources_checked"])


def test_gold_scope_matches_manifest():
    manifest = json.loads((GOLD_DIR / "manifest.json").read_text())
    reported = _load_jsonl(GOLD_DIR / manifest["reported_rows_file"])
    unresolved = _load_jsonl(GOLD_DIR / manifest["unresolved_rows_file"])
    seed_drugs = _seed_drug_names()

    covered_drugs = {row["drug_name"] for row in reported + unresolved}
    assert len(seed_drugs) == manifest["target_drug_count"]
    assert covered_drugs == seed_drugs

    years = [int(row["period"][:4]) for row in reported + unresolved]
    assert min(years) >= manifest["start_year"]
    assert max(years) <= manifest["end_year"]
    assert max(years) - min(years) + 1 <= manifest["max_year_span"]


def test_gold_edge_cases_have_expected_disposition():
    manifest = json.loads((GOLD_DIR / "manifest.json").read_text())
    rows = _load_jsonl(GOLD_DIR / manifest["edge_cases_file"])

    assert len({row["edge_id"] for row in rows}) == len(rows)
    for row in rows:
        candidate = RevenueCandidate.model_validate(row["candidate"])
        kept, dropped = filter_revenue_candidates(
            [candidate.model_dump()],
            product=row["target_drug"],
            generic=row["generic_name"],
        )

        if row["case_type"] == "old_record":
            assert not (manifest["start_year"] <= candidate.calendar_year <= manifest["end_year"])
            assert len(kept) == 1
            assert not dropped
        else:
            assert not kept
            assert len(dropped) == 1
            assert dropped[0]["_drop_reason"] == row["expected_reason"]
