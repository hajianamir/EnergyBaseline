"""Alembic environment. Uses the centralised resolver (SQLite fallback)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from energybaseline.config import ensure_dirs, get_database_url, get_engine_url
from energybaseline.db.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    explicit = config.attributes.get("eb_target_url")
    if explicit:
        return str(explicit)
    ensure_dirs()
    return get_engine_url(get_database_url())


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
