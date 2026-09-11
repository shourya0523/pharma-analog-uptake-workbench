"""Move the XBRL member register from a seed file into the database."""

from sqlalchemy.orm import Session

from alembic import op
from app.db.models import Base

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None

TABLE = "xbrl_member_resolutions"


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, tables=[Base.metadata.tables[TABLE]], checkfirst=True)
    # The reviewed file is the starting position, not the store. Seeding here
    # keeps a fresh database equal to the one the file describes; after this the
    # table leads, because a run can add to it and the file cannot.
    from app.extraction.member_store import seed_from_csv

    with Session(bind=bind) as session:
        seed_from_csv(session)
        session.commit()


def downgrade() -> None:
    op.drop_table(TABLE)
