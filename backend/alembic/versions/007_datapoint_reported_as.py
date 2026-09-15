"""What a published figure is a figure for, where that is not the job's product.

A filer that sells two products together prints one line for both. The figure
is the pair's, and nobody publishes the split, so the row is published as the
pair - and this column says what it is a figure for, in the filer's own words.
Null means the figure is the job's product's own, which is what every existing
row is.
"""

import sqlalchemy as sa

from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    # As in 005 and 006: a fresh database already has the column from the
    # live models, because the baseline builds its tables from them.
    if "reported_as" not in _columns("datapoints"):
        with op.batch_alter_table("datapoints") as batch_op:
            batch_op.add_column(sa.Column("reported_as", sa.String(length=512), nullable=True))


def downgrade() -> None:
    if "reported_as" in _columns("datapoints"):
        with op.batch_alter_table("datapoints") as batch_op:
            batch_op.drop_column("reported_as")
