"""One SQLAlchemy **Core** adapter for both SQL dialects: SQLite now, Postgres at M7.

`docs/contracts.md` section 7 is the DDL, given as Postgres, with the SQLite
differences stated in one sentence: "SQLite is the same with ``JSONB`` as
``JSON`` and ``TIMESTAMPTZ`` as ``TEXT``". Both of those are expressed here as
*type variants* on a single table definition rather than as two schemas, so
there is exactly one place where a column's shape is declared and M7 adds a
dialect rather than a second module.

Core, not the ORM, deliberately: every document is already a Pydantic model and
every row is already a flat projection of one, so an identity map and a unit of
work would buy nothing and would put a second, lazily-loaded representation of
a dataset in play next to the model.

What is dialect-specific, and where M7 plugs in
-----------------------------------------------

Four things, and only four. They are listed here because the whole point of one
codebase for two dialects is that the list stays short and known.

1. :class:`JsonDocument` - ``JSON`` everywhere, ``JSONB`` on Postgres, through
   ``with_variant``. Every JSON column in the DDL uses it, and the ``->>``
   extraction the label filter needs compiles from the *same* expression on
   both: ``JSON_EXTRACT(labels, '$."tier"')`` on SQLite,
   ``labels ->> 'tier'`` on Postgres.
2. :class:`UtcDateTime` - ``TIMESTAMP WITH TIME ZONE`` on Postgres, SQLite's
   ``DATETIME`` (which is TEXT) elsewhere. SQLite's driver drops ``tzinfo``
   silently, so this type normalises to UTC on the way in and re-attaches UTC on
   the way out; without it a timestamp written as ``10:14:22Z`` reads back naive
   and compares unequal to what was stored.
3. :data:`POSTGRES_ONLY_INDEXES` - ``datasets_labels_gin`` and
   ``datasets_search``, which contracts section 7 says have no SQLite
   equivalent. They are created by the migration inside a dialect branch and
   filtered out of Alembic's autogenerate comparison, so neither dialect sees
   drift.
4. The engine factory's SQLite ``PRAGMA foreign_keys=ON``, which makes SQLite
   enforce the DDL's foreign keys the way Postgres always does.

Nothing else in this module asks what dialect it is talking to, and the
``q`` filter is the reason that list is short by one: case folding is the place
SQL *cannot* agree across backends - SQLite's ``lower()`` is ASCII-only,
Postgres's is locale-aware, Mongo's ``$regex`` is a third answer - so the fold
and the substring match both happen in Python, once, in
:meth:`SqlStore.find_datasets`. Ruling R-36 makes substring semantics the
contract, so M7's Postgres adapter accelerates that query with ``pg_trgm``
rather than replacing it with full-text search. The conformance suite asserts
identical *results* across backends, never identical query plans.

What this module does not do
----------------------------

It does not validate (ruling R-23), does not read a clock (ruling R-09), does
not mint an id (ruling R-10) and issues no ``DELETE`` (ground rule 6). See
`base.py`, which states each of those and the test that enforces it.

One property holds across every method and is worth relying on: **a write
returns exactly what the matching read returns.** Every writer builds its
return value from the stored row rather than from its argument, so
``put_dataset(ds)`` and a later ``get_dataset`` agree on the allocated version,
``upsert_step`` agrees on the allocated ``seq``, and ``put_run`` agrees on a
``path`` that was reconstructed rather than declared.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Dialect,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    create_engine,
    desc,
    event,
    false,
    func,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection, Engine, Row
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.sql import Select
from sqlalchemy.types import JSON, TypeDecorator

from agentprops.models import (
    Author,
    Blueprint,
    BlueprintRef,
    BlueprintSummary,
    Dataset,
    DatasetQuery,
    DatasetSummary,
    ModelInfo,
    PathStep,
    Run,
    RunPin,
    RunQuery,
    RunSummary,
    Skeleton,
    StepRecord,
    StoreCounts,
    StoreHealth,
    Warning,
)
from agentprops.storage.base import (
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    PublishedVersionImmutableError,
    RecordNotFoundError,
    Store,
    StoreError,
)

__all__ = [
    "METADATA",
    "POSTGRES_ONLY_INDEXES",
    "JsonDocument",
    "SqlStore",
    "UtcDateTime",
    "blueprints",
    "create_engine_for",
    "create_schema",
    "datasets",
    "include_object",
    "run_steps",
    "runs",
    "skeletons",
    "sqlite_url",
]


# --------------------------------------------------------------------------
# types
# --------------------------------------------------------------------------


class UtcDateTime(TypeDecorator[datetime]):
    """``TIMESTAMPTZ`` on Postgres, SQLite's TEXT ``DATETIME`` elsewhere, UTC on both.

    SQLite has no timestamp type and no notion of an offset: SQLAlchemy stores
    a ``DATETIME`` as ``'YYYY-MM-DD HH:MM:SS.ffffff'`` text and its bind
    processor **silently discards ``tzinfo``**. A ``created_at`` written as
    ``2026-09-08T10:14:22Z`` therefore reads back naive, and compares unequal to
    the aware value the model carries - a difference that would show up as a
    conformance failure between SQLite and Postgres for no reason a reader could
    guess.

    So both directions are normalised here: aware values are converted to UTC
    before binding (making the discarded offset a no-op) and naive values read
    back get UTC re-attached. Naive values on the way *in* are read as UTC, on
    the principle that the store has no other timezone to offer.

    The stored text format matters for one more reason: it is the format SQLite
    sorts, and ``find_datasets`` promises deterministic ordering by
    ``(created_at, id)``. SQLAlchemy's fixed-width format sorts
    lexicographically in chronological order, and ``CURRENT_TIMESTAMP`` - what
    the DDL's ``DEFAULT now()`` becomes on SQLite - produces a prefix of it, so
    a defaulted value and a bound value sort correctly against each other.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


#: ``JSONB`` on Postgres, ``JSON`` everywhere else - contracts section 7's one
#: stated difference between the two dialects.
#:
#: The variant is what keeps the label filter portable. ``labels["tier"]`` plus
#: ``.as_string()`` compiles to ``JSON_EXTRACT(labels, '$."tier"')`` on SQLite
#: and ``labels ->> 'tier'`` on Postgres from the same Python expression, so
#: ``find_datasets`` has one implementation rather than a dialect switch. The
#: Postgres GIN index in :data:`POSTGRES_ONLY_INDEXES` then makes that
#: expression fast without changing it.
#:
#: ``none_as_null=True`` because SQLAlchemy's default is the other one: a Python
#: ``None`` bound to a ``JSON`` column is persisted as the JSON value ``null``,
#: not as SQL ``NULL``. It round-trips either way, so nothing looked wrong - but
#: the three nullable JSON columns (``runs.model``, ``runs.outcome``,
#: ``run_steps.actual``) then hold a value where the DDL says ``NULL``, and
#: ``actual IS NULL`` - the guard that makes :meth:`SqlStore.set_step_actual`
#: write-once under a concurrent writer - matches nothing at all. "Absent"
#: should be SQL ``NULL`` on both dialects.
JsonDocument = JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True), "postgresql"
)

