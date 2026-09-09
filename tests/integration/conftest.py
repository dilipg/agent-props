"""The ``store`` fixture, parameterised over backends, and the golden aggregates.

`docs/contracts.md` section 6: the conformance suite is "written once against
[the Protocol] and parameterised over all three backends". This is the half that
makes that literally true - every test in this directory takes ``store``, typed
as :class:`~agentprops.storage.Store`, and never names an adapter.

M7's work here was exactly what M3 predicted: extend
``IMPLEMENTED_STORE_BACKENDS`` in `tests/conftest.py` and add two branches to
:func:`store`. **Not a line of `test_store_conformance.py` changed**, which is
the acceptance criterion the ``Store`` Protocol was being tested against.

How the two container-backed backends behave when there is no container
-----------------------------------------------------------------------

They **skip, with the reason and the URL in the message**, once - the reason is
remembered in :data:`_UNREACHABLE`, because a session-scoped fixture that skips
is not cached and an unreachable Postgres otherwise costs one connect attempt
per test. They only ever run when ``--store`` names them: :data:`conftest.DEFAULT_STORE_BACKENDS` is
SQLite alone, which is the containerless mode CI uses. A skip that says
"connect to postgresql://... failed" is a different thing from a silent absence,
and :mod:`integration.test_backend_selection` is the guard that a selected
backend cannot quietly contribute zero tests.

Isolation, per backend
----------------------

Every test gets an **empty** store, because half the conformance assertions
count rows. SQLite gets a fresh file in ``tmp_path``. Postgres and Mongo have no
per-test file, so they are reset instead: ``METADATA.drop_all`` then
``create_all`` for Postgres, ``drop_database`` then the index declarations for
Mongo. The engine and the client are **session-scoped**, so the reset is a
handful of statements rather than a reconnect - a per-test connection to
Postgres is most of the wall clock of a run this size.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from agentprops.models import SECTION_IDS, Blueprint, Dataset, Run, RunPin, Section, Skeleton
from agentprops.storage import (
    METADATA,
    MongoStore,
    SqlStore,
    Store,
    create_engine_for,
    create_schema,
    sqlite_url,
)
from conftest import (
    FIXTURES_DIR,
    FROZEN_NOW,
    IMPLEMENTED_STORE_BACKENDS,
    mongo_test_url,
    postgres_test_url,
    selected_store_backends,
)

__all__ = ["FROZEN_NOW"]

#: The database the Mongo branch creates, drops and recreates. Named for what it
#: is, so nobody points ``AGENTPROPS_TEST_MONGO_URL`` at a database they wanted
#: to keep and loses it: the name is **not** taken from the URL's path.
#:
#: That is not a hypothetical. This machine had a **second** MongoDB already
#: listening on 27017, holding an unrelated production database, and the default
#: URL reached it - so an M7 run created and dropped this database on a server
#: nobody meant to test. Taking the name from the URL's path would have made the
#: same run drop `pascal-prod`. A constant is the whole of the protection.
MONGO_TEST_DATABASE = "agentprops_conformance"

#: The skip reason for a server that has already been shown to be unreachable,
#: remembered so it is *shown* to be unreachable exactly once per run.
#:
#: A session-scoped fixture that calls ``pytest.skip`` is **not** cached -
#: pytest re-runs it for every test that requests it - so an unreachable
#: Postgres cost one connect attempt per skipped test. Measured before this
#: existed: ``--store postgres`` against a dead port took **4 minutes 32
#: seconds** to report 80 skips, which is close enough to "the suite hung" that
#: a developer would stop believing the skip. With the reason remembered it is
#: four seconds.
#:
#: A plain module-level dict rather than a fixture, because a fixture cannot
#: outlive the thing it is caching the failure of.
_UNREACHABLE: dict[str, str] = {}


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parameterise every ``store``-taking test over the selected backends."""
    if "store" in metafunc.fixturenames:
        metafunc.parametrize(
            "store",
            selected_store_backends(metafunc.config),
            indirect=True,
            ids=lambda backend: str(backend),
        )


#: How long the Postgres probe waits before deciding there is no server.
#:
#: Passed as a URL parameter, which psycopg honours, because
#: :func:`~agentprops.storage.create_engine_for` deliberately takes no options -
#: a store is opened by a URL and nothing else. Five seconds, because the OS
#: default is not a timeout at all: measured on Windows, a connect to a
#: *black-holed* localhost port (SYN dropped rather than refused) took **260
#: seconds** to fail, which turns "there is no Postgres" into "the suite hung".
#: A refused port answers instantly either way.
_CONNECT_TIMEOUT_S = 5


