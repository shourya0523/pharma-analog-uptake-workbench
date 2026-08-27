from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.models import Base


config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        with supplied.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=connection.dialect.name == "sqlite",
            )
            with context.begin_transaction():
                context.run_migrations()
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
