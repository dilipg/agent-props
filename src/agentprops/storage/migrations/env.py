"""Alembic's entry point. Reads the schema from `storage/sql.py`, never a copy of it.

``target_metadata`` is :data:`agentprops.storage.sql.METADATA` itself, so
``alembic revision --autogenerate`` and ``alembic check`` compare a database
against the table definitions the adapter actually queries. There is no second
declaration of the schema anywhere in this repository, which is the only way a
migration and an adapter cannot drift.

Three settings here are decisions rather than boilerplate:

``AGENTPROPS_DB_URL``
    The URL comes from ``-x url=...`` first, then the environment, then
    `alembic.ini`. A migration tool whose target lives only in a committed ini
    file is a migration tool that gets run against the wrong database - and
    the ``-x`` override is what lets the test suite migrate a temporary
    database while a developer has that variable exported.

``include_object``
    Imported from `storage/sql.py`, where it is defined next to the names it
    filters. It hides :data:`agentprops.storage.sql.POSTGRES_ONLY_INDEXES`.
    ``datasets_labels_gin`` and ``datasets_search`` have no SQLite equivalent
    (contracts section 7), so they are created by the migration inside a
    dialect branch and are deliberately absent from ``METADATA``. Without this
    filter, autogenerate against Postgres would see two indexes it does not
    know about and propose dropping them.

``render_as_batch``
    SQLite cannot ``ALTER`` most things. Batch mode makes Alembic rebuild the
    table instead, so a future column change is writable once rather than twice.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

# Importable whether or not the package is installed: `prepend_sys_path = src`
# in alembic.ini is relative to the working directory, and this is not.
_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agentprops.storage.sql import (  # noqa: E402
    METADATA,
    create_engine_for,
    include_object,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = METADATA

#: Set by `alembic.ini`, overridden by the environment, overridden by ``-x``.
URL_ENV_VAR = "AGENTPROPS_DB_URL"


def database_url() -> str:
    """The URL to migrate: ``-x url=`` beats the environment beats the ini file."""
    from_argument = context.get_x_argument(as_dictionary=True).get("url")
    if from_argument:
        return str(from_argument)
    from_environment = os.environ.get(URL_ENV_VAR)
    if from_environment:
        return from_environment
    configured = config.get_main_option("sqlalchemy.url")
    if not configured:
        raise RuntimeError(
            f"no database URL: set {URL_ENV_VAR}, or sqlalchemy.url in alembic.ini, "
            "or pass -x url=..."
        )
    return configured


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it, for a DBA to review."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect and migrate.

    The engine comes from :func:`agentprops.storage.create_engine_for` rather
    than from ``engine_from_config`` so that SQLite gets its
    ``PRAGMA foreign_keys=ON`` exactly the way the adapter does - on the
    ``connect`` event, before SQLAlchemy hands the connection over.

    That detail is load-bearing, not tidiness. Issuing the pragma on the
    connection here instead would open a transaction *before*
    ``context.configure``, Alembic would see a connection already in a
    transaction, conclude that the caller owns it, and never commit - which
    leaves the tables created (pysqlite autocommits DDL) and the
    ``alembic_version`` row rolled back. The symptom is an ``alembic check``
    that reports "target database is not up to date" immediately after a
    successful ``alembic upgrade head``.
    """
    connectable = create_engine_for(database_url())
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                include_object=include_object,
                render_as_batch=True,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