#: Indexes contracts section 7 marks Postgres-only, by name.
#:
#: They are absent from :data:`METADATA` because ``create_all`` would then try
#: to build a GIN index on SQLite, and they are created by the initial migration
#: inside a ``dialect.name == "postgresql"`` branch. `migrations/env.py` filters
#: these names out of autogenerate comparison so that a future ``alembic check``
#: against Postgres does not propose dropping them.
POSTGRES_ONLY_INDEXES: Final[frozenset[str]] = frozenset({"datasets_labels_gin", "datasets_search"})


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Alembic's autogenerate filter: hide :data:`POSTGRES_ONLY_INDEXES`.

    Lives here rather than in `migrations/env.py` for two reasons. It is a
    statement about *this* schema - which objects are dialect-specific - and
    `env.py` is a script that Alembic executes on import, so a predicate
    defined there cannot be imported by a test without running a migration.
    `tests/integration/test_migrations.py` uses it to run the same comparison
    ``alembic check`` runs.
    """
    return not (type_ == "index" and name in POSTGRES_ONLY_INDEXES)


#: How many characters of ``narrative`` a :class:`DatasetSummary` carries.
#: Contracts section 2.2.1: "the first 200 characters of narrative".
NARRATIVE_EXCERPT_CHARS: Final = 200

#: Bounded retries for dataset version allocation. Reached only when another
#: writer wins the race for the same ``(id, version)`` that many times running.
VERSION_ALLOCATION_ATTEMPTS: Final = 8

#: Bounded retries for ``run_steps.seq`` allocation, guarded by
#: ``UNIQUE (run_id, seq)`` (ruling R-37). Same shape, same reason, same number.
SEQ_ALLOCATION_ATTEMPTS: Final = 8

#: Attempts for a writer whose only race is two concurrent *first* writes of one
#: key - ``put_blueprint``, ``put_skeleton``, ``put_run``, ``set_step_actual``.
#: Two is enough: the loser's second pass takes the "row exists" branch, which
#: is where it belonged all along. On the last attempt the ``IntegrityError`` is
#: re-raised as itself rather than translated, so a foreign-key violation still
#: names the constraint it broke.
FIRST_WRITE_ATTEMPTS: Final = 2

_BACKEND_NAMES: Final[dict[str, str]] = {"sqlite": "sqlite", "postgresql": "postgres"}


# --------------------------------------------------------------------------
# schema - contracts section 7, verbatim except where a note says otherwise
# --------------------------------------------------------------------------

METADATA = MetaData()

blueprints = Table(
    "blueprints",
    METADATA,
    Column("agent_id", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("document", JsonDocument, nullable=False),
    Column("created_at", UtcDateTime, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("agent_id", "version", name="blueprints_pkey"),
    CheckConstraint(
        f"status IN ('{STATUS_DRAFT}', '{STATUS_PUBLISHED}')",
        name="blueprints_status_check",
    ),
)
"""``created_at`` is the one column defaulted by the database.

:class:`~agentprops.models.blueprint.Blueprint` carries no ``created_at`` field
- ruling R-09 assigns that value to the column default, and nothing reads it
back into a model, since ``BlueprintSummary`` is
``{agent_id, version, status, description}``.
"""

datasets = Table(
    "datasets",
    METADATA,
    Column("id", Uuid, nullable=False),
    Column("version", Integer, nullable=False),
    Column("agent_id", Text, nullable=False),
    Column("bp_version", Text, nullable=False),
    Column("archived", Boolean, nullable=False, server_default=false()),
    Column("labels", JsonDocument, nullable=False),
    Column("seed", BigInteger, nullable=False),
    # provenance promoted out of the document so find can filter and sort
    # without parsing JSON
    Column("title", Text, nullable=False),
    Column("intent", Text, nullable=False),
    Column("author_name", Text, nullable=False),
    Column("author_handle", Text, nullable=False),
    Column("author_agent", Text, nullable=False),
    Column("supersedes", Uuid, nullable=True),
    Column("document", JsonDocument, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    PrimaryKeyConstraint("id", "version", name="datasets_pkey"),
    ForeignKeyConstraint(
        ["agent_id", "bp_version"],
        ["blueprints.agent_id", "blueprints.version"],
        name="datasets_blueprint_fkey",
    ),
)
"""``created_at`` has **no** ``DEFAULT now()``, and that is ruling R-09, not an omission.

The column is populated from ``provenance.created_at`` on insert. One value,
authored in the document, so ``find_datasets``'s deterministic ordering by
``(created_at, id)`` is reproducible across an export/import cycle - a
``DEFAULT now()`` column would be re-stamped on re-import and the ordering
would be irreproducible.

``PRIMARY KEY (id, version)`` is also the concurrency guard for version
allocation: see :meth:`SqlStore.put_dataset`.
"""

Index("datasets_lookup", datasets.c.agent_id, datasets.c.bp_version, datasets.c.archived)
Index("datasets_author", datasets.c.author_handle)

skeletons = Table(
    "skeletons",
    METADATA,
    Column("id", Uuid, primary_key=True),
    Column("agent_id", Text, nullable=False),
    Column("bp_version", Text, nullable=False),
    # `labels` and `seed` are `dataset_skeleton`'s two non-section inputs
    # (ruling R-06), added to the DDL at M5 for the reason `Skeleton.labels`
    # records: `dataset_submit(skeleton_id)` takes nothing else, so they have
    # to survive here or the dataset cannot be assembled. Same column types as
    # `datasets`, so a skeleton and the dataset it becomes agree.
    Column("labels", JsonDocument, nullable=False),
    Column("seed", BigInteger, nullable=False),
    Column("manifest", JsonDocument, nullable=False),
    Column("parts", JsonDocument, nullable=False, server_default=text("'{}'")),
    Column("submitted_as", Uuid, nullable=True),
    Column("created_at", UtcDateTime, nullable=False, server_default=func.now()),
)

runs = Table(
    "runs",
    METADATA,
    Column("id", Text, primary_key=True),
    Column("agent_id", Text, nullable=False),
    Column("dataset_id", Uuid, nullable=False),
    Column("dataset_ver", Integer, nullable=False),
    Column("bp_version", Text, nullable=False),
    Column("declared_bp_version", Text, nullable=True),
    Column("run_class", Text, nullable=False, server_default=text("'dev'")),
    Column("model", JsonDocument, nullable=True),
    Column("status", Text, nullable=False),
    Column("outcome", JsonDocument, nullable=True),
    Column("warnings", JsonDocument, nullable=False, server_default=text("'[]'")),
    Column("started_at", UtcDateTime, nullable=False, server_default=func.now()),
    Column("finished_at", UtcDateTime, nullable=True),
    Column("external_refs", JsonDocument, nullable=False, server_default=text("'{}'")),
    ForeignKeyConstraint(
        ["dataset_id", "dataset_ver"],
        ["datasets.id", "datasets.version"],
        name="runs_dataset_fkey",
    ),
)
"""``declared_bp_version`` is the one column not in contracts section 7's DDL.

Contracts section 2.3 gives ``Run`` a ``declared_blueprint_version`` field -
what the agent *said* it was running, as opposed to ``pin.blueprint_version``,
which is what it is actually being served - and the ``runs`` DDL has no column
for it. Reported as a discrepancy rather than resolved silently; the column
exists because the alternative is dropping a model field on write, and that
field is the input to the ``blueprint_version_mismatch`` warning that ground
rule 3 requires be attached "to the response *and* to the stored run".

It is nullable and absent from ``RunSummary``, which ruling R-05 derives from
"the ``runs`` DDL columns minus ``outcome``" - so no read path shape changes.
"""

Index("runs_lookup", runs.c.agent_id, runs.c.run_class, desc(runs.c.started_at))

run_steps = Table(
    "run_steps",
    METADATA,
    Column("run_id", Text, ForeignKey("runs.id"), nullable=False),
    Column("node_id", Text, nullable=False),
    Column("iteration", Integer, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("served", JsonDocument, nullable=False),
    Column("actual", JsonDocument, nullable=True),
    Column("fetched_at", UtcDateTime, nullable=False, server_default=func.now()),
    Column("recorded_at", UtcDateTime, nullable=True),
    PrimaryKeyConstraint("run_id", "node_id", "iteration", name="run_steps_pkey"),
    UniqueConstraint("run_id", "seq", name="run_steps_seq_key"),
)
"""``seq`` is ``NOT NULL`` here while ``StepRecord.seq`` is optional.

