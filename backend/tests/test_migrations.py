from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from app.db.migrations import BASELINE_TABLES, SchemaMismatchError, upgrade_database


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
        assert conn.execute(text("SELECT status FROM extraction_runs WHERE id='run-1'")).scalar_one() == "completed"


def test_unknown_unversioned_schema_is_refused(tmp_path: Path):
    engine = create_engine(_url(tmp_path / "unknown.db"))
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE extraction_runs (id VARCHAR(36) PRIMARY KEY, surprise TEXT)"))

    with pytest.raises(SchemaMismatchError, match="does not match the supported baseline"):
        upgrade_database(engine)

