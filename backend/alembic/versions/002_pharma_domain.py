"""Add normalized pharmaceutical domain entities."""

from alembic import op

from app.db.migrations import BASELINE_TABLES
from app.db.models import Base


revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

NORMALIZED_TABLES = [
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
]


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(
        bind=bind,
        tables=[Base.metadata.tables[name] for name in NORMALIZED_TABLES],
        checkfirst=True,
    )


def downgrade() -> None:
    for name in reversed(NORMALIZED_TABLES):
        if name not in BASELINE_TABLES:
            op.drop_table(name)
