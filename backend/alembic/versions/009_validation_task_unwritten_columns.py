"""The three columns a held row's reason was never written into.

`validation_tasks` carried `judge_status`, `deterministic_results` and
`issues` from the baseline. The only code that creates a task supplies none of
them and nothing reads them, so every stored row leaves all three at their
default and a reviewer is given the `reason` string alone.

They are dropped rather than left empty because an empty column reads as a
question nobody has answered yet, and this one cannot be: there is no writer
to wait for.

A database created from the current models never had them - revision 001
builds its tables from the models - so each drop is guarded by what the table
actually holds.
"""

import sqlalchemy as sa

from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None

_COLUMNS = {
    "judge_status": sa.String(length=64),
    "deterministic_results": sa.JSON(),
    "issues": sa.JSON(),
}


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    present = _columns("validation_tasks")
    dropping = [name for name in _COLUMNS if name in present]
    if dropping:
        with op.batch_alter_table("validation_tasks") as batch_op:
            for name in dropping:
                batch_op.drop_column(name)


def downgrade() -> None:
    present = _columns("validation_tasks")
    missing = {name: type_ for name, type_ in _COLUMNS.items() if name not in present}
    if missing:
        with op.batch_alter_table("validation_tasks") as batch_op:
            for name, type_ in missing.items():
                batch_op.add_column(sa.Column(name, type_, nullable=True))
