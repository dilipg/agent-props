"""The migration is applied to a fresh database, and its schema is compared to `sql.py`.

A migration nobody runs is not a migration. These tests run one.

This suite is parameterised over the two **SQL dialects** and not over the
three backends, unlike `test_store_conformance.py`. Alembic is SQL-only -
contracts section 8 gives Mongo collections and indexes, not DDL - so there is
nothing here for a document store to conform to, and `mongo.py` declares its
indexes through ``MongoStore.create_schema()`` instead, which
`test_store_conformance.py` exercises on every test.

**M7 added the Postgres half, and it was the point of the exercise.** M3 left a
note saying ``alembic check`` might report a false positive against a real
Postgres, because ``METADATA`` declares ``desc(runs.c.started_at)`` while the
migration writes ``sa.text("started_at DESC")`` and autogenerate cannot reliably
reflect an expression index. Against a real Postgres 17 it is **clean** - the
index is created as ``btree (agent_id, run_class, started_at DESC)`` and no
operation is detected - so the note is closed rather than worked around. The
Postgres parameter is what keeps it closed.

The Postgres case skips with a reason when there is no server, exactly as the
conformance suite does; ``uv run pytest`` with no flags still runs the SQLite
half, which is the containerless mode CI uses.

The comparison is Alembic's own autogenerate diff, which is exactly what
``alembic check`` runs. Reflecting the database and eyeballing column names
would prove less: autogenerate compares types, nullability, primary keys,
foreign keys and indexes, and it is the same machinery that will tell M7
whether a Postgres database matches.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, insert, inspect, select, text

from agentprops.models import Blueprint, Dataset, DatasetQuery
from agentprops.storage import (
    METADATA,
    POSTGRES_ONLY_INDEXES,
    SqlStore,
    create_engine_for,
    sqlite_url,
)
from agentprops.storage.sql import (
    blueprints,
    datasets,
    include_object,
    run_steps,
    runs,
    skeletons,
)
from conftest import postgres_session_url
from integration.conftest import make_run

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

#: Every table the DDL declares, plus Alembic's own bookkeeping table.
EXPECTED_TABLES = frozenset(
    {"blueprints", "datasets", "skeletons", "runs", "run_steps", "alembic_version"}
)


def alembic_config(url: str) -> Config:
    """A config pointed at ``url``, however the ambient environment is set up.

    The URL is passed as ``-x url=``, which `env.py` prefers over both
    ``AGENTPROPS_DB_URL`` and `alembic.ini` - so a developer with that variable
    exported cannot have these tests migrate their real database.
    """
    config = Config(str(ALEMBIC_INI))
    config.cmd_opts = argparse.Namespace(x=[f"url={url}"])
    return config


#: The two SQL dialects this suite runs against. Not a backend list - Mongo is
#: absent because there is no Alembic for a document store, which contracts
#: section 8 settles by giving collections and indexes rather than DDL.
SQL_DIALECTS = ("sqlite", "postgres")


@pytest.fixture(params=SQL_DIALECTS, ids=lambda name: str(name))
def migrated(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    """A fresh database with ``alembic upgrade head`` applied. Returns its URL.

    SQLite gets a new file. Postgres gets its ``public`` schema dropped and
    recreated first, which is the equivalent of a new file and is what makes
    ``test_the_migration_creates_every_table`` meaningful there: a shared
    database carrying a previous run's tables would satisfy that assertion
    without the migration having done anything.

    Dropping a schema is DDL on a throwaway database rather than the deletion of
    a row anyone authored, so ground rule 6 is untouched - the same reasoning
    ``test_the_migration_is_reversible`` records for ``downgrade base``.
    """
    dialect = str(request.param)
    if dialect == "sqlite":
        url = sqlite_url(tmp_path / "migrated.db")
        command.upgrade(alembic_config(url), "head")
        return url

    engine = request.getfixturevalue("postgres_engine")
    # The **plain** URL, with no driver named, which is what a compose file and
    # an environment variable carry - so this exercises the normalisation in
    # `create_engine_for` that `docker compose --profile shared up` needs.
    #
    # This session's database rather than the server's (ruling R-63), which is
    # what makes the `DROP SCHEMA public CASCADE` below a local act: before
    # R-63 it emptied a database a concurrent run was mid-test against.
    url = postgres_session_url()
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    command.upgrade(alembic_config(url), "head")
    return url


def dialect_of(url: str) -> str:
    """``sqlite`` or ``postgres``, from the URL the ``migrated`` fixture returned."""
    return "sqlite" if url.startswith("sqlite") else "postgres"


def test_alembic_ini_is_at_the_repository_root() -> None:
    """Where `alembic init` writes it, and where every alembic command looks.

    The M0 controller ruling assigns this file to M3 for that reason. If it
    moves, ``uv run alembic upgrade head`` stops working from the repository
    root with no flags, which is the command the README and CLAUDE.md promise.
    """
    assert ALEMBIC_INI.is_file()
    text = ALEMBIC_INI.read_text(encoding="utf-8")
    assert "script_location = %(here)s/src/agentprops/storage/migrations" in text


def test_the_migration_creates_every_table(migrated: str) -> None:
    engine = create_engine_for(migrated)
    try:
        assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES
    finally:
        engine.dispose()


def test_the_migrated_schema_matches_sql_py(migrated: str) -> None:
    """``alembic check``, as a test: no new upgrade operations detected.

    This is the assertion that makes it safe for the conformance fixture to
    build its schema with ``create_all``: the two paths produce the same schema,
    so a test passing against one is evidence about the other. If they ever
    diverge - a column added to `sql.py` without a revision - this fails and
    names the difference.

    ``compare_server_default=True`` goes beyond what ``alembic check`` compares
    by default, and it is here for one column: under ruling R-09
    ``datasets.created_at`` must have **no** default, because a ``DEFAULT now()``
    would be re-stamped on re-import and ``dataset_find``'s ordering would stop
    being reproducible. Without this flag that is the one load-bearing default
    in the schema sitting outside the drift gate.

    **Run against both dialects from M7**, which closes M3's open note: the
    suspicion was that ``runs_lookup``'s ``DESC`` column would make this report
    a false positive on Postgres, since autogenerate cannot reliably reflect an
    expression index. Against Postgres 17 it reports nothing.
    """
    engine = create_engine_for(migrated)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_object": include_object,
                    "compare_type": True,
                    "compare_server_default": True,
                },
            )
            differences = compare_metadata(context, METADATA)
    finally:
        engine.dispose()
    assert differences == []


def test_the_migration_produces_the_declared_keys_and_indexes(migrated: str) -> None:
    """The parts of contracts section 7 a column list does not cover.

    Spelled out rather than left to the autogenerate diff because these four
    are load-bearing behaviour, not shape: ``(id, version)`` is what guards
    dataset version allocation, ``(run_id, node_id, iteration)`` is what makes
    ``upsert_step`` idempotent, and the two foreign keys are the referential
    integrity SQLite only enforces because the engine factory turns it on.
    """
    engine = create_engine_for(migrated)
    try:
        inspector = inspect(engine)
        assert inspector.get_pk_constraint("datasets")["constrained_columns"] == ["id", "version"]
        assert inspector.get_pk_constraint("run_steps")["constrained_columns"] == [
            "run_id",
            "node_id",
            "iteration",
        ]
        assert inspector.get_pk_constraint("blueprints")["constrained_columns"] == [
            "agent_id",
            "version",
        ]
        dataset_fk = inspector.get_foreign_keys("datasets")
        assert [fk["referred_table"] for fk in dataset_fk] == ["blueprints"]
        assert dataset_fk[0]["constrained_columns"] == ["agent_id", "bp_version"]
        run_fk = {fk["referred_table"] for fk in inspector.get_foreign_keys("runs")}
        assert run_fk == {"datasets"}
        portable = {"datasets_lookup", "datasets_author"}
        # The dialect-specific ones are asserted by name in their own test; here
        # the claim is that the *portable* indexes exist on both dialects and
        # that nothing unexpected joined them.
        assert (
            {index["name"] for index in inspector.get_indexes("datasets")} - POSTGRES_ONLY_INDEXES
        ) == portable
        assert {index["name"] for index in inspector.get_indexes("runs")} == {"runs_lookup"}
    finally:
        engine.dispose()


def test_the_postgres_only_index_is_present_on_postgres_and_absent_on_sqlite(
    migrated: str,
) -> None:
    """Contracts section 7's one remaining dialect-specific index, both directions.

    ``datasets_labels_gin`` has no SQLite equivalent, and SQLite falls back to
    JSON extraction - which is what ``find_datasets`` implements and what the
    conformance suite asserts the *results* of. So its absence on SQLite is a
    documented property rather than a gap, and its presence on Postgres is the
    half M3 could not check.

    Both directions in one test, deliberately: an "absent on SQLite" assertion
    alone passes just as well against a migration that creates the index
    nowhere, which is a real way to lose a GIN index and never notice.

    ``datasets_search`` is asserted absent on **both**. It was contracts section
    7's ``to_tsvector`` index; ruling R-36 superseded it and M7 dropped it,
    because ``find_datasets`` issues no SQL text match for any such index to
    serve. See ``POSTGRES_ONLY_INDEXES``.
    """
    engine = create_engine_for(migrated)
    try:
        names = {index["name"] for index in inspect(engine).get_indexes("datasets")}
    finally:
        engine.dispose()

    assert "datasets_search" not in names, "the dead to_tsvector index is back"
    if dialect_of(migrated) == "postgres":
        assert names >= POSTGRES_ONLY_INDEXES, f"the Postgres-only indexes are missing: {names}"
    else:
        assert names.isdisjoint(POSTGRES_ONLY_INDEXES)


def test_the_runs_index_keeps_its_descending_column(migrated: str) -> None:
    """M3's open note, closed: ``started_at DESC`` survives and ``alembic check`` is clean.

    ``METADATA`` declares ``desc(runs.c.started_at)`` and the migration writes
    ``sa.text("started_at DESC")``, and Alembic's autogenerate cannot reliably
    reflect or compare an expression index column - so M3 recorded a suspicion
    that ``alembic check`` would report a false positive against a real
    Postgres. It does not, which
    :func:`test_the_migrated_schema_matches_sql_py` establishes on both
    dialects. This is the other half: the index exists, and on Postgres it
    really is descending.

    Read from the reflected definition rather than from the index's column list,
    because a column list is where the ``DESC`` gets lost - which is exactly why
    the suspicion was reasonable.
    """
    engine = create_engine_for(migrated)
    try:
        assert "runs_lookup" in {index["name"] for index in inspect(engine).get_indexes("runs")}
        if dialect_of(migrated) == "postgres":
            with engine.connect() as connection:
                definition = connection.execute(
                    text("select indexdef from pg_indexes where indexname = 'runs_lookup'")
                ).scalar_one()
            assert "started_at DESC" in definition, definition
    finally:
        engine.dispose()


#: Every ``server_default`` in the schema, with the value the DDL says it
#: produces. Read as a table so the test below cannot exercise three of them and
#: claim seven - which is what it did until this round: its docstring promised a
#: row omitting "every one of them" and its body inserted one ``skeletons`` row
#: and read back ``parts``.
#:
#: ``None`` means "a timestamp, so any value will do" - the assertion for those
#: is that the column is populated at all, because the value is a clock reading
#: and there is nothing to compare it to.
SERVER_DEFAULTS: tuple[tuple[str, str, object], ...] = (
    ("blueprints", "created_at", None),
    ("datasets", "archived", False),
    ("skeletons", "parts", {}),
    ("skeletons", "created_at", None),
    ("runs", "run_class", "dev"),
    ("runs", "warnings", []),
    ("runs", "external_refs", {}),
    ("runs", "started_at", None),
    ("run_steps", "fetched_at", None),
)


def _defaulted_rows(engine: Engine) -> dict[str, Any]:
    """One row per table, each **omitting every defaulted column**, read back.

    Written through SQLAlchemy Core rather than raw SQL so the values bind
    through the column types: a ``jsonb`` column needs a cast from a text
    parameter on Postgres and does not on SQLite, and hand-writing that is how a
    test ends up proving something about its own SQL.

    Insert order follows the foreign keys, which both SQL dialects enforce.
    """
    when = datetime(2026, 9, 8, 10, 14, 22, tzinfo=UTC)
    identifier = UUID("3f8c1a20-0000-4000-8000-0000000000b1")
    with engine.begin() as connection:
        connection.execute(
            insert(blueprints).values(
                agent_id="defaults", version="1.0.0", status="draft", document={}
            )
        )
        connection.execute(
            insert(datasets).values(
                id=identifier,
                version=1,
                agent_id="defaults",
                bp_version="1.0.0",
                labels={},
                seed=1,
                title="t",
                intent="i",
                author_name="n",
                author_handle="h",
                author_agent="human",
                document={},
                created_at=when,
            )
        )
        connection.execute(
            insert(skeletons).values(
                id=identifier,
                agent_id="defaults",
                bp_version="1.0.0",
                labels={},
                seed=1,
                manifest=[],
            )
        )
        connection.execute(
            insert(runs).values(
                id="defaults-run",
                agent_id="defaults",
                dataset_id=identifier,
                dataset_ver=1,
                bp_version="1.0.0",
                status="running",
            )
        )
        connection.execute(
            insert(run_steps).values(
                run_id="defaults-run", node_id="n", iteration=0, seq=1, served={}
            )
        )
        return {
            table: dict(connection.execute(select(target)).one()._mapping)
            for table, target in (
                ("blueprints", blueprints),
                ("datasets", datasets),
                ("skeletons", skeletons),
                ("runs", runs),
                ("run_steps", run_steps),
            )
        }


def test_every_server_default_executes_on_this_dialect(migrated: str) -> None:
    r"""M3's second open note, closed by *executing* every default rather than one.

    Nine ``server_default``\ s across five tables, and M3 verified all of them
    only as **compiled SQL**: "Postgres coerces an unknown-type literal to
    ``jsonb``, which is why they are written that way, but 'coerces' is a claim
    about the server and no server has been asked."

    Asked here, on both dialects, by inserting a row per table that omits every
    defaulted column and reading the values back. Two of the nine are the ones
    that would fail loudly rather than subtly: ``sa.false()`` on a boolean,
    because ``DEFAULT 0`` - which is what autogenerate rendered for SQLite - is
    an error on Postgres; and ``sa.text("'{}'")`` on a ``jsonb`` column, which
    is the coercion M3's note was actually about.

    This test used to exercise **one** of them while its docstring claimed all,
    which is the same class of overclaim this milestone corrected twice
    elsewhere. :data:`SERVER_DEFAULTS` is now a table, so the count is the
    schema's rather than the author's.
    """
    engine = create_engine_for(migrated)
    try:
        rows = _defaulted_rows(engine)
    finally:
        engine.dispose()

    for table, column, expected in SERVER_DEFAULTS:
        value = rows[table][column]
        if expected is None:
            assert value is not None, f"{table}.{column} has no value, so its default never ran"
            assert isinstance(value, datetime), f"{table}.{column} is {value!r}, not a timestamp"
            continue
        assert value == expected, f"{table}.{column} defaulted to {value!r}, not {expected!r}"


def test_the_defaults_table_covers_every_declared_default() -> None:
    """The guard for the table, so the test above cannot go stale silently.

    :data:`SERVER_DEFAULTS` is compared against ``METADATA`` itself, so a column
    that gains or loses a ``server_default`` fails here rather than being
    quietly unexercised - which is exactly the failure the test above had.
    """
    declared = {
        (table.name, column.name)
        for table in METADATA.sorted_tables
        for column in table.columns
        if column.server_default is not None
    }
    covered = {(table, column) for table, column, _ in SERVER_DEFAULTS}
    assert covered == declared, (
        f"uncovered: {sorted(declared - covered)}; stale: {sorted(covered - declared)}"
    )


def test_a_write_journey_runs_against_a_migrated_database(
    migrated: str, blueprint: Blueprint, dataset: Dataset
) -> None:
    """The migrated schema is not just shaped right, it works.

    A schema comparison can pass while a column type is unusable through the
    adapter - a JSON column that will not accept a dict, a timestamp that comes
    back naive. So the last check is the adapter's own round trip against a
    database it did not create.
    """
    store = SqlStore.from_url(migrated)
    try:
        store.put_blueprint(blueprint, publish=True)
        stored = store.put_dataset(dataset)
        assert stored.version == 1
        assert store.get_dataset(str(dataset.id), 1) == stored
        (summary,) = store.find_datasets(DatasetQuery(labels={"tier": "regional"}))
        assert summary.created_at == dataset.provenance.created_at
        run = store.put_run(make_run("run-migrated", stored))
        assert store.get_run(run.id) == run
        assert store.health().healthy is True
    finally:
        store.dispose()


def test_the_migration_is_reversible(migrated: str) -> None:
    """``downgrade base`` drops the schema it created.

    Dropping a table is DDL, not a row delete: ground rule 6 forbids deleting
    data, not tearing down a throwaway database. Nothing in `storage/` calls
    this, and the no-delete guard in `tests/unit/test_storage_no_delete.py`
    covers the migration package too.
    """
    command.downgrade(alembic_config(migrated), "base")
    engine = create_engine_for(migrated)
    try:
        remaining = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert remaining == {"alembic_version"}


def test_a_plain_postgres_url_names_the_installed_driver() -> None:
    """The bug ``docker compose --profile shared up`` found, as a unit-sized test.

    A plain ``postgresql://`` URL selects SQLAlchemy's *default* Postgres
    driver, which is psycopg2 - not a dependency of this project. The
    ``shared`` profile hands ``AGENTPROPS_STORE`` straight to
    ``alembic -x url=...``, so before ``create_engine_for`` normalised the URL,
    the container started, ran the migration and died on
    ``ModuleNotFoundError: No module named 'psycopg2'`` - while the service half
    of the very same URL worked, because ``context_for`` normalised and
    `migrations/env.py` did not.

    Fixed at the seam rather than at the second caller: ``create_engine_for`` is
    the one place a SQL URL becomes an engine, so it is the one place that can
    be the answer for every caller. This asserts it for both spellings, and that
    an explicitly named driver is left alone - because silently rewriting
    ``postgresql+asyncpg://`` would be a different bug of the same shape.

    No server needed: ``create_engine`` resolves and imports the DBAPI without
    connecting, which is exactly the step that was failing.
    """
    for plain in ("postgresql://u:p@h/db", "postgres://u:p@h/db"):
        engine = create_engine_for(plain)
        try:
            assert engine.dialect.driver == "psycopg", plain
        finally:
            engine.dispose()

    named = create_engine_for("postgresql+psycopg://u:p@h/db")
    try:
        assert named.dialect.driver == "psycopg"
    finally:
        named.dispose()

    sqlite = create_engine_for(sqlite_url())
    try:
        assert sqlite.dialect.name == "sqlite", "a SQLite URL was rewritten"
    finally:
        sqlite.dispose()
