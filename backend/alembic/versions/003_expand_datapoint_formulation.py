"""Expand datapoint formulation for multi-value enrichment."""

import sqlalchemy as sa
from alembic import op


revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("datapoints") as batch_op:
        batch_op.alter_column(
            "formulation",
            existing_type=sa.String(length=128),
            type_=sa.String(length=512),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("datapoints") as batch_op:
        batch_op.alter_column(
            "formulation",
            existing_type=sa.String(length=512),
            type_=sa.String(length=128),
            existing_nullable=True,
        )
