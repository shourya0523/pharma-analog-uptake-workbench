import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from app.analytics.gold_dataset import (
    lifecycle_record,
    peak_record,
    promote_lifecycle_history,
)
from app.analytics.lifecycle import expected_quarters_for_job, latest_completed_quarter
from app.db.models import (
    Base,
    DatapointORM,
    DrugJobORM,
    ExtractionRunORM,
    UnresolvedQuarterORM,
)
from app.domain.models import (
    Citation,
    PeriodType,
    RevenueCandidate,
    RevenueScope,
    SourceType,
    ValidationStatus,
    new_id,
)
from app.identity.resolver import resolve_product_identity
from app.parsing.fda_label import parse_label_record
from app.parsing.indications import parse_indications
from app.quality.candidate_filters import filter_revenue_candidates
from app.quality.checks import quote_contains_value, run_quality_checks
from sqlalchemy import create_engine, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_DIR = REPO_ROOT / "seed" / "gold"

ALLOWED_GOLD_SOURCE_TYPES = {
    SourceType.SEC_FILING,
    SourceType.EARNINGS_RELEASE,
    SourceType.LLM_SEARCH,
    SourceType.COMPANY_IR,
}


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _seed_drug_names() -> set[str]:
    with (REPO_ROOT / "seed" / "example_drugs.csv").open(newline="") as handle:
        return {row["drug_name"] for row in csv.DictReader(handle)}


def _column_keys(model) -> set[str]:
    return {column.key for column in sa_inspect(model).mapper.column_attrs}


def _intersect_payload(row: dict, fields: set[str]) -> dict:
    return {key: row[key] for key in fields if key in row}


def _revenue_candidate_from_gold(row: dict) -> RevenueCandidate:
    payload = _intersect_payload(row, set(RevenueCandidate.model_fields))
    if "confidence_score" in row:
        payload["confidence"] = row["confidence_score"]
    return RevenueCandidate.model_validate(payload)


def _citation_from_gold(row: dict) -> Citation:
    payload = _intersect_payload(row, set(Citation.model_fields))
    payload["source_id"] = row["gold_id"]
    payload["confidence"] = row["confidence_score"]
    return Citation.model_validate(payload)


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

        candidate = _revenue_candidate_from_gold(row)
        citation = _citation_from_gold(row)
        assert PeriodType(row["period_type"]) is PeriodType.QUARTERLY
        assert RevenueScope(row["revenue_scope"])
        assert SourceType(row["source_type"]) in ALLOWED_GOLD_SOURCE_TYPES
        assert ValidationStatus(row["validation_status"]) is ValidationStatus.CONFIRMED
        assert row["extraction_method"] == "independent_filing_research"
        assert quote_contains_value(candidate.source_quote, candidate.value_reported)
        assert citation.source_url == row["source_url"]

        kept, dropped = filter_revenue_candidates(
            [candidate.model_dump()],
            product=row["drug_name"],
            generic=row["generic_name"],
        )
        assert len(kept) == 1
        assert not dropped

    by_drug: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_drug[row["drug_name"]].append(row)
    for drug_rows in by_drug.values():
        issues = run_quality_checks(drug_rows)
        assert not issues, [issue.issue_type for issue in issues]


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
    lifecycle = _load_jsonl(GOLD_DIR / manifest["lifecycle_file"])
    seed_drugs = _seed_drug_names()

    assert manifest["coverage_mode"] == "full_lifecycle"
    assert manifest["generation"] == "independent_filing_research"
    assert "pipeline" in manifest["purpose"].lower()
    assert len(seed_drugs) == manifest["target_drug_count"]
    assert {row["drug_name"] for row in lifecycle} == seed_drugs
    as_of = date.fromisoformat(manifest["as_of_date"])
    assert manifest["as_of_quarter"] == latest_completed_quarter(as_of)

    for row in lifecycle:
        approval = date.fromisoformat(row["fda_approval_date"]) if row.get("fda_approval_date") else None
        reported_p = {item["period"] for item in reported if item["drug_name"] == row["drug_name"]}
        unresolved_p = {item["period"] for item in unresolved if item["drug_name"] == row["drug_name"]}
        expected = expected_quarters_for_job(
            approval_date=approval,
            known_periods=sorted(reported_p | unresolved_p),
            as_of=as_of,
            lifecycle_coverage=True,
        )
        rebuilt = lifecycle_record(
            drug_name=row["drug_name"],
            approval_date=approval,
            as_of=as_of,
            reported=reported_p,
            unresolved=unresolved_p,
            approval_source_url=row.get("approval_source_url"),
        )
        assert row["expected_quarter_count"] == rebuilt["expected_quarter_count"]
        assert row["lifecycle_start_quarter"] == rebuilt["lifecycle_start_quarter"]
        assert row["lifecycle_end_quarter"] == rebuilt["lifecycle_end_quarter"]
        assert set(expected) == (reported_p | unresolved_p)
        assert reported_p <= set(expected)


