"""The migration is applied to a fresh database, and its schema is compared to `sql.py`.

A migration nobody runs is not a migration. These tests run one.

This suite is deliberately *not* parameterised over backends, unlike
`test_store_conformance.py`. Alembic is SQL-only - contracts section 8 gives
Mongo collections and indexes, not DDL - so there is nothing here for a
document store to conform to. It runs against SQLite, which is the dialect M3
ships; the same assertions hold against Postgres at M7 by pointing
``AGENTPROPS_DB_URL`` at one.

The comparison is Alembic's own autogenerate diff, which is exactly what
``alembic check`` runs. Reflecting the database and eyeballing column names
would prove less: autogenerate compares types, nullability, primary keys,
foreign keys and indexes, and it is the same machinery that will tell M7
whether a Postgres database matches.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from agentprops.models import Blueprint, Dataset, DatasetQuery
from agentprops.storage import METADATA, SqlStore, create_engine_for, sqlite_url
from agentprops.storage.sql import include_object
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


@pytest.fixture
def migrated(tmp_path: Path) -> str:
    """A fresh database with ``alembic upgrade head`` applied. Returns its URL."""
    url = sqlite_url(tmp_path / "migrated.db")
    command.upgrade(alembic_config(url), "head")
    return url


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
    """
    engine = create_engine_for(migrated)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"include_object": include_object, "compare_type": True},
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
        assert {index["name"] for index in inspector.get_indexes("datasets")} == {
            "datasets_lookup",
            "datasets_author",
        }
        assert {index["name"] for index in inspector.get_indexes("runs")} == {"runs_lookup"}
    finally:
        engine.dispose()


def test_the_postgres_only_indexes_are_absent_on_sqlite(migrated: str) -> None:
    """Contracts section 7: ``datasets_labels_gin`` and ``datasets_search`` have
    no SQLite equivalent, and SQLite falls back to a scan.

    The fallback is what ``find_datasets`` implements, and the conformance suite
    asserts its results - so the absence of the indexes is a documented property
    of this dialect rather than a gap.
    """
    engine = create_engine_for(migrated)
    try:
        names = {index["name"] for index in inspect(engine).get_indexes("datasets")}
    finally:
        engine.dispose()
    assert names.isdisjoint({"datasets_labels_gin", "datasets_search"})


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
