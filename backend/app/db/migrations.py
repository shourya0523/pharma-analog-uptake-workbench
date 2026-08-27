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


def _expected_baseline_fingerprint() -> dict[str, tuple[str, ...]]:
    from app.db.models import Base

    return {
        name: tuple(sorted(column.name for column in Base.metadata.tables[name].columns))
        for name in sorted(BASELINE_TABLES)
    }


def upgrade_database(engine: Engine, target_revision: str = "head") -> None:
    """Upgrade a fresh or recognized legacy database through Alembic."""

    tables = set(inspect(engine).get_table_names())
    config = _config(engine)
    if tables and "alembic_version" not in tables:
        actual_tables = tables & BASELINE_TABLES
        if tables != BASELINE_TABLES or _fingerprint(engine, actual_tables) != _expected_baseline_fingerprint():
            raise SchemaMismatchError(
                "Unversioned database does not match the supported baseline. "
                "Back up the database and use the documented export/reset remediation."
            )
        command.stamp(config, "001")
    command.upgrade(config, target_revision)