M1 flagged that seam deliberately: the model must parse a run document that
elides ``seq``, and the column must never hold a null, because ``seq`` is what
reconstructs the path. :meth:`SqlStore.upsert_step` closes it by *allocating*
the value rather than passing the model's ``None``.

``UNIQUE (run_id, seq)`` is an **addition to contracts section 7**, authorised by
ruling R-37. It is what makes the allocation sound rather than hopeful: M3's
first implementation read ``max(seq)`` and inserted inside one
``engine.begin()``, and the review *reproduced* two connections both committing
``seq = 1`` against one file-backed SQLite database. pysqlite defers ``BEGIN``
until the first DML, so neither the existence check nor the ``max(seq)`` read
takes a lock; Postgres under READ COMMITTED behaves the same. A transaction
around a read and a write is not a lock over the value read. The constraint
rejects the duplicate, and :meth:`SqlStore.upsert_step` retries - the same
shape ``put_dataset`` uses for dataset versions.

Why it matters at M6: ``get_run`` orders steps by ``seq``, and ``Run.path`` is
what step resolution disambiguates against (contracts section 5 takes
``run.path[-1]`` as the head). A duplicate ``seq`` would surface as
intermittently wrong tool-name resolution with no visible cause.
"""


# --------------------------------------------------------------------------
# engine and schema helpers
# --------------------------------------------------------------------------


def create_engine_for(url: str) -> Engine:
    """An engine for ``url``, with SQLite taught to enforce foreign keys.

    SQLite parses ``REFERENCES`` clauses but ignores them unless
    ``PRAGMA foreign_keys`` is on, per connection. Turning it on is what makes
    the two SQL dialects behave the same way, which is the parity that matters:
    contracts section 7 declares the foreign keys, and a store that declared
    them without enforcing them would be claiming something untrue.

    Mongo has no foreign keys at all, so contracts section 8 exempts
    referential behaviour from the conformance suite - the suite writes a
    blueprint before a dataset because that is the honest order, not because
    SQLite forces it.
    """
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def sqlite_url(path: str | Path | None = None) -> str:
    """``--store sqlite <path>``, as a URL. ``None`` means in-memory."""
    if path is None:
        return "sqlite+pysqlite://"
    return f"sqlite+pysqlite:///{Path(path)}"


def create_schema(engine: Engine) -> None:
    """Create every table in :data:`METADATA`, for tests and throwaway databases.

    A real database is migrated, not created: `migrations/` owns the schema and
    ``alembic upgrade head`` is the only thing that should touch a database
    anyone cares about. This exists so a test can have a fresh store in a
    millisecond, and `tests/integration/test_migrations.py` asserts that what
    it produces and what the migration produces are the same schema - which is
    the only thing that makes using it here safe.
    """
    METADATA.create_all(engine)


# --------------------------------------------------------------------------
# small pure helpers
# --------------------------------------------------------------------------


def _canonical(value: Any) -> str:
    """A stable text form of a JSON value, for identity comparison (ruling R-29).

    Deliberately a duplicate of ``validation.jsonschemas.canonical`` rather than
    an import of it: `storage/` may import `models/` and nothing else sideways,
    and BP-016's idempotency check needs exactly the ``json.dumps(sort_keys=True)``
    comparison R-29 names. Three lines duplicated across a layer boundary is the
    cheaper of the two prices.
    """
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _semver_key(version: str) -> tuple[int, tuple[int, ...], str]:
    """Sort key for a version string, newest last.

    BP-002 requires semver, and `service/` has already enforced it by the time a
    row exists - but a store must not raise on data it can be handed, so an
    unparseable version sorts *below* every parseable one and ties break
    lexicographically. Done in Python rather than SQL because ``ORDER BY`` on a
    ``TEXT`` column puts ``1.10.0`` before ``1.9.0``, and no portable SQL
    expression fixes that.
    """
    head = version.split("-", 1)[0].split("+", 1)[0]
    parts = head.split(".")
    if all(part.isdigit() for part in parts) and parts != [""]:
        return (1, tuple(int(part) for part in parts), version)
    return (0, (), version)


def _parse_uuid(value: str) -> uuid.UUID | None:
    """A UUID, or ``None`` for anything that is not one.

    The read path's parser. A malformed id is "no such row", never an
    exception: ``get_dataset``, ``get_skeleton`` and ``find_runs`` all take ids
    that arrived as tool inputs, and ``RunQuery.dataset_id``'s own docstring
    settles the question - "an unparseable filter should return no rows, not
    raise".
    """
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _require_uuid(value: str, field: str) -> uuid.UUID:
    """A UUID, or :class:`StoreError`. The write path's parser.

    The asymmetry with :func:`_parse_uuid` is intentional. On a read a
    malformed id is a miss; on a write it is a value that cannot be stored in a
    ``UUID`` column, and the choices are to raise or to drop it. Nothing
    user-caused reaches here: ``provenance.supersedes`` is proved to name a real
    dataset by DS-031, and dataset ids come from ``Seeded.uuid()``.
    """
    parsed = _parse_uuid(value)
    if parsed is None:
        raise StoreError(f"{field} is not a UUID: {value!r}")
    return parsed


def _document(model: Blueprint | Dataset, **overrides: Any) -> dict[str, Any]:
    """The JSON document to store for ``model``, with ``overrides`` applied.

    ``exclude_unset=True`` is load-bearing, not a micro-optimisation. Ruling
    R-08 defines the round trip as
    ``model_validate(raw).model_dump(mode="json", exclude_unset=True) == raw``,
    and the golden fixtures omit many optional fields - ``tool_name`` on three
    nodes, ``max_iterations`` on eight, ``input`` on every pool entry. A full
    dump would reintroduce those as ``null`` and the bytes a dataset was
    submitted with would not be the bytes ``dataset_export`` emits, which is
    exactly the byte-stability M7's cross-backend import gate depends on.

    The overrides are the fields the *store* owns rather than the author: a
    dataset's allocated ``version`` and its lineage-level ``archived`` state
    (ruling R-34), and a blueprint's ``status``. Applying them to the document
    as well as to the column is what makes it impossible for a row and the
    document inside it to disagree - which is the same reason ``set_archived``
    rewrites the document rather than only the column.
    """
    document = model.model_dump(mode="json", exclude_unset=True)
    document.update(overrides)
    return document


def _labels(value: Any) -> dict[str, str]:
    """A ``labels`` column, typed. JSON gives back ``Any``; ``Labels`` is
    ``dict[str, str]`` and the rows were validated before they were written."""
    return {str(key): str(item) for key, item in dict(value).items()}


def _folded(value: object) -> str:
    """The case-folded form used by the ``q`` filter, on every backend.

    One implementation, in Python, because there is no SQL expression that
    folds case identically on all three backends: SQLite's ``lower()`` is
    ASCII-only, Postgres's is locale-aware, and Mongo's ``$regex`` with ``i``
    is a third answer. Folding the *term* in Python and the *column* in SQL -
    which is what M3 shipped first - is the worst of the three, because the two
    halves of one comparison then disagree with each other.

    Verified, not assumed: SQLite returns ``lower('BENGALŪRU')`` as
    ``'bengalŪru'``, while Python returns ``'bengalūru'``. Every current
    fixture is ASCII, so nothing was broken - it was a trap for whoever wrote
    the first non-ASCII dataset.

    ``str.casefold`` rather than ``str.lower``: it is the Unicode operation
    designed for caseless comparison (it folds ``ß`` to ``ss``), and picking
    the weaker one here would be choosing to be subtly wrong on purpose.
    """
    return str(value).casefold()


# --------------------------------------------------------------------------
# the adapter
# --------------------------------------------------------------------------


class SqlStore:
    """The ``Store`` for both SQL dialects.

    Constructed over an :class:`~sqlalchemy.engine.Engine`, so the caller owns
    the connection string, the pool and the lifecycle. ``backend`` is derived
    from the dialect, because :class:`StoreHealth` reports ``sqlite`` or
    ``postgres`` and a caller should not have to keep those two facts in sync.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._backend = _BACKEND_NAMES.get(engine.dialect.name, engine.dialect.name)

    @classmethod
    def from_url(cls, url: str) -> SqlStore:
        """A store over a fresh engine for ``url``. Schema not created."""
        return cls(create_engine_for(url))

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def backend(self) -> str:
        return self._backend

    def dispose(self) -> None:
        """Release the pool. Not on the Protocol - a lifecycle concern, and
        Mongo's equivalent is a different call."""
        self._engine.dispose()

    # ---------------------------------------------------------------- blueprints

    def put_blueprint(self, bp: Blueprint, publish: bool) -> Blueprint:
        """See :meth:`agentprops.storage.base.Store.put_blueprint`.

        ``publish`` is the single source of truth for the stored status: the
        document's own ``status`` is normalised to match, so the flag and the
        document cannot drift apart. A consequence worth knowing: writing an
        already-published version with ``publish=False`` is a *demotion*, which
        is a mutation of a published version, so it is refused by BP-016 like
        any other difference.

        The insert branch retries once on an ``IntegrityError``, which is a
        concurrent *first* write of the same ``{agent_id, version}``: the second
        pass takes the "row exists" branch, where two simultaneous identical
        publishes converge on R-29's no-op success. That case is not exotic -
        it is a CI pipeline that publishes on every run, which is the situation
        R-29 exists for - and without the retry it surfaces as a raw driver
        error instead.
        """
        status = STATUS_PUBLISHED if publish else STATUS_DRAFT
        document = _document(bp, status=status)
        for attempt in range(FIRST_WRITE_ATTEMPTS):
            try:
                with self._engine.begin() as conn:
                    row = self._blueprint_row(conn, bp.agent_id, bp.version)
                    if row is None:
                        conn.execute(
                            insert(blueprints).values(
                                agent_id=bp.agent_id,
                                version=bp.version,
                                status=status,
                                document=document,
                            )
                        )
                        return Blueprint.model_validate(document)

                    stored: dict[str, Any] = row.document
                    if row.status == STATUS_PUBLISHED:
                        if _canonical(stored) == _canonical(document):
                            # Ruling R-29: a byte-identical re-publish is a
                            # no-op success. Nothing changes, because the
                            # documents are identical, so immutability is fully
                            # preserved - and `dataset_import` and a CI pipeline
                            # that publishes on every run both become idempotent.
                            return Blueprint.model_validate(stored)
                        raise PublishedVersionImmutableError(
                            f"blueprint {bp.agent_id}@{bp.version} is published and differs "
                            "(BP-016)"
                        )

                    conn.execute(
                        update(blueprints)
                        .where(
                            blueprints.c.agent_id == bp.agent_id,
                            blueprints.c.version == bp.version,
                        )
                        .values(status=status, document=document)
                    )
                    return Blueprint.model_validate(document)
            except IntegrityError:
                if attempt == FIRST_WRITE_ATTEMPTS - 1:
                    raise
        raise StoreError(  # pragma: no cover - the loop returns or re-raises
            f"could not write blueprint {bp.agent_id}@{bp.version}"
        )

    def get_blueprint(self, agent_id: str, version: str | None) -> Blueprint | None:
        """See :meth:`agentprops.storage.base.Store.get_blueprint`."""
        with self._engine.connect() as conn:
            if version is not None:
                row = conn.execute(
                    select(blueprints.c.document).where(
                        blueprints.c.agent_id == agent_id,
                        blueprints.c.version == version,
                    )
                ).one_or_none()
                if row is None:
                    return None
                exact: dict[str, Any] = row.document
                return Blueprint.model_validate(exact)

            published = conn.execute(
                select(blueprints.c.version, blueprints.c.document).where(
                    blueprints.c.agent_id == agent_id,
                    blueprints.c.status == STATUS_PUBLISHED,
                )
            ).all()
        if not published:
            return None
        newest = max(published, key=lambda candidate: _semver_key(str(candidate.version)))
        latest: dict[str, Any] = newest.document
        return Blueprint.model_validate(latest)

    def list_blueprints(self, status: str | None) -> list[BlueprintSummary]:
        """See :meth:`agentprops.storage.base.Store.list_blueprints`.

        Ordered by ``(agent_id, semver)`` so the list is stable across calls
        and across backends. The order is not specified anywhere; it needs to be
        *some* deterministic order, and this is the one a human reading the list
        wants.
        """
        statement = select(
            blueprints.c.agent_id,
            blueprints.c.version,
            blueprints.c.status,
            blueprints.c.document,
        )
        if status is not None:
            statement = statement.where(blueprints.c.status == status)
        with self._engine.connect() as conn:
            rows = conn.execute(statement).all()
        ordered = sorted(rows, key=lambda row: (str(row.agent_id), _semver_key(str(row.version))))
        return [
            BlueprintSummary(
                agent_id=str(row.agent_id),
                version=str(row.version),
                status=str(row.status),
                description=str(row.document.get("description", "")),
            )
            for row in ordered
        ]

    # ------------------------------------------------------------------ datasets

    def put_dataset(self, ds: Dataset) -> Dataset:
        """See :meth:`agentprops.storage.base.Store.put_dataset`.

        Version allocation is ``max(version) + 1`` read outside the insert, with
        ``PRIMARY KEY (id, version)`` as the guard rather than a lock: two
        writers that read the same maximum both attempt the same version, one of
        them loses on the constraint, and the loser re-reads and takes the next
        one. Monotonic under concurrency, no ``SELECT ... FOR UPDATE`` (which
        SQLite does not have), no advisory lock (which Mongo does not have), and
        no long-held transaction.

        An ``IntegrityError`` that is *not* that race - a missing blueprint
        tripping the foreign key, say - is re-raised immediately rather than
        retried, so the real cause is not buried under eight attempts.

        ``archived`` is **inherited from the lineage**, not taken from
        ``ds.archived`` (ruling R-34). Archive is a property of the dataset, not
        of a version: ``set_archived`` takes an id and no version, so an edit
        written after an archive must not un-hide the lineage - and since
        ``find_datasets`` returns one row per lineage, a half-archived lineage
        would have no coherent answer. The caller's flag is honoured only for
        the first version, where there is no lineage to inherit from.
        """
        base = _document(ds)
        inherited = self._lineage_archived(ds.id)
        archived = ds.archived if inherited is None else inherited
        for _ in range(VERSION_ALLOCATION_ATTEMPTS):
            version = self._next_dataset_version(ds.id)
            document = {**base, "version": version, "archived": archived}
            try:
                row = self._dataset_row(ds, document, version, archived)
                with self._engine.begin() as conn:
                    conn.execute(insert(datasets).values(**row))
            except IntegrityError:
                if self._dataset_version_exists(ds.id, version):
                    continue
                raise
            return Dataset.model_validate(document)
        raise StoreError(f"could not allocate a version for dataset {ds.id} after repeated races")

    def get_dataset(self, dataset_id: str, version: int | None) -> Dataset | None:
        """See :meth:`agentprops.storage.base.Store.get_dataset`."""
        identifier = _parse_uuid(dataset_id)
        if identifier is None:
            return None
        statement = select(datasets.c.document).where(datasets.c.id == identifier)
        statement = (
            statement.where(datasets.c.version == version)
            if version is not None
            else statement.order_by(desc(datasets.c.version)).limit(1)
        )
        with self._engine.connect() as conn:
            row = conn.execute(statement).one_or_none()
        if row is None:
            return None
        document: dict[str, Any] = row.document
        return Dataset.model_validate(document)

    def find_datasets(self, q: DatasetQuery) -> list[DatasetSummary]:
        """See :meth:`agentprops.storage.base.Store.find_datasets`.

        **One row per dataset lineage**, at its latest version - ratified by
        ruling R-38, which calls it the only coherent option, and the reasoning
        is here rather than only in `DECISIONS.md` because M4 and M9 both
        consume this method. ``dataset_find``'s specified ordering is
        ``(created_at, id)``, and under ruling R-09 ``created_at`` comes from
        ``provenance.created_at`` - authored content, identical across every
        version of one dataset. Per-version rows would therefore share *both*
        sort keys, so the specified ordering could not be total, and M4's gate
        that "label queries return deterministic ordering for identical inputs"
        could not hold. It is also what the method is *for*: PRD 5.7 and M9
        make this list a review surface, and a list showing one dataset five
        times, once per edit, is a worse review surface rather than a more
        complete one. ``get_dataset(dataset_id, version)`` reaches a specific
        version.

        Archived rows are excluded **in the WHERE clause**, not filtered out
        afterwards, so a page is never short; :meth:`get_dataset` returns them,
        which is the asymmetry a pinned run depends on.

        The ``q`` filter is applied in Python, after the SQL filters and the
        ordering - see :func:`_folded` for why that is the only implementation
        that returns identical rows on all three backends. Pagination follows
        it, for the same reason the archive exclusion precedes it: a filter that
        runs after ``LIMIT`` returns short pages.
        """
        newer = datasets.alias("newer")
        latest_version = (
            select(func.max(newer.c.version)).where(newer.c.id == datasets.c.id).scalar_subquery()
        )
        statement = select(
            datasets.c.id,
            datasets.c.version,
            datasets.c.agent_id,
            datasets.c.bp_version,
            datasets.c.archived,
            datasets.c.labels,
            datasets.c.title,
            datasets.c.intent,
            datasets.c.author_name,
            datasets.c.author_handle,
            datasets.c.author_agent,
            datasets.c.document,
            datasets.c.created_at,
        ).where(datasets.c.version == latest_version, datasets.c.archived == false())
        statement = self._apply_dataset_filters(statement, q)
        statement = statement.order_by(datasets.c.created_at, datasets.c.id)
        if q.q is None:
            statement = _paginate(statement, q.limit, q.offset)
        with self._engine.connect() as conn:
            rows = conn.execute(statement).all()
        if q.q is None:
            return [_dataset_summary(row) for row in rows]
        term = _folded(q.q)
        matched = [row for row in rows if term in _folded(row.title) or term in _folded(row.intent)]
        return [_dataset_summary(row) for row in _slice(matched, q.limit, q.offset)]

    def set_archived(self, dataset_id: str, archived: bool) -> DatasetSummary:
        """See :meth:`agentprops.storage.base.Store.set_archived`.

        Every version of the lineage is flipped, and the flag is written to the
        stored *document* as well as to the column, in one transaction. Two
        consequences, both wanted: an exported dataset carries its true archive
        state rather than a stale ``false``, and nothing anywhere has to know
        which of the two copies wins.

        The document is rewritten in Python rather than with ``json_set`` /
        ``jsonb_set`` because those are two different functions in the two SQL
        dialects and neither exists in Mongo. Archiving is a rare
        administrative action on a handful of rows; a portable read-modify-write
        inside one transaction is the right trade.

        This is the only in-place update on a dataset row, and it does not
        violate "datasets are immutable and versioned": the flag is store
        metadata, not fixture content, and archiving must not bump the version -
        a run pinned to version 1 has to keep reading version 1.
        """
        identifier = _parse_uuid(dataset_id)
        if identifier is None:
            raise RecordNotFoundError(f"no dataset {dataset_id!r}")
        with self._engine.begin() as conn:
            rows = conn.execute(
                select(datasets.c.version, datasets.c.document)
                .where(datasets.c.id == identifier)
                .order_by(datasets.c.version)
            ).all()
            if not rows:
                raise RecordNotFoundError(f"no dataset {dataset_id!r}")
            for row in rows:
                document: dict[str, Any] = {**row.document, "archived": archived}
                conn.execute(
                    update(datasets)
                    .where(datasets.c.id == identifier, datasets.c.version == row.version)
                    .values(archived=archived, document=document)
                )
            summary_row = conn.execute(
                select(
                    datasets.c.id,
                    datasets.c.version,
                    datasets.c.agent_id,
                    datasets.c.bp_version,
                    datasets.c.archived,
                    datasets.c.labels,
                    datasets.c.title,
                    datasets.c.intent,
                    datasets.c.author_name,
                    datasets.c.author_handle,
                    datasets.c.author_agent,
                    datasets.c.document,
                    datasets.c.created_at,
                )
                .where(datasets.c.id == identifier)
                .order_by(desc(datasets.c.version))
                .limit(1)
            ).one()
        return _dataset_summary(summary_row)

    # ----------------------------------------------------------------- skeletons

    def put_skeleton(self, sk: Skeleton) -> Skeleton:
        """See :meth:`agentprops.storage.base.Store.put_skeleton`.

        Select-then-insert-or-update rather than ``ON CONFLICT``: SQLite and
        Postgres spell that differently and Mongo does not have it at all, and
        an upsert of one row by primary key inside a transaction is not the
        thing worth optimising here. As in :meth:`put_blueprint`, the insert
        branch retries once so that two concurrent first writes converge on the
        update branch instead of surfacing a raw driver error.

        Note for M7: ``parts`` is keyed by section id, and two of ruling R-06's
        five section ids contain a dot (``nodes.core``, ``nodes.branches``).
        That is a legal JSON object key and a legal SQL JSON path segment, but
        Mongo cannot dot-path-address such a field, so the Mongo adapter has to
        store ``parts`` as an opaque document rather than reaching into it. The
        conformance suite fills a dotted key so that constraint is discovered
        here rather than at M5 or M8.
        """
        values = {
            "agent_id": sk.agent_id,
            "bp_version": sk.bp_version,
            "labels": dict(sk.labels),
            "seed": sk.seed,
            "manifest": [section.model_dump(mode="json") for section in sk.manifest],
            "parts": sk.parts,
            "submitted_as": sk.submitted_as,
            "created_at": sk.created_at,
        }
        for attempt in range(FIRST_WRITE_ATTEMPTS):
            try:
                with self._engine.begin() as conn:
                    exists = self._existing_skeleton(conn, sk.id)
                    if exists is None:
                        conn.execute(insert(skeletons).values(id=sk.id, **values))
                    else:
                        conn.execute(
                            update(skeletons).where(skeletons.c.id == sk.id).values(**values)
                        )
                    row = conn.execute(select(skeletons).where(skeletons.c.id == sk.id)).one()
                    return _skeleton(row)
            except IntegrityError:
                if attempt == FIRST_WRITE_ATTEMPTS - 1:
                    raise
        raise StoreError(  # pragma: no cover - the loop returns or re-raises
            f"could not write skeleton {sk.id}"
        )

    def get_skeleton(self, skeleton_id: str) -> Skeleton | None:
        """See :meth:`agentprops.storage.base.Store.get_skeleton`."""
        identifier = _parse_uuid(skeleton_id)
        if identifier is None:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(select(skeletons).where(skeletons.c.id == identifier)).one_or_none()
        return None if row is None else _skeleton(row)

    def mark_skeleton_submitted(self, skeleton_id: str, dataset_id: str) -> None:
        """See :meth:`agentprops.storage.base.Store.mark_skeleton_submitted`.

        SK-005 ("a skeleton is submitted once") is M5's rule and belongs in
        `service/` per ruling R-23, so this does not enforce it. What it does
        refuse is a *different* dataset id over an existing one, on the same
        reasoning as BP-016's guard: re-marking the same dataset is a harmless
        replay, and re-marking a different one destroys lineage that nothing
        else records.
        """
        identifier = _parse_uuid(skeleton_id)
        if identifier is None:
            raise RecordNotFoundError(f"no skeleton {skeleton_id!r}")
        dataset = _require_uuid(dataset_id, "dataset_id")
        with self._engine.begin() as conn:
            row = conn.execute(
                select(skeletons.c.submitted_as).where(skeletons.c.id == identifier)
            ).one_or_none()
            if row is None:
                raise RecordNotFoundError(f"no skeleton {skeleton_id!r}")
            current: uuid.UUID | None = row.submitted_as
            if current is not None and current != dataset:
                raise StoreError(
                    f"skeleton {skeleton_id} was already submitted as {current} (SK-005)"
                )
            conn.execute(
                update(skeletons).where(skeletons.c.id == identifier).values(submitted_as=dataset)
            )

    # ---------------------------------------------------------------------- runs

    def put_run(self, run: Run) -> Run:
        """See :meth:`agentprops.storage.base.Store.put_run`.

        The insert branch retries once, as :meth:`put_blueprint` does, so two
        concurrent first writes of one run id converge on the update branch.
        Note what the retry does *not* swallow: a missing pinned dataset trips
        ``runs_dataset_fkey``, the second pass fails the same way, and the
        ``IntegrityError`` is re-raised naming the constraint it broke.
        """
        values = {
            "agent_id": run.agent_id,
            "dataset_id": run.pin.dataset_id,
            "dataset_ver": run.pin.dataset_version,
            "bp_version": run.pin.blueprint_version,
            "declared_bp_version": run.declared_blueprint_version,
            "run_class": run.run_class,
            "model": None if run.model is None else run.model.model_dump(mode="json"),
            "status": run.status,
            "outcome": run.outcome,
            "warnings": [warning.model_dump(mode="json") for warning in run.warnings],
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "external_refs": run.external_refs,
        }
        for attempt in range(FIRST_WRITE_ATTEMPTS):
            try:
                with self._engine.begin() as conn:
                    exists = self._existing_run(conn, run.id)
                    if exists is None:
                        conn.execute(insert(runs).values(id=run.id, **values))
                    else:
                        conn.execute(update(runs).where(runs.c.id == run.id).values(**values))
                break
            except IntegrityError:
                if attempt == FIRST_WRITE_ATTEMPTS - 1:
                    raise
        for step in run.steps:
            self.upsert_step(run.id, step)
        stored = self.get_run(run.id)
        if stored is None:  # pragma: no cover - the row was just written
            raise StoreError(f"run {run.id} vanished during write")
        return stored

    def get_run(self, run_id: str) -> Run | None:
        """See :meth:`agentprops.storage.base.Store.get_run`.

        ``path`` is rebuilt from ``run_steps``. It is not stored, and no column
        in contracts section 7 holds it: the ordered sequence of calls carrying
        a run id *is* the traversal, so branch selection is observed rather than
        declared and an agent cannot report a path it did not take.

        The order is ``(seq, node_id, iteration)`` - **total**, per ruling R-37,
        which makes the step order a fourth alongside R-35's three. ``seq``
        alone is unique under ``UNIQUE (run_id, seq)``, so the two extra keys
        never decide anything today; they are there because ``Run.path`` is what
        step resolution disambiguates against (contracts section 5 takes
        ``run.path[-1]`` as the head), and a partial order that happens to be
        stable on one backend is how M7 discovers a divergence at the worst
        possible layer.
        """
        with self._engine.connect() as conn:
            row = conn.execute(select(runs).where(runs.c.id == run_id)).one_or_none()
            if row is None:
                return None
            step_rows = conn.execute(
                select(run_steps)
                .where(run_steps.c.run_id == run_id)
                .order_by(run_steps.c.seq, run_steps.c.node_id, run_steps.c.iteration)
            ).all()
        steps = [_step(step_row) for step_row in step_rows]
        return Run(
            id=str(row.id),
            agent_id=str(row.agent_id),
            pin=RunPin(
                dataset_id=row.dataset_id,
                dataset_version=int(row.dataset_ver),
                blueprint_version=str(row.bp_version),
            ),
            declared_blueprint_version=row.declared_bp_version,
            model=None if row.model is None else ModelInfo.model_validate(row.model),
            run_class=str(row.run_class),
            path=[
                PathStep(node_id=step.node_id, iteration=step.iteration, at=at)
                for step in steps
                if (at := step.fetched_at) is not None
            ],
            steps=steps,
            outcome=row.outcome,
            warnings=[Warning.model_validate(warning) for warning in row.warnings],
            status=str(row.status),
            started_at=row.started_at,
            finished_at=row.finished_at,
            external_refs=dict(row.external_refs),
        )

    def find_runs(self, q: RunQuery) -> list[RunSummary]:
        """See :meth:`agentprops.storage.base.Store.find_runs`.

        Newest first, tie-broken by id - the order ``runs_lookup``
        ``(agent_id, run_class, started_at DESC)`` is built for, and the order a
        caller listing runs wants. An unparseable ``dataset_id`` filter matches
        nothing rather than raising.
        """
        statement = select(
            runs.c.id,
            runs.c.agent_id,
            runs.c.dataset_id,
            runs.c.dataset_ver,
            runs.c.bp_version,
            runs.c.run_class,
            runs.c.model,
            runs.c.status,
            runs.c.warnings,
            runs.c.started_at,
            runs.c.finished_at,
            runs.c.external_refs,
        )
        if q.agent_id is not None:
            statement = statement.where(runs.c.agent_id == q.agent_id)
        if q.dataset_id is not None:
            identifier = _parse_uuid(q.dataset_id)
            if identifier is None:
                return []
            statement = statement.where(runs.c.dataset_id == identifier)
        if q.run_class is not None:
            statement = statement.where(runs.c.run_class == q.run_class)
        if q.model is not None:
            statement = statement.where(runs.c.model["name"].as_string() == q.model)
        statement = statement.order_by(desc(runs.c.started_at), runs.c.id)
        statement = _paginate(statement, q.limit, q.offset)
        with self._engine.connect() as conn:
            rows = conn.execute(statement).all()
        return [
            RunSummary(
                id=str(row.id),
                agent_id=str(row.agent_id),
                dataset_id=row.dataset_id,
                dataset_ver=int(row.dataset_ver),
                bp_version=str(row.bp_version),
                run_class=str(row.run_class),
                model=None if row.model is None else ModelInfo.model_validate(row.model),
                status=str(row.status),
                warnings=[Warning.model_validate(warning) for warning in row.warnings],
                started_at=row.started_at,
                finished_at=row.finished_at,
                external_refs=dict(row.external_refs),
            )
            for row in rows
        ]

    def upsert_step(self, run_id: str, step: StepRecord) -> StepRecord:
        """See :meth:`agentprops.storage.base.Store.upsert_step`.

        Idempotent on ``(run_id, node_id, iteration)``, which is the table's
        primary key, so the guarantee is the schema's rather than this method's.
        A second call returns the stored row untouched - the same ``served``
        fixture and the same ``seq`` - which is what lets a client retry
        ``fetch_step`` an hour later and get the same answer.

        ``seq`` is allocated as ``max(seq) + 1`` within the run and any incoming
        ``StepRecord.seq`` is ignored, because ``seq`` is the store's record of
        the order in which steps were actually served. Honouring a caller-supplied
        value would give traversal order two sources of truth and let a client
        forge one. The visible consequence, recorded rather than hidden: a run
        re-imported step by step is renumbered from 1, preserving order but not
        the original numbers.

        **``UNIQUE (run_id, seq)`` is what makes the allocation sound, not the
        transaction** (ruling R-37). M3's first implementation read ``max(seq)``
        and inserted inside one ``engine.begin()`` and described that as safe; it
        is not, and the review reproduced two connections both committing
        ``seq = 1``. pysqlite defers ``BEGIN`` until the first DML, so neither
        the existence check nor the ``max`` read takes a lock, and Postgres under
        READ COMMITTED behaves the same. So the constraint rejects the duplicate
        and this retries, exactly as :meth:`put_dataset` does for dataset
        versions.

        Three outcomes are distinguished after an ``IntegrityError``, because
        collapsing them would bury a real error under eight retries:

        - the step key now exists - another writer served this same step first,
          so the next pass returns *its* row and this call is the no-op;
        - the ``(run_id, seq)`` pair is taken - the race, lost, so re-read and
          take the next number;
        - neither - a missing run tripping the foreign key, re-raised as itself.

        ``fetched_at`` is left to the column's ``DEFAULT now()`` when the model
        does not carry one - a DB column default, which is the first of ruling
        R-09's four legitimate timestamp sources, and the reason there is no
        clock read anywhere in this package.
        """
        for _ in range(SEQ_ALLOCATION_ATTEMPTS):
            existing = self._step_row(run_id, step.node_id, step.iteration)
            if existing is not None:
                return _step(existing)
            seq = self._next_step_seq(run_id)
            values: dict[str, Any] = {
                "run_id": run_id,
                "node_id": step.node_id,
                "iteration": step.iteration,
                "seq": seq,
                "served": step.served,
                "actual": step.actual,
                "recorded_at": step.recorded_at,
            }
            if step.fetched_at is not None:
                values["fetched_at"] = step.fetched_at
            try:
                with self._engine.begin() as conn:
                    conn.execute(insert(run_steps).values(**values))
            except IntegrityError:
                if self._step_row(run_id, step.node_id, step.iteration) is not None:
                    continue
                if self._step_seq_taken(run_id, seq):
                    continue
                raise
            written = self._step_row(run_id, step.node_id, step.iteration)
            if written is None:  # pragma: no cover - the row was just written
                raise StoreError(f"step {run_id}/{step.node_id}/{step.iteration} vanished")
            return _step(written)
        raise StoreError(f"could not allocate a seq for run {run_id} after repeated races")

    def set_step_actual(
        self, run_id: str, node_id: str, iteration: int, actual: Mapping[str, Any]
    ) -> StepRecord:
        """See :meth:`agentprops.storage.base.Store.set_step_actual`.

        Ruling R-33's method, built at M3 rather than M8 so that M7's two
        adapters implement it once instead of M8 reopening three signed-off
        adapters.

        ``recorded_at`` is stamped by the *database*, with the same
        ``func.now()`` the sibling ``fetched_at`` column uses as its default.
        R-33's signature carries no timestamp, and leaving the column null
        forever would make it unreachable - ``upsert_step`` accepts a
        ``recorded_at`` only on insert, and its repeat is a literal no-op. A
        clock read by the database is the same ruling R-09 category as a column
        default; there is still no clock read in Python anywhere in this
        package.

        **There is exactly one place this method decides anything**, and that is
        deliberate. ``WHERE actual IS NULL`` makes the ``UPDATE`` conditional, so
        a writer that lost the race changes nothing - and it then *loops back*
        rather than reading the row and returning it, because the row it would
        read holds the winner's value. The top of the loop is where an already
        recorded actual is compared with this caller's, and where a difference
        becomes a :class:`StoreError`.

        M3's fix round 1 got this wrong in exactly the way two decision sites
        always go wrong: the post-``UPDATE`` branch checked that *some* actual
        was now stored rather than that it was *this* one, so the losing writer
        in a genuine race received a successful ``StepRecord`` carrying the
        winner's value. A step's recorded actual is evidence, and evidence that
        silently belongs to a different writer is worse than an error. Looping
        back is the same shape :meth:`upsert_step` uses for its own lost race.
        """
        for _ in range(FIRST_WRITE_ATTEMPTS):
            row = self._step_row(run_id, node_id, iteration)
            if row is None:
                raise RecordNotFoundError(
                    f"no step {run_id}/{node_id}/{iteration}: "
                    "an actual cannot be recorded for a step that was never served"
                )
            stored: dict[str, Any] | None = row.actual
            if stored is not None:
                if _canonical(stored) == _canonical(dict(actual)):
                    return _step(row)
                raise StoreError(
                    f"step {run_id}/{node_id}/{iteration} already recorded a different actual"
                )
            with self._engine.begin() as conn:
                conn.execute(
                    update(run_steps)
                    .where(
                        run_steps.c.run_id == run_id,
                        run_steps.c.node_id == node_id,
                        run_steps.c.iteration == iteration,
                        run_steps.c.actual.is_(None),
                    )
                    .values(actual=dict(actual), recorded_at=func.now())
                )
            # Back to the top, whether that UPDATE applied or not. The next pass
            # re-reads and takes the identical-or-refuse decision above, which
            # returns this call's own value when it won and raises when a
            # concurrent writer's value got there first.
        raise StoreError(f"could not record an actual for {run_id}/{node_id}/{iteration}")

    # -------------------------------------------------------------------- health

    def health(self) -> StoreHealth:
        """See :meth:`agentprops.storage.base.Store.health`.

        Never raises: a store that cannot be reached reports
        ``healthy: false`` with zero counts, because ``store_status`` exists to
        answer "is the backend there" and an exception is a worse answer than
        "no". ``counts`` are row counts, so ``datasets`` counts every *version*
        and includes archived ones (ruling R-05).
        """
        try:
            with self._engine.connect() as conn:
                counts = StoreCounts(
                    blueprints=_count(conn, blueprints),
                    datasets=_count(conn, datasets),
                    runs=_count(conn, runs),
                )
        except SQLAlchemyError:
            return StoreHealth(
                backend=self._backend,
                healthy=False,
                counts=StoreCounts(blueprints=0, datasets=0, runs=0),
            )
        return StoreHealth(backend=self._backend, healthy=True, counts=counts)

    # ------------------------------------------------------------------ internals

    def _next_dataset_version(self, dataset_id: uuid.UUID) -> int:
        with self._engine.connect() as conn:
            current = conn.execute(
                select(func.max(datasets.c.version)).where(datasets.c.id == dataset_id)
            ).scalar()
        return int(current or 0) + 1

    def _blueprint_row(self, conn: Connection, agent_id: str, version: str) -> Row[Any] | None:
        """The stored status and document for one blueprint version, or ``None``.

        Takes the connection rather than opening its own, because
        :meth:`put_blueprint` needs the read and the write it decides on to be
        in one transaction: without that, a version could go from draft to
        published between the two and BP-016 would be checked against a status
        that no longer holds.
        """
        return conn.execute(
            select(blueprints.c.status, blueprints.c.document).where(
                blueprints.c.agent_id == agent_id,
                blueprints.c.version == version,
            )
        ).one_or_none()

    def _existing_skeleton(self, conn: Connection, skeleton_id: uuid.UUID) -> Row[Any] | None:
        """Whether a skeleton row exists, read on the caller's connection.

        A method rather than an inline ``select`` for the same two reasons as
        :meth:`_blueprint_row`: the read and the write it decides on stay in one
        transaction, and the losing side of a concurrent first write is
        reachable in a test by making this return ``None`` once.
        """
        return conn.execute(
            select(skeletons.c.id).where(skeletons.c.id == skeleton_id)
        ).one_or_none()

    def _existing_run(self, conn: Connection, run_id: str) -> Row[Any] | None:
        """Whether a run row exists, read on the caller's connection."""
        return conn.execute(select(runs.c.id).where(runs.c.id == run_id)).one_or_none()

    def _lineage_archived(self, dataset_id: uuid.UUID) -> bool | None:
        """The lineage's current archive state, or ``None`` if it has no versions yet.

        Ruling R-34: ``archived`` is a property of the *lineage*, not of a
        version. ``set_archived`` takes an id and no version and already flips
        every row; this is the other half - a new version inherits the state
        rather than taking the caller's word for it, so a lineage can never end
        up half-hidden from ``find_datasets``, which returns one row per
        lineage and would have no coherent answer for that.

        Read from the newest existing version, which under ``set_archived``'s
        lineage-wide update is the same as every other version's.
        """
        with self._engine.connect() as conn:
            row = conn.execute(
                select(datasets.c.archived)
                .where(datasets.c.id == dataset_id)
                .order_by(desc(datasets.c.version))
                .limit(1)
            ).one_or_none()
        return None if row is None else bool(row.archived)

    def _step_row(self, run_id: str, node_id: str, iteration: int) -> Row[Any] | None:
        with self._engine.connect() as conn:
            return conn.execute(
                select(run_steps).where(
                    run_steps.c.run_id == run_id,
                    run_steps.c.node_id == node_id,
                    run_steps.c.iteration == iteration,
                )
            ).one_or_none()

    def _next_step_seq(self, run_id: str) -> int:
        with self._engine.connect() as conn:
            current = conn.execute(
                select(func.max(run_steps.c.seq)).where(run_steps.c.run_id == run_id)
            ).scalar()
        return int(current or 0) + 1

    def _step_seq_taken(self, run_id: str, seq: int) -> bool:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(run_steps.c.seq).where(run_steps.c.run_id == run_id, run_steps.c.seq == seq)
            ).one_or_none()
        return row is not None

    def _dataset_version_exists(self, dataset_id: uuid.UUID, version: int) -> bool:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(datasets.c.version).where(
                    datasets.c.id == dataset_id, datasets.c.version == version
                )
            ).one_or_none()
        return row is not None

    def _dataset_row(
        self, ds: Dataset, document: dict[str, Any], version: int, archived: bool
    ) -> dict[str, Any]:
        """The promoted columns for one dataset version.

        ``created_at`` comes from ``provenance.created_at`` - ruling R-09, and
        the reason the column has no ``DEFAULT now()``. ``archived`` is passed
        in rather than read off ``ds`` because it belongs to the lineage, not to
        the model the caller handed over (ruling R-34).
        """
        return {
            "id": ds.id,
            "version": version,
            "agent_id": ds.blueprint.agent_id,
            "bp_version": ds.blueprint.version,
            "archived": archived,
            "labels": ds.labels,
            "seed": ds.seed,
            "title": ds.provenance.title,
            "intent": ds.provenance.intent,
            "author_name": ds.provenance.author.name,
            "author_handle": ds.provenance.author.handle,
            "author_agent": ds.provenance.author.agent,
            "supersedes": (
                None
                if ds.provenance.supersedes is None
                else _require_uuid(ds.provenance.supersedes, "provenance.supersedes")
            ),
            "document": document,
            "created_at": ds.provenance.created_at,
        }

    def _apply_dataset_filters(self, statement: Select[Any], q: DatasetQuery) -> Select[Any]:
        """``dataset_find``'s four SQL filters. The fifth, ``q``, is not SQL.

        **labels** is the one contracts section 7 says SQLite handles
        differently, and it needs no dialect branch: one equality per dimension
        against the extracted JSON value, and
        ``labels["tier"].as_string() == "regional"`` compiles to
        ``JSON_EXTRACT(labels, '$."tier"')`` on SQLite and ``labels ->> 'tier'``
        on Postgres from this one expression. Every dimension given must match,
        which is ``DatasetQuery.labels``'s documented meaning. The Postgres GIN
        index accelerates that expression without changing it.

        **``q`` is deliberately absent from this method.** It is a case-folded
        substring match (ruling R-36 makes substring semantics the contract, so
        M7's Postgres adapter uses ``ILIKE`` or a ``pg_trgm`` index, never
        full-text search) - and there is no *SQL* expression that folds case
        identically on all three backends. M3 shipped
        ``LOWER(title) LIKE '%term%'`` against a term folded by Python, which is
        two different fold functions in one comparison. So the fold and the
        match both live in Python now, in :meth:`find_datasets`, where one
        implementation serves every backend. See :func:`_folded`.

        M7 may add an ``ILIKE``/``pg_trgm`` prefilter on Postgres purely for
        speed, provided it matches a *superset* of what the Python fold matches;
        Postgres's ``lower()`` and Python's ``casefold()`` are close enough for
        that to hold, and the conformance suite - which asserts results, never
        plans - is what would catch it if it did not.
        """
        if q.agent_id is not None:
            statement = statement.where(datasets.c.agent_id == q.agent_id)
        if q.blueprint_version is not None:
            statement = statement.where(datasets.c.bp_version == q.blueprint_version)
        if q.author is not None:
            statement = statement.where(datasets.c.author_handle == q.author)
        if q.labels:
            for dimension, value in q.labels.items():
                statement = statement.where(datasets.c.labels[dimension].as_string() == value)
        return statement


