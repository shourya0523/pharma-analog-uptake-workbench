"""Link a job to its canonical product, and give a product a cadence.

The pipeline already resolves a canonical product during identity, but nothing
recorded which product a job belonged to, so everything a job produced -
datapoints, profile fields, unresolved quarters - could only be reached through
the run that happened to produce it. The column closes that gap; the backfill
links the rows that predate it by the same identity the resolver would have
assigned, and leaves a job alone when the match is not unique.
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


def _indexes(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    # Revisions 001 and 002 build their tables from the live models rather than
    # from DDL frozen at the time they were written, so a fresh database already
    # has both columns by the time this revision runs and only an existing one
    # needs them added. Adding them conditionally is what makes both paths work.
    if "product_id" not in _columns("drug_jobs"):
        with op.batch_alter_table("drug_jobs") as batch_op:
            batch_op.add_column(sa.Column("product_id", sa.String(length=36), nullable=True))
    if "ix_drug_jobs_product_id" not in _indexes("drug_jobs"):
        op.create_index("ix_drug_jobs_product_id", "drug_jobs", ["product_id"])

    if "cadence" not in _columns("canonical_products"):
        with op.batch_alter_table("canonical_products") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "cadence",
                    sa.String(length=32),
                    nullable=False,
                    server_default="one_off",
                )
            )

    # Link existing jobs by brand name, case-insensitively, and only where the
    # name picks out exactly one product: a job whose name matches two products
    # is left null rather than attached to the wrong one.
    op.execute(
        """
        UPDATE drug_jobs
           SET product_id = (
                 SELECT cp.id
                   FROM canonical_products cp
                  WHERE LOWER(cp.canonical_name) = LOWER(drug_jobs.drug_name)
               )
         WHERE product_id IS NULL
           AND (
                 SELECT COUNT(*)
                   FROM canonical_products cp
                  WHERE LOWER(cp.canonical_name) = LOWER(drug_jobs.drug_name)
               ) = 1
        """
    )


def downgrade() -> None:
    if "cadence" in _columns("canonical_products"):
        with op.batch_alter_table("canonical_products") as batch_op:
            batch_op.drop_column("cadence")

    if "ix_drug_jobs_product_id" in _indexes("drug_jobs"):
        op.drop_index("ix_drug_jobs_product_id", table_name="drug_jobs")
    if "product_id" in _columns("drug_jobs"):
        with op.batch_alter_table("drug_jobs") as batch_op:
            batch_op.drop_column("product_id")
