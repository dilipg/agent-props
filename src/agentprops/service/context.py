"""What every service function is handed: a store, a clock, and nothing else.

Two ports, both injected, both frozen in tests. That is the whole of the
service layer's environment, and keeping it to two is deliberate - a third
would be the moment to ask whether the thing being added belongs in the service
at all. There is no LLM client here, no HTTP client, no API key, no config
object (ground rule 4, and CLAUDE.md's "no LLM inside the service").

The ``resolver`` property is ruling R-11's injection point. It is a property
rather than a field because a :class:`~agentprops.service.resolver.StoreResolver`
is stateless and derived: there is exactly one correct resolver for a given
store, and letting a caller pass a different one would let a write path validate
against a store it does not then write to.

`server/` never constructs a store itself. :func:`context_for` and
:func:`context_from_url` are here so that the process entry point in
`server/__main__.py` can open a store while still importing only `service/` -
which is what makes the layering rule in `tests/unit/test_layering.py` literally
true for `server/` rather than true with an exemption.

Which backend a target names, and who creates its schema
--------------------------------------------------------

:func:`context_for` is M7's dispatch and the seam `server/__main__.py`'s
docstring was holding open for it. One argument, three answers, and the third
column is the part that matters:

============================== ================= ==========================
target                         backend           schema
============================== ================= ==========================
``mongodb://…``                Mongo             **declared on startup**
``postgresql://…``             Postgres          ``alembic upgrade head``
``sqlite://…`` or a bare path  SQLite            created if absent
============================== ================= ==========================

Mongo's indexes are declared here because contracts section 8 gives collections
and indexes rather than DDL, there is no Alembic for a document store, and
``create_index`` is idempotent - so a startup declaration is the only mechanism
available and it is a safe one.

A **Postgres** URL deliberately does *not* create anything, which is the
warning `server/__main__.py` recorded before this function existed: "a URL
argument that silently ran ``create_schema`` against a migrated Postgres
database would be worse than not having one". A real SQL database is changed by
a reviewed revision and by nothing else, and `docker-compose.yml`'s ``shared``
profile runs ``alembic upgrade head`` before it starts the service for exactly
that reason. A SQLite *file* is the containerless throwaway CLAUDE.md
describes, so it keeps the create-if-absent behaviour it has had since M4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from agentprops.service.clock import Clock, SystemClock
from agentprops.service.resolver import StoreResolver
from agentprops.storage import MongoStore, SqlStore, Store, create_schema, sqlite_url
from agentprops.validation import Resolver

__all__ = [
    "ServiceContext",
    "context_for",
    "context_from_url",
    "mongo_context",
    "sqlite_context",
]

#: URL prefixes that name a Mongo deployment. ``mongodb+srv`` is the SRV
#: seed-list form every hosted Mongo hands out, so leaving it off would make the
#: dispatch work locally and fail on Atlas.
_MONGO_PREFIXES: Final = ("mongodb://", "mongodb+srv://")

#: URL prefixes that name a Postgres database. ``postgresql+`` catches an
#: explicit driver - ``postgresql+psycopg://`` - which a caller may write by
#: hand. Naming the driver is **not** this function's job:
#: :func:`~agentprops.storage.create_engine_for` normalises it, because that is
#: the one place a SQL URL becomes an engine and a second normalisation here is
#: a second place to forget.
_POSTGRES_PREFIXES: Final = ("postgresql://", "postgres://", "postgresql+")

#: URL prefixes that name a SQLite database, as opposed to a bare filesystem
#: path, which is the other spelling and the more common one.
_SQLITE_PREFIXES: Final = ("sqlite://", "sqlite+")


@dataclass(frozen=True)
class ServiceContext:
    """The store and the clock, injected together."""

    store: Store
    clock: Clock = field(default_factory=SystemClock)

    @property
    def resolver(self) -> Resolver:
        """The ``Resolver`` the catalogue's three existence checks run against."""
        return StoreResolver(self.store)


def context_for(target: str, *, clock: Clock | None = None) -> ServiceContext:
    """A context over whichever backend ``target`` names. See the module docstring.

    ``target`` is a Mongo URL, a Postgres URL, a SQLite URL, or a filesystem
    path - and the fall-through is the path, because that is the one spelling a
    human types without thinking about schemes and the one
    ``--store ./agentprops.db`` has meant since M4.

    One dispatch rather than three functions the caller chooses between, because
    the caller is a process entry point reading one environment variable and it
    has no basis for choosing. The three named constructors stay public for a
    caller that *does* know.
    """
    if target.startswith(_MONGO_PREFIXES):
        return mongo_context(target, clock=clock)
    if target.startswith(_POSTGRES_PREFIXES):
        return context_from_url(target, clock=clock)
    if target.startswith(_SQLITE_PREFIXES):
        return _with_sql_schema(context_from_url(target, clock=clock))
    return sqlite_context(target, clock=clock)


def context_from_url(url: str, *, clock: Clock | None = None) -> ServiceContext:
    """A context over a SQL store at ``url``. **Creates no schema.**

    Both SQL dialects, from one adapter. The absence of schema creation is the
    behaviour, not an omission: a Postgres database is migrated, and this is the
    function :func:`context_for` routes a Postgres URL to.
    """
    store = SqlStore.from_url(url)
    return ServiceContext(store=store, clock=clock or SystemClock())


def mongo_context(url: str, *, clock: Clock | None = None) -> ServiceContext:
    """A context over a Mongo deployment, with its indexes declared.

    Declared rather than migrated, because there is nothing to migrate:
    contracts section 8 gives collections and indexes, Mongo creates a
    collection on first write, and ``create_index`` is idempotent. So the only
    mechanism available is a startup declaration, and it is a safe one - unlike
    the SQL equivalent, which would rewrite a table.

    Two of those indexes are load-bearing rather than cosmetic: the unique
    ``{run_id, seq}`` index is what makes ``upsert_step``'s ``seq`` allocation
    sound (ruling R-37). A process that skipped this call would still serve
    every read correctly and would lose that guarantee silently, which is why
    it is here and not in a deployment runbook.
    """
    store = MongoStore.from_url(url)
    store.create_schema()
    return ServiceContext(store=store, clock=clock or SystemClock())


def sqlite_context(path: str | Path, *, clock: Clock | None = None) -> ServiceContext:
    """A context over a SQLite **file**, with the schema created if absent.

    The containerless mode CLAUDE.md describes and CI uses.

    ``path`` is required, and an in-memory database is deliberately not offered
    as a default. ``sqlite_url(None)`` produces ``sqlite+pysqlite://``, whose
    engine uses SQLAlchemy's per-thread pool - and an in-memory SQLite database
    belongs to its *connection*. The SDK runs a synchronous tool through
    ``anyio.to_thread.run_sync``, so the schema created on the calling thread is
    invisible to the thread the tool runs on, and every tool answers "no such
    table: blueprints". Found by running exactly that, not by reasoning about
    it. A default that silently cannot work is worse than no default.
    """
    return _with_sql_schema(context_from_url(sqlite_url(path), clock=clock))


def _with_sql_schema(context: ServiceContext) -> ServiceContext:
    """``context`` with its SQL schema created if absent. For SQLite only.

    A helper rather than two copies of the ``isinstance`` narrowing, and a
    function rather than a flag on :func:`context_from_url`, so that the one
    caller who must **not** get this - a Postgres URL - cannot get it by
    forgetting to pass ``False``.
    """
    store = context.store
    if isinstance(store, SqlStore):  # pragma: no branch - a SQLite URL is a SqlStore
        create_schema(store.engine)
    return context
