from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from app.db.migrations import (
    BASELINE_001_COLUMNS,
    BASELINE_TABLES,
    SchemaMismatchError,
    upgrade_database,
)
from app.db.models import EvidenceAssertionORM


def _url(path: Path) -> str:
    return f"sqlite:///{path}"


def test_empty_database_upgrades_to_normalized_schema(tmp_path: Path):
    engine = create_engine(_url(tmp_path / "fresh.db"))

    upgrade_database(engine)

    tables = set(inspect(engine).get_table_names())
    assert BASELINE_TABLES <= tables
    assert {
        "analog_families",
        "canonical_products",
        "product_formulations",
        "product_indications",
        "moa_components",
        "peak_sales_estimates",
        "competitive_snapshots",
        "uptake_metrics",
        "evidence_assertions",
        "derivation_lineage",
        "alembic_version",
    } <= tables
    formulation = next(
        column
        for column in inspect(engine).get_columns("datapoints")
        if column["name"] == "formulation"
    )
    assert formulation["type"].length == 512


def test_existing_baseline_rows_survive_upgrade(tmp_path: Path):
    engine = create_engine(_url(tmp_path / "existing.db"))
    upgrade_database(engine, target_revision="001")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO extraction_runs "
                "(id, status, options_json, created_at, updated_at) "
                "VALUES ('run-1', 'completed', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    upgrade_database(engine)

    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT status FROM extraction_runs WHERE id='run-1'")
            ).scalar_one()
            == "completed"
        )


def test_recorded_baseline_is_still_a_subset_of_what_001_creates(tmp_path: Path):
    """The recorded columns must stay real.

    Revision 001 builds from the live models, so what it creates grows as they
    do and can never equal a frozen list. What must hold is that every column
    the guard expects still exists - a renamed or dropped baseline column would
    otherwise leave the guard describing a schema nobody has.
    """

    engine = create_engine(_url(tmp_path / "baseline.db"))
    upgrade_database(engine, target_revision="001")

    inspector = inspect(engine)
    for table in sorted(BASELINE_TABLES):
        actual = {column["name"] for column in inspector.get_columns(table)}
        assert set(BASELINE_001_COLUMNS[table]) <= actual, table


def test_legacy_baseline_still_stamps_after_a_column_is_added_to_a_baseline_table(
    tmp_path: Path,
):
    """A database frozen at an older revision is old, not corrupt.

    drug_jobs gained product_id in 004. An unversioned copy that predates it has
    to be recognised and stamped, which it was not while the expected
    fingerprint was read back from the live models.
    """

    engine = create_engine(_url(tmp_path / "legacy.db"))
    # Built from the recorded columns rather than from today's models, which is
    # the whole point: this is the shape a database on disk actually has.
    with engine.begin() as conn:
        for table in sorted(BASELINE_TABLES):
            columns = ", ".join(
                f"{name} TEXT PRIMARY KEY" if name == "id" else f"{name} TEXT"
                for name in BASELINE_001_COLUMNS[table]
            )
            conn.execute(text(f"CREATE TABLE {table} ({columns})"))

    upgrade_database(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("drug_jobs")}
    assert "product_id" in columns


def test_unknown_unversioned_schema_is_refused(tmp_path: Path):
    engine = create_engine(_url(tmp_path / "unknown.db"))
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE extraction_runs (id VARCHAR(36) PRIMARY KEY, surprise TEXT)"
            )
        )

    with pytest.raises(
        SchemaMismatchError, match="does not match the supported baseline"
    ):
        upgrade_database(engine)


def test_evidence_assertion_uses_scalar_hash_for_postgresql_uniqueness(tmp_path: Path):
    ddl = str(
        CreateTable(EvidenceAssertionORM.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    unique_clause = next(
        line for line in ddl.splitlines() if line.strip().startswith("UNIQUE")
    )
    assert "value_hash" in unique_clause
    assert "value_json" not in unique_clause

    engine = create_engine(_url(tmp_path / "evidence.db"))
    upgrade_database(engine)
    with Session(engine) as db:
        assertion = EvidenceAssertionORM(
            id="assertion-1",
            entity_type="product",
            entity_id="product-1",
            field_name="moa",
            value_json={"components": ["A", "B"]},
            source_id="source-1",
            source_url="https://example.test/label",
            extraction_method="structured_fda",
        )
        db.add(assertion)
        db.commit()
        assert len(assertion.value_hash) == 64