def _protocol_conformance(store: SqlStore) -> Store:
    """``mypy --strict`` checks that :class:`SqlStore` satisfies ``Store``.

    ``runtime_checkable`` only sees method *names*; this sees the signatures,
    and it fails the type check rather than a test if an argument name or a
    return type drifts from `docs/contracts.md` section 6.
    """
    return store


# --------------------------------------------------------------------------
# row to model
# --------------------------------------------------------------------------


def _count(conn: Connection, table: Table) -> int:
    return int(conn.execute(select(func.count()).select_from(table)).scalar() or 0)


def _slice(rows: list[Row[Any]], limit: int | None, offset: int | None) -> list[Row[Any]]:
    """:func:`_paginate`'s meaning, applied to a list.

    Used only by ``find_datasets``'s ``q`` branch, where the filter cannot be
    pushed into SQL, so ``LIMIT``/``OFFSET`` cannot be either: a page sliced
    before the filter runs comes back short.
    """
    start = offset or 0
    return rows[start:] if limit is None else rows[start : start + limit]


def _paginate(statement: Select[Any], limit: int | None, offset: int | None) -> Select[Any]:
    """``limit`` and ``offset``, both optional and unconstrained (ruling R-04).

    `service/` owns the defaults, so an omitted ``limit`` means "no limit"
    here rather than a number this layer invented. SQLAlchemy emits the
    ``LIMIT -1`` SQLite needs to accept a bare ``OFFSET``.
    """
    if limit is not None:
        statement = statement.limit(limit)
    if offset is not None:
        statement = statement.offset(offset)
    return statement


