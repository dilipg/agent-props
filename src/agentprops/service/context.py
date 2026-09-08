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

`server/` never constructs a store itself. :func:`context_from_url` is here so
that the process entry point in `server/__main__.py` can open a store while
still importing only `service/` - which is what makes the layering rule in
`tests/unit/test_layering.py` literally true for `server/` rather than true with
an exemption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentprops.service.clock import Clock, SystemClock
from agentprops.service.resolver import StoreResolver
from agentprops.storage import SqlStore, Store, create_schema, sqlite_url
from agentprops.validation import Resolver

__all__ = ["ServiceContext", "context_from_url", "sqlite_context"]


@dataclass(frozen=True)
class ServiceContext:
    """The store and the clock, injected together."""

    store: Store
    clock: Clock = field(default_factory=SystemClock)

    @property
    def resolver(self) -> Resolver:
        """The ``Resolver`` the catalogue's three existence checks run against."""
        return StoreResolver(self.store)


def context_from_url(url: str, *, clock: Clock | None = None) -> ServiceContext:
    """A context over a SQL store at ``url``.

    Only SQLite and Postgres URLs are meaningful today; M7 adds the Mongo
    adapter and this is where the branch goes.
    """
    store = SqlStore.from_url(url)
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
    context = context_from_url(sqlite_url(path), clock=clock)
    store = context.store
    if isinstance(store, SqlStore):  # pragma: no branch - the only adapter today
        create_schema(store.engine)
    return context
