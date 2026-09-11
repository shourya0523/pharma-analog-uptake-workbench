from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import Engine, inspect

from alembic import command

BASELINE_TABLES = {
    "extraction_runs",
    "drug_jobs",
    "drug_profile_fields",
    "source_documents",
    "datapoints",
    "validation_tasks",
    "quality_checks",
    "unresolved_quarters",
    "review_events",
    "exports",
}


class SchemaMismatchError(RuntimeError):
    """Raised when an unversioned database is not the supported legacy schema."""


def _config(engine: Engine) -> Config:
    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.attributes["connection"] = engine
    return config


def _fingerprint(engine: Engine, tables: set[str]) -> dict[str, tuple[str, ...]]:
    inspector = inspect(engine)
    return {
        table: tuple(sorted(column["name"] for column in inspector.get_columns(table)))
        for table in sorted(tables)
    }


# The columns revision 001 actually creates. This is a historical fact, so it is
# recorded rather than derived from the live models: reading it back from
# Base.metadata made every later column added to a baseline table look like a
# corrupt legacy database, and refused to stamp one that was merely old.
BASELINE_001_COLUMNS: dict[str, tuple[str, ...]] = {
    "datapoints": (
        "calendar_quarter", "calendar_year", "citation_json", "confidence_score",
        "currency", "extraction_method", "fiscal_quarter", "fiscal_year",
        "formulation", "geography", "id", "issue_flags", "job_id", "metric",
        "period", "period_type", "revenue_scope", "reviewer_notes",
        "route_of_administration", "source_id", "source_quote", "source_support",
        "source_url", "unit", "validation_status",
        "value_normalized_usd_millions", "value_reported",
    ),
    "drug_jobs": (
        "auto_pass_count", "candidates_extracted", "cik", "completeness_pct",
        "created_at", "current_step", "drug_name", "error", "generic_name", "id",
        "indication", "known_source_url", "manufacturer", "needs_review_count",
        "quality_flags", "run_id", "sources_found", "status", "ticker",
        "unresolved_count", "updated_at",
    ),
    "drug_profile_fields": (
        "citation_json", "field", "id", "job_id", "validation_status", "value",
    ),
    "exports": ("created_at", "format", "id", "job_id", "run_id", "status", "storage_key"),
    "extraction_runs": ("created_at", "error", "id", "options_json", "status", "updated_at"),
    "quality_checks": (
        "affected_datapoint", "explanation", "id", "issue_type", "job_id",
        "recommended_action", "severity", "status",
    ),
    "review_events": (
        "action", "after_json", "before_json", "created_at", "datapoint_id", "id",
        "job_id", "notes",
    ),
    "source_documents": (
        "accession_number", "filing_type", "id", "job_id", "metadata_json", "notes",
        "page_or_section", "parsing_status", "relevant_datapoints_found",
        "retrieval_status", "source_date", "source_title", "source_type",
        "source_url", "storage_key",
    ),
    "unresolved_quarters": (
        "confidence_that_unavailable", "id", "job_id", "period", "reason_unresolved",
        "recommended_next_step", "reviewer_notes", "sources_checked",
    ),
    "validation_tasks": (
        "confidence_score", "datapoint_id", "deterministic_results", "id", "issues",
        "job_id", "judge_status", "reason", "reviewer_notes", "status",
    ),
}


def _expected_baseline_fingerprint() -> dict[str, tuple[str, ...]]:
    return {name: BASELINE_001_COLUMNS[name] for name in sorted(BASELINE_TABLES)}


def _is_recognized_baseline(engine: Engine, tables: set[str]) -> bool:
    """Whether an unversioned database is the baseline, possibly a later one.

    Every baseline column has to be there, but extra ones are fine: revisions
    add columns to baseline tables, so a database that stopped being stamped
    after one of those is still the schema this project wrote, not a foreign
    one. Requiring an exact match would refuse it, and refusing means telling
    someone to reset a database that was only ever old.
    """

    actual = _fingerprint(engine, tables)
    expected = _expected_baseline_fingerprint()
    if set(actual) != set(expected):
        return False
    return all(set(expected[table]) <= set(columns) for table, columns in actual.items())


def upgrade_database(engine: Engine, target_revision: str = "head") -> None:
    """Upgrade a fresh or recognized legacy database through Alembic."""

    tables = set(inspect(engine).get_table_names())
    config = _config(engine)
    if tables and "alembic_version" not in tables:
        actual_tables = tables & BASELINE_TABLES
        if tables != BASELINE_TABLES or not _is_recognized_baseline(engine, actual_tables):
            raise SchemaMismatchError(
                "Unversioned database does not match the supported baseline. "
                "Back up the database and use the documented export/reset remediation."
            )
        command.stamp(config, "001")
    command.upgrade(config, target_revision)

