"""Storage adapters behind the ``Store`` Protocol.

`base.py` holds the Protocol - `docs/contracts.md` section 6 - plus the two
programming-error guards an adapter may raise. `sql.py` implements it in
SQLAlchemy Core for both SQL dialects, SQLite now and Postgres at M7, from one
set of table definitions. `migrations/` is Alembic, with `alembic.ini` at the
repository root because that is where `alembic init` writes it and where every
alembic command looks for it.

Everything above this package reaches storage through :class:`Store`, never
through an adapter directly: that is what lets the conformance suite in
`tests/integration/` be written once and parameterised over backends, and what
will let M7 add two adapters by adding two fixture parameters.
"""

from agentprops.storage.base import (
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    PublishedVersionImmutableError,
    RecordNotFoundError,
    Store,
    StoreError,
)
from agentprops.storage.sql import (
    METADATA,
    POSTGRES_ONLY_INDEXES,
    SqlStore,
    create_engine_for,
    create_schema,
    sqlite_url,
)

__all__ = [
    "METADATA",
    "POSTGRES_ONLY_INDEXES",
    "STATUS_DRAFT",
    "STATUS_PUBLISHED",
    "PublishedVersionImmutableError",
    "RecordNotFoundError",
    "SqlStore",
    "Store",
    "StoreError",
    "create_engine_for",
    "create_schema",
    "sqlite_url",
]