def test_gold_peak_sales_use_production_peak_selection():
    manifest = json.loads((GOLD_DIR / "manifest.json").read_text())
    reported = _load_jsonl(GOLD_DIR / manifest["reported_rows_file"])
    peaks = _load_jsonl(GOLD_DIR / manifest["peak_sales_file"])
    lifecycle = {row["drug_name"]: row for row in _load_jsonl(GOLD_DIR / manifest["lifecycle_file"])}
    as_of = date.fromisoformat(manifest["as_of_date"])

    assert {row["drug_name"] for row in peaks} == _seed_drug_names()
    for row in peaks:
        rebuilt = peak_record(
            drug_name=row["drug_name"],
            rows=[item for item in reported if item["drug_name"] == row["drug_name"]],
            as_of=as_of,
            expected_count=lifecycle[row["drug_name"]]["expected_quarter_count"],
        )
        assert row["selection_method"] == rebuilt["selection_method"]
        assert row["estimate_type"] == rebuilt["estimate_type"]
        assert row["value"] == rebuilt["value"]
        assert row["peak_eligible"] == rebuilt["peak_eligible"]
        assert row["complete_comparable_years"] == rebuilt["complete_comparable_years"]
        assert lifecycle[row["drug_name"]]["peak_eligible"] == rebuilt["peak_eligible"]


def test_promoted_lifecycle_history_lands_in_gold_revenue():
    archive_edges = GOLD_DIR / "archive" / "window-2022-2026" / "edge_cases.jsonl"
    edges = _load_jsonl(archive_edges if archive_edges.is_file() else GOLD_DIR / "edge_cases.jsonl")
    reported = _load_jsonl(GOLD_DIR / "quarterly_revenue.jsonl")
    keys = {(row["drug_name"], row["period"]) for row in reported}
    promoted = [promote_lifecycle_history(edge) for edge in edges]
    promoted = [row for row in promoted if row]
    assert promoted, "USD issuer history previously excluded by the year window should become gold"
    for row in promoted:
        assert (row["drug_name"], row["period"]) in keys


def test_gold_edge_cases_have_expected_disposition():
    manifest = json.loads((GOLD_DIR / "manifest.json").read_text())
    rows = _load_jsonl(GOLD_DIR / manifest["edge_cases_file"])

    assert len({row["edge_id"] for row in rows}) == len(rows)
    for row in rows:
        candidate = _revenue_candidate_from_gold(row["candidate"])
        kept, dropped = filter_revenue_candidates(
            [candidate.model_dump()],
            product=row["target_drug"],
            generic=row["generic_name"],
        )

        if row["case_type"] == "cross_currency_unresolved":
            assert len(kept) == 1
            assert not dropped
        else:
            assert not kept
            assert len(dropped) == 1
            assert dropped[0]["_drop_reason"] == row["expected_reason"]


