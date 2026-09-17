"""Which series a figure belongs to, and which figure the series holds.

Three columns on `datapoints`. `series_identity` is what makes two readings
readings of one curve - issuer, product, scope, place, formulation, the line
the filer reported it as, currency and period basis in one key.
`series_selection` says what the series does with the row: the figure it holds
for that quarter, another reading of the same figure, or a reading a stronger
one superseded. `geography_normalized` is the place in a controlled
vocabulary, beside the filer's own words for it in `geography`.

Null in all three is what every existing row is: a row written before anything
decided. Readers treat that as undecided rather than as a decision against the
row, so an older database still shows its series.
"""

import sqlalchemy as sa

from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

_COLUMNS = {
    "geography_normalized": sa.String(length=128),
    "series_identity": sa.String(length=512),
    "series_selection": sa.String(length=32),
}


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    # As in 005, 006 and 007: a fresh database already has these from the live
    # models, because the baseline builds its tables from them.
    present = _columns("datapoints")
    missing = {name: type_ for name, type_ in _COLUMNS.items() if name not in present}
    if missing:
        with op.batch_alter_table("datapoints") as batch_op:
            for name, type_ in missing.items():
                batch_op.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    present = _columns("datapoints")
    dropping = [name for name in _COLUMNS if name in present]
    if dropping:
        with op.batch_alter_table("datapoints") as batch_op:
            for name in dropping:
                batch_op.drop_column(name)