def _with_connect_timeout(url: str) -> str:
    return url + ("&" if "?" in url else "?") + f"connect_timeout={_CONNECT_TIMEOUT_S}"


@pytest.fixture(scope="session")
def postgres_engine() -> Iterator[Engine]:
    """One engine for the whole session, or a skip naming the URL that failed.

    Session-scoped because the reset below is cheap and a connection is not:
    reconnecting per test is most of the wall clock of a run this size. The
    connection is *proved* here rather than at first use, so an unreachable
    server produces one clear skip reason on every test instead of a
    ``ConnectionRefusedError`` inside whichever assertion happened to run first.
    """
    if "postgres" in _UNREACHABLE:
        pytest.skip(_UNREACHABLE["postgres"])
    url = _with_connect_timeout(postgres_test_url())
    engine = create_engine_for(url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except SQLAlchemyError as exc:
        engine.dispose()
        _UNREACHABLE["postgres"] = (
            f"no Postgres at {url}: {type(exc).__name__}. Start one with "
            f"`docker compose --profile shared up -d postgres`, or set "
            f"AGENTPROPS_TEST_POSTGRES_URL."
        )
        pytest.skip(_UNREACHABLE["postgres"])
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def mongo_client() -> Iterator[MongoClient[dict[str, Any]]]:
    """One client for the whole session, or a skip naming the URL that failed."""
    if "mongo" in _UNREACHABLE:
        pytest.skip(_UNREACHABLE["mongo"])
    url = mongo_test_url()
    client: MongoClient[dict[str, Any]] = MongoClient(
        url, tz_aware=True, uuidRepresentation="standard", serverSelectionTimeoutMS=3000
    )
    try:
        client.admin.command("ping")
    except PyMongoError as exc:
        client.close()
        _UNREACHABLE["mongo"] = (
            f"no MongoDB at {url}: {type(exc).__name__}. Start one with "
            f"`docker compose --profile local up -d mongo`, or set "
            f"AGENTPROPS_TEST_MONGO_URL."
        )
        pytest.skip(_UNREACHABLE["mongo"])
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Store]:
    """One **empty** store per test, on the backend named by ``--store``.

    Empty matters: half the conformance suite counts rows, and
    ``health().counts`` is asserted exactly.

    SQLite gets a file-backed database rather than ``:memory:``, so the store is
    exercised through a real connection pool and a real file - the mode
    CLAUDE.md calls containerless and CI uses. Its schema is created from
    ``METADATA``; `test_migrations.py` is what proves that schema and the
    migration's agree, on **both** SQL dialects, and one test there runs a write
    journey against a migrated database.

    The two container-backed branches request their session-scoped connection
    fixture, which is where an unreachable server becomes a skip with a reason.
    """
    backend = str(request.param)
    if backend not in IMPLEMENTED_STORE_BACKENDS:  # pragma: no cover - all three exist at M7
        pytest.skip(
            f"the {backend} storage adapter is not implemented; "
            f"implemented: {', '.join(IMPLEMENTED_STORE_BACKENDS)}"
        )
    if backend == "postgres":
        yield from _postgres_store(request)
        return
    if backend == "mongo":
        yield from _mongo_store(request)
        return
    adapter = SqlStore.from_url(sqlite_url(tmp_path / "agentprops.db"))
    create_schema(adapter.engine)
    try:
        yield adapter
    finally:
        adapter.dispose()


def _postgres_store(request: pytest.FixtureRequest) -> Iterator[Store]:
    """An empty Postgres store over the session engine.

    ``drop_all`` before ``create_all`` rather than after, deliberately: a run
    interrupted mid-test leaves tables behind, and dropping first means the next
    run is clean without anyone having to remember. Dropping a *table* is DDL on
    a throwaway database, not the deletion of a row anyone authored, so ground
    rule 6 is untouched - the same reasoning `test_migrations.py` records for
    ``downgrade base``.

    **A consequence worth knowing, because it looks like a bug.** This fixture
    creates the schema with ``create_all`` and never stamps ``alembic_version``,
    while `test_migrations.py` migrates and ``test_the_migration_is_reversible``
    downgrades to base - so after a full run the shared Postgres test database
    holds the five tables with an *empty* ``alembic_version``, and a
    ``uv run alembic check`` against it reports "Target database is not up to
    date". That is the fixtures' residue on a throwaway database and not schema
    drift: reset the schema, ``alembic upgrade head``, and the check is clean and
    idempotent. ``test_the_migrated_schema_matches_sql_py[postgres]`` runs the
    same comparison ``alembic check`` runs, against a database it migrated
    itself, which is the assertion that actually holds the line.
    """
    engine: Engine = request.getfixturevalue("postgres_engine")
    METADATA.drop_all(engine)
    METADATA.create_all(engine)
    yield SqlStore(engine)


