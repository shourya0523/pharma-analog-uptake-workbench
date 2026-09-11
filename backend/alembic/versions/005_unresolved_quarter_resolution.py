"""Record how a missing quarter was resolved.

An unresolved quarter had no way to say it had been dealt with, so a reviewer who
confirmed a non-disclosure saw the same row again on the next visit. The column
holds what the reviewer decided; a null still means open, which is what every
existing row is.
"""

import sqlalchemy as sa
from alembic import op


revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    # As in 004: a fresh database already has the column from the live models.
    if "resolution" not in _columns("unresolved_quarters"):
        with op.batch_alter_table("unresolved_quarters") as batch_op:
            batch_op.add_column(sa.Column("resolution", sa.String(length=32), nullable=True))


def downgrade() -> None:
    if "resolution" in _columns("unresolved_quarters"):
        with op.batch_alter_table("unresolved_quarters") as batch_op:
            batch_op.drop_column("resolution")
