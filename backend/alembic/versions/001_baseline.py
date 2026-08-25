"""Establish the legacy workbench schema baseline."""

from alembic import op

from app.db.migrations import BASELINE_TABLES
from app.db.models import Base


revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(
        bind=bind,
        tables=[Base.metadata.tables[name] for name in sorted(BASELINE_TABLES)],
        checkfirst=True,
    )


def downgrade() -> None:
    for name in reversed(sorted(BASELINE_TABLES)):
        op.drop_table(name)