def _mongo_store(request: pytest.FixtureRequest) -> Iterator[Store]:
    """An empty Mongo store over the session client.

    The database name is :data:`MONGO_TEST_DATABASE`, **not** whatever the URL's
    path says: this fixture drops the database, and a URL is the wrong place to
    take that decision from.
    """
    client: MongoClient[dict[str, Any]] = request.getfixturevalue("mongo_client")
    client.drop_database(MONGO_TEST_DATABASE)
    adapter = MongoStore(client, MONGO_TEST_DATABASE)
    adapter.create_schema()
    yield adapter


def _load(relative: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((FIXTURES_DIR / relative).read_text(encoding="utf-8"))
    return document


@pytest.fixture
def blueprint() -> Blueprint:
    """The golden `location-onboarding` blueprint, version 1.0.0."""
    return Blueprint.model_validate(_load("blueprints/location-onboarding-1.0.0.json"))


@pytest.fixture
def dataset() -> Dataset:
    """`priya-missing-docs`: the loop-path golden dataset."""
    return Dataset.model_validate(_load("datasets/priya-missing-docs.json"))


@pytest.fixture
def dataset_document() -> dict[str, Any]:
    """The same dataset as raw JSON, for the byte-for-byte round-trip assertion."""
    return _load("datasets/priya-missing-docs.json")


@pytest.fixture
def other_dataset() -> Dataset:
    """`arun-escalated`: the compliant-branch golden dataset.

    Its ``provenance.created_at`` is 11:02:47, after `priya`'s 10:14:22, which
    is what makes ``find_datasets``'s ordering assertion mean something.
    """
    return Dataset.model_validate(_load("datasets/arun-escalated.json"))


@pytest.fixture
def published(store: Store, blueprint: Blueprint) -> Blueprint:
    """The golden blueprint, published, so datasets have a parent to reference.

    The DDL's ``FOREIGN KEY (agent_id, bp_version)`` is real on both SQL
    dialects, and writing the blueprint first is the honest order regardless -
    DS-001 requires a dataset to name an existing published blueprint.
    """
    return store.put_blueprint(blueprint, publish=True)


@pytest.fixture
def skeleton(blueprint: Blueprint, dataset: Dataset) -> Skeleton:
    """A skeleton with the five-section manifest ruling R-06 fixes.

    ``labels`` and ``seed`` are the golden dataset's, because they are
    ``dataset_skeleton``'s two inputs and M5 added them to the row: a skeleton
    that does not carry them cannot be assembled into a dataset.
    """
    return Skeleton(
        id=UUID("3f8c1a20-0000-4000-8000-00000000aaaa"),
        agent_id=blueprint.agent_id,
        bp_version=blueprint.version,
        labels=dict(dataset.labels),
        seed=dataset.seed,
        manifest=[
            Section(
                id=section_id,
                required=True,
                pointers=[f"/{section_id.replace('.', '/')}"],
                description=f"fill {section_id}",
            )
            for section_id in SECTION_IDS
        ],
        created_at=FROZEN_NOW,
    )


def make_run(run_id: str, dataset: Dataset, version: int = 1, **overrides: Any) -> Run:
    """A run pinned to ``(dataset.id, version)``.

    The run id is a client-generated opaque string - ground rule 9's one
    sanctioned ``uuid4()`` is in the *client*, and these are literals so the
    tests stay deterministic.
    """
    values: dict[str, Any] = {
        "id": run_id,
        "agent_id": dataset.blueprint.agent_id,
        "pin": RunPin(
            dataset_id=dataset.id,
            dataset_version=version,
            blueprint_version=dataset.blueprint.version,
        ),
        "run_class": "dev",
        "status": "running",
        "started_at": FROZEN_NOW,
    }
    values.update(overrides)
    return Run(**values)
