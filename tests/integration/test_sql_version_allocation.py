"""The two branches of dataset version allocation that a race would exercise.

`test_store_conformance.py` proves versions come out ``1, 2, 3`` and stay
independently readable. It cannot prove *why* that holds under concurrency,
because it must run identically against three backends and the mechanism is
SQL's: ``PRIMARY KEY (id, version)`` is the guard, not a lock.

So this module is deliberately white-box and SQL-only, like
`test_migrations.py`. It forces the two paths that only a second writer would
otherwise reach:

- a lost race, which must re-allocate and still be monotonic;
- an ``IntegrityError`` that is *not* a lost race, which must surface
  immediately rather than be retried eight times and reported as a version
  allocation failure.

Untested retry logic is where a store quietly corrupts something, and the whole
argument for reading ``max(version)`` outside the insert rests on the loser of
the race behaving correctly.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from agentprops.models import Blueprint, Dataset
from agentprops.storage import SqlStore, create_schema, sqlite_url

pytestmark = pytest.mark.integration


@pytest.fixture
def sql_store(tmp_path: Path) -> Iterator[SqlStore]:
    """A SQLite store, typed as the adapter rather than as the Protocol.

    The Protocol is what the conformance suite is written against; these two
    tests are about the adapter's internals, and saying so in the type is more
    honest than reaching through a ``Store``.
    """
    store = SqlStore.from_url(sqlite_url(tmp_path / "races.db"))
    create_schema(store.engine)
    try:
        yield store
    finally:
        store.dispose()


def test_a_lost_version_race_reallocates_and_stays_monotonic(
    sql_store: SqlStore,
    blueprint: Blueprint,
    dataset: Dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loser of the race takes the next version, not a duplicate.

    Simulated by making the first version read return a stale answer, which is
    exactly what a second writer sees when both read ``max(version)`` before
    either inserts. The primary key rejects the duplicate, the insert is
    retried against a fresh read, and the result is ``2``.
    """
    sql_store.put_blueprint(blueprint, publish=True)
    assert sql_store.put_dataset(dataset).version == 1

    honest = SqlStore._next_dataset_version
    reads: list[int] = []

    def stale(self: SqlStore, dataset_id: UUID) -> int:
        reads.append(len(reads))
        if len(reads) == 1:
            return 1  # the version that already exists: the race, lost
        return int(honest(self, dataset_id))

    monkeypatch.setattr(SqlStore, "_next_dataset_version", stale)

    second = sql_store.put_dataset(dataset)
    assert second.version == 2
    assert len(reads) == 2, "the losing insert did not retry"
    assert sql_store.get_dataset(str(dataset.id), 1) is not None
    assert sql_store.health().counts.datasets == 2


def test_an_unrelated_integrity_error_is_not_retried(sql_store: SqlStore, dataset: Dataset) -> None:
    """A missing blueprint surfaces as itself, immediately.

    Writing a dataset whose ``{agent_id, bp_version}`` names no blueprint trips
    ``datasets_blueprint_fkey``. If the retry loop caught every
    ``IntegrityError`` it would try eight times and report "could not allocate
    a version", burying the real cause one milestone away from the fix.

    This also happens to be the assertion that SQLite is enforcing the DDL's
    foreign keys at all - it only does so because the engine factory turns
    ``PRAGMA foreign_keys`` on per connection.
    """
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        sql_store.put_dataset(dataset)
    assert sql_store.health().counts.datasets == 0
