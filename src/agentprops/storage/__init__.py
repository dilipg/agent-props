"""Storage adapters behind the ``Store`` Protocol.

`base.py` holds the Protocol - `docs/contracts.md` section 6 - plus the two
programming-error guards an adapter may raise. `sql.py` implements it in
SQLAlchemy Core for **both** SQL dialects, SQLite and Postgres, from one set of
table definitions; `mongo.py` implements it over pymongo, sharing nothing with
`sql.py` except `common.py`. `migrations/` is Alembic, with `alembic.ini` at
the repository root because that is where `alembic init` writes it and where
every alembic command looks for it.

`common.py` is the part worth knowing about: the pure predicates and mappers
two adapters must answer **identically**, in one place, because the conformance
suite asserts identical results across backends and a second copy of the ``q``
case fold is the divergence rulings R-36 and R-39(a) exist to prevent.

Everything above this package reaches storage through :class:`Store`, never
through an adapter directly: that is what let the conformance suite in
`tests/integration/` be written once at M3 and gain two backends at M7 by
adding two fixture branches, with not a line of the suite changed.
"""

from agentprops.storage.base import (
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    PublishedVersionImmutableError,
    RecordNotFoundError,
    Store,
    StoreError,
)
from agentprops.storage.mongo import DEFAULT_DATABASE, MongoStore
from agentprops.storage.sql import (
    METADATA,
    POSTGRES_ONLY_INDEXES,
    SqlStore,
    create_engine_for,
    create_schema,
    postgres_url,
    sqlite_url,
)

__all__ = [
    "DEFAULT_DATABASE",
    "METADATA",
    "POSTGRES_ONLY_INDEXES",
    "STATUS_DRAFT",
    "STATUS_PUBLISHED",
    "MongoStore",
    "PublishedVersionImmutableError",
    "RecordNotFoundError",
    "SqlStore",
    "Store",
    "StoreError",
    "create_engine_for",
    "create_schema",
    "postgres_url",
    "sqlite_url",
]