def _dataset_summary(row: Row[Any]) -> DatasetSummary:
    """A ``DatasetSummary`` from a row, built from the promoted columns.

    Which is what they were promoted for - "so find can filter and sort without
    parsing JSON". The document is read for exactly one field: contracts 2.2.1
    puts a 200-character ``narrative`` excerpt in the summary and the DDL
    promotes no column for it.
    """
    document: dict[str, Any] = row.document
    narrative = str(document.get("narrative", ""))
    return DatasetSummary(
        id=row.id,
        version=int(row.version),
        title=str(row.title),
        intent=str(row.intent),
        labels=_labels(row.labels),
        author=Author(
            name=str(row.author_name),
            handle=str(row.author_handle),
            agent=str(row.author_agent),
        ),
        blueprint=BlueprintRef(agent_id=str(row.agent_id), version=str(row.bp_version)),
        narrative_excerpt=narrative[:NARRATIVE_EXCERPT_CHARS],
        archived=bool(row.archived),
        created_at=row.created_at,
    )


def _skeleton(row: Row[Any]) -> Skeleton:
    return Skeleton(
        id=row.id,
        agent_id=str(row.agent_id),
        bp_version=str(row.bp_version),
        labels=dict(row.labels),
        seed=int(row.seed),
        manifest=list(row.manifest),
        parts=dict(row.parts),
        submitted_as=row.submitted_as,
        created_at=row.created_at,
    )


def _step(row: Row[Any]) -> StepRecord:
    return StepRecord(
        node_id=str(row.node_id),
        iteration=int(row.iteration),
        served=dict(row.served),
        actual=row.actual,
        recorded_at=row.recorded_at,
        seq=int(row.seq),
        fetched_at=row.fetched_at,
    )