def test_gold_metadata_uses_production_parsers_and_identity_resolver():
    rows = _load_jsonl(GOLD_DIR / "metadata.jsonl")
    assert len({row["gold_id"] for row in rows}) == len(rows)
    assert all(row["source_url"].startswith("https://") for row in rows)

    for row in rows:
        if row["case_type"] == "label":
            parsed = parse_label_record(row["record"])
            expected = row["expected"]
            if "epc_terms" in expected:
                assert parsed.epc_terms == expected["epc_terms"]
                assert parsed.moa_terms == expected["moa_terms"]
            else:
                assert len(parsed.active_ingredients) == expected["ingredient_count"]
                assert len(parsed.moa_terms) == expected["moa_count"]
        elif row["case_type"] == "indication":
            parsed = parse_indications(row["text"])[0]
            assert parsed.approved_lot.value.value == row["expected"]["approved_lot"]
            assert parsed.setting == row["expected"]["setting"]
            assert parsed.population == row["expected"]["population"]
        elif row["case_type"] == "identity":
            identities = [resolve_product_identity(**product) for product in row["products"]]
            assert identities[0].analog_family_key == identities[1].analog_family_key
            assert identities[0].identity_key != identities[1].identity_key


def _parse_json_cell(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def test_gold_sqlite_roundtrip_reads_every_field(tmp_path):
    manifest = json.loads((GOLD_DIR / "manifest.json").read_text())
    reported = _load_jsonl(GOLD_DIR / manifest["reported_rows_file"])
    unresolved = _load_jsonl(GOLD_DIR / manifest["unresolved_rows_file"])
    edges = _load_jsonl(GOLD_DIR / manifest["edge_cases_file"])
    seed_by_name = {}
    with (REPO_ROOT / "seed" / "example_drugs.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            seed_by_name[row["drug_name"]] = row

    datapoint_fields = _column_keys(DatapointORM)
    unresolved_fields = _column_keys(UnresolvedQuarterORM)
    engine = create_engine(f"sqlite:///{tmp_path / 'gold_smoke.db'}", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()

    run = ExtractionRunORM(id=new_id(), status="completed", options_json={"source": "gold_smoke"})
    db.add(run)
    jobs: dict[str, DrugJobORM] = {}
    for drug_name, seed in seed_by_name.items():
        job = DrugJobORM(
            id=new_id(),
            run_id=run.id,
            drug_name=drug_name,
            generic_name=seed.get("generic_name") or None,
            manufacturer=seed.get("manufacturer") or None,
            ticker=seed.get("ticker") or None,
            indication=seed.get("indication") or None,
            status="completed",
            current_step="ready_for_review",
        )
        db.add(job)
        jobs[drug_name] = job
    db.flush()

    for row in reported:
        citation = _citation_from_gold(row)
        payload = _intersect_payload(row, datapoint_fields)
        db.add(
            DatapointORM(
                id=new_id(),
                job_id=jobs[row["drug_name"]].id,
                source_id=None,
                citation_json={**citation.model_dump(mode="json"), "gold_id": row["gold_id"]},
                issue_flags=[],
                reviewer_notes=row.get("gold_notes"),
                **payload,
            )
        )
        jobs[row["drug_name"]].candidates_extracted += 1
        jobs[row["drug_name"]].auto_pass_count += 1

    for row in unresolved:
        payload = _intersect_payload(row, unresolved_fields)
        db.add(
            UnresolvedQuarterORM(
                id=new_id(),
                job_id=jobs[row["drug_name"]].id,
                reviewer_notes=row.get("gold_notes"),
                **payload,
            )
        )
        jobs[row["drug_name"]].unresolved_count += 1

    for row in edges:
        candidate = {**row["candidate"], "source_url": row["source_url"]}
        payload = _intersect_payload(candidate, datapoint_fields)
        payload.setdefault("extraction_method", "manual_gold_search")
        payload.setdefault("confidence_score", 0.0)
        payload.setdefault(
            "validation_status",
            "rejected" if row["case_type"] not in {"old_record", "cross_currency_unresolved"} else "needs_review",
        )
        db.add(
            DatapointORM(
                id=new_id(),
                job_id=jobs[row["target_drug"]].id,
                source_id=None,
                citation_json={
                    "gold_id": row["edge_id"],
                    "source_url": row["source_url"],
                    "source_title": row["source_title"],
                },
                issue_flags=[row["expected_reason"]],
                reviewer_notes=row.get("edge_notes"),
                **payload,
            )
        )

    db.commit()

    sql_datapoints = db.execute(
        text(
            """
            SELECT d.*, j.drug_name, j.generic_name, j.manufacturer
            FROM datapoints d
            JOIN drug_jobs j ON j.id = d.job_id
            """
        )
    ).mappings().all()
    sql_unresolved = db.execute(
        text(
            """
            SELECT u.*, j.drug_name
            FROM unresolved_quarters u
            JOIN drug_jobs j ON j.id = u.job_id
            """
        )
    ).mappings().all()
    sql_jobs = db.execute(text("SELECT drug_name, candidates_extracted, unresolved_count FROM drug_jobs")).mappings().all()

    reported_ids = {row["gold_id"] for row in reported}
    edge_ids = {row["edge_id"] for row in edges}
    reported_from_db = [row for row in sql_datapoints if _parse_json_cell(row["citation_json"]).get("gold_id") in reported_ids]
    edge_from_db = [row for row in sql_datapoints if _parse_json_cell(row["citation_json"]).get("gold_id") in edge_ids]

    assert len(reported_from_db) == len(reported)
    assert len(sql_unresolved) == len(unresolved)
    assert len(edge_from_db) == len(edges)
    assert {row["drug_name"] for row in sql_jobs} == _seed_drug_names()

    compare_fields = sorted((datapoint_fields & set(reported[0])) - {"id", "job_id"})
    reported_index = {row["gold_id"]: row for row in reported}
    for db_row in reported_from_db:
        gold = reported_index[_parse_json_cell(db_row["citation_json"])["gold_id"]]
        assert db_row["drug_name"] == gold["drug_name"]
        assert db_row["generic_name"] == gold["generic_name"]
        for field in compare_fields:
            left, right = db_row[field], gold[field]
            if isinstance(left, float) and isinstance(right, (int, float)):
                assert left == float(right)
            else:
                assert left == right
        citation = Citation.model_validate(_parse_json_cell(db_row["citation_json"]))
        assert citation.source_url == gold["source_url"]
        assert quote_contains_value(db_row["source_quote"], db_row["value_reported"])
        assert not run_quality_checks([dict(db_row)])

        candidate = _revenue_candidate_from_gold(dict(db_row))
        kept, dropped = filter_revenue_candidates(
            [candidate.model_dump()],
            product=db_row["drug_name"],
            generic=db_row["generic_name"],
        )
        assert len(kept) == 1
        assert not dropped

    for db_row in sql_unresolved:
        gold = next(
            row
            for row in unresolved
            if row["drug_name"] == db_row["drug_name"] and row["period"] == db_row["period"]
        )
        assert db_row["reason_unresolved"] == gold["reason_unresolved"]
        assert db_row["recommended_next_step"] == gold["recommended_next_step"]
        assert db_row["confidence_that_unavailable"] == gold["confidence_that_unavailable"]
        sources = _parse_json_cell(db_row["sources_checked"])
        assert sources == gold["sources_checked"]
        assert all(source["source_url"].startswith("https://") for source in sources)
        assert gold["gold_notes"] in (db_row["reviewer_notes"] or "")

    by_drug_sql: dict[str, list[dict]] = defaultdict(list)
    for row in reported_from_db:
        by_drug_sql[row["drug_name"]].append(dict(row))
    for drug_rows in by_drug_sql.values():
        issues = run_quality_checks(drug_rows)
        assert not issues, [issue.issue_type for issue in issues]

    db.close()
