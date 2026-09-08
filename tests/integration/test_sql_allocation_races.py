"""Every allocation race in `sql.py`, forced, including the losing side of each.

`test_store_conformance.py` proves dataset versions come out ``1, 2, 3``, that
``seq`` counts up per run, and that a second writer of one key converges. It
cannot prove *why* any of that holds under concurrency: it must run identically
against three backends, and every one of these mechanisms is SQL's - a unique
constraint rejecting a duplicate, and a retry that re-reads.

So this module is deliberately white-box and SQL-only, like `test_migrations.py`.
It forces the paths only a second writer would otherwise reach:

- **dataset versions** - a lost race must re-allocate and stay monotonic, and an
  ``IntegrityError`` that is *not* a lost race must surface immediately rather
  than be retried eight times and reported as an allocation failure;
- **``run_steps.seq``** - the same two, added in M3's first fix round after the
  review *reproduced* two connections committing ``seq = 1``. The transaction
  around the read and the insert was never a lock; ``UNIQUE (run_id, seq)``
  (ruling R-37) is what makes the retry sound;
- **two concurrent first writes of one key** - ``put_blueprint``,
  ``put_skeleton`` and ``put_run`` must converge on their "row exists" branch
  rather than surfacing a raw driver error, and for a blueprint that
  convergence is R-29's no-op success, which is the case R-29 exists for;
- **``set_step_actual``** - a writer that lost the conditional ``UPDATE`` must
  *raise*, not return the winner's value. Added in fix round 2, because that is
  exactly what M3's fix round 1 got wrong: it checked that some actual was now
  stored rather than that it was this caller's.

Untested retry logic is where a store quietly corrupts something. The reviewer's
standard - test the *losing* side - is the standard here, and every allocation
and first-write race in `sql.py` now has a test of it.

**How the races are forced, stated plainly.** One internal read is monkeypatched
to return a stale answer once - the allocation read, the existence check, or the
step row - which is exactly what the loser of a real race sees. Nothing else is
mocked: the ``INSERT`` or ``UPDATE`` that follows, the constraint that rejects
it, the ``IntegrityError`` and the retry all run against a real file-backed
SQLite database. These tests do not use threads or a second connection, so what
they prove is that the *recovery* is correct given a stale read, not that a
stale read is reachable - and that it is reachable was established separately by
replaying the statement sequence on two interleaved connections, which is what
ruling R-37 records.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from agentprops.models import Blueprint, Dataset, Skeleton, StepRecord
from agentprops.storage import SqlStore, StoreError, create_schema, sqlite_url
from integration.conftest import make_run

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


def test_a_lost_seq_race_reallocates_and_stays_monotonic(
    sql_store: SqlStore,
    blueprint: Blueprint,
    dataset: Dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ruling R-37, the branch the first implementation did not have.

    The review replayed ``upsert_step``'s statement sequence on two interleaved
    connections against one file-backed SQLite database and **both committed
    ``seq = 1``**: pysqlite defers ``BEGIN`` until the first DML, so neither the
    existence check nor the ``max(seq)`` read takes a lock, and Postgres under
    READ COMMITTED behaves the same. Simulated here the same way the dataset
    version race is - by making the first allocation read return a stale answer.

    ``UNIQUE (run_id, seq)`` rejects the duplicate; the retry re-reads and takes
    2. Without the constraint this test would produce two rows at ``seq = 1``
    and ``get_run``'s ordering would be non-deterministic from then on.
    """
    sql_store.put_blueprint(blueprint, publish=True)
    stored = sql_store.put_dataset(dataset)
    run = sql_store.put_run(make_run("run-seq-race", stored))
    first = sql_store.upsert_step(
        run.id, StepRecord(node_id="receive_request", iteration=0, served={})
    )
    assert first.seq == 1

    honest = SqlStore._next_step_seq
    reads: list[int] = []

    def stale(self: SqlStore, run_id: str) -> int:
        reads.append(len(reads))
        if len(reads) == 1:
            return 1  # the seq that already exists: the race, lost
        return int(honest(self, run_id))

    monkeypatch.setattr(SqlStore, "_next_step_seq", stale)

    second = sql_store.upsert_step(run.id, StepRecord(node_id="check_docs", iteration=0, served={}))
    assert second.seq == 2
    assert len(reads) == 2, "the losing insert did not retry"

    read_back = sql_store.get_run(run.id)
    assert read_back is not None
    assert [step.seq for step in read_back.steps] == [1, 2]


def test_a_step_whose_key_appears_mid_race_becomes_the_no_op(
    sql_store: SqlStore,
    blueprint: Blueprint,
    dataset: Dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other ``IntegrityError`` on this path: the primary key, not the seq.

    Two writers serving the *same* step key: the loser must return the winner's
    stored row - ``upsert_step``'s documented no-op - rather than retrying until
    it gives up. Simulated by hiding the existing row from the first existence
    check, which is exactly what the loser sees.
    """
    sql_store.put_blueprint(blueprint, publish=True)
    stored = sql_store.put_dataset(dataset)
    run = sql_store.put_run(make_run("run-step-key-race", stored))
    winner = sql_store.upsert_step(
        run.id, StepRecord(node_id="receive_request", iteration=0, served={"by": "winner"})
    )

    honest = SqlStore._step_row
    hidden: list[int] = []

    def blind(self: SqlStore, run_id: str, node_id: str, iteration: int) -> Any:
        hidden.append(len(hidden))
        if len(hidden) == 1:
            return None  # the winner's row, not yet visible to the loser
        return honest(self, run_id, node_id, iteration)

    monkeypatch.setattr(SqlStore, "_step_row", blind)

    loser = sql_store.upsert_step(
        run.id, StepRecord(node_id="receive_request", iteration=0, served={"by": "loser"})
    )
    assert loser == winner
    assert loser.served == {"by": "winner"}
    read_back = sql_store.get_run(run.id)
    assert read_back is not None and len(read_back.steps) == 1


def test_a_step_on_a_missing_run_is_not_retried(sql_store: SqlStore) -> None:
    """A foreign-key violation surfaces as itself, as it does for datasets.

    ``run_steps.run_id`` references ``runs.id``, so a step for a run that was
    never started is an error about a missing run - not eight failed attempts
    reported as "could not allocate a seq".
    """
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        sql_store.upsert_step(
            "no-such-run", StepRecord(node_id="check_docs", iteration=0, served={})
        )


def test_two_concurrent_first_publishes_converge_on_a_no_op(
    sql_store: SqlStore, blueprint: Blueprint, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-29's own motivating case, under a race.

    "A CI pipeline that publishes on every run is the normal case, not an
    abuse" - and two runners publishing the identical blueprint at the same
    instant both take the insert branch, because neither can see the other's
    row yet. One loses on the primary key. Before the fix round that surfaced as
    a raw ``IntegrityError``; now the loser retries once, finds the row, and
    lands on the no-op success R-29 defines.

    Simulated by hiding the existing row from the first select, which is what
    the loser sees.
    """
    winner = sql_store.put_blueprint(blueprint, publish=True)

    honest = SqlStore._blueprint_row
    hidden: list[int] = []

    def blind(self: SqlStore, conn: Any, agent_id: str, version: str) -> Any:
        hidden.append(len(hidden))
        if len(hidden) == 1:
            return None
        return honest(self, conn, agent_id, version)

    monkeypatch.setattr(SqlStore, "_blueprint_row", blind)

    assert sql_store.put_blueprint(blueprint, publish=True) == winner
    assert len(hidden) == 2, "the losing insert did not retry"
    assert sql_store.health().counts.blueprints == 1


def test_a_run_pinned_to_a_missing_dataset_is_not_retried_into_silence(
    sql_store: SqlStore, blueprint: Blueprint, dataset: Dataset
) -> None:
    """``put_run``'s retry must not swallow ``runs_dataset_fkey``.

    The retry exists for two concurrent first writes of one run id. A run whose
    pin names a dataset version that does not exist fails the same way on the
    second attempt, and the ``IntegrityError`` is re-raised naming the
    constraint it broke rather than being translated into something vaguer.
    """
    sql_store.put_blueprint(blueprint, publish=True)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        sql_store.put_run(make_run("run-unpinned", dataset))
    assert sql_store.health().counts.runs == 0


def test_a_losing_set_step_actual_refuses_the_winners_value(
    sql_store: SqlStore,
    blueprint: Blueprint,
    dataset: Dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The losing writer must raise, not inherit the winner's evidence.

    ``WHERE actual IS NULL`` makes the ``UPDATE`` conditional, so a writer that
    lost changes nothing - and the question is what it *returns*. M3's fix round
    1 read the row back and returned it whenever *some* actual was stored, so
    the loser got a successful ``StepRecord`` carrying the winner's value and no
    exception. A step's recorded actual is evidence; evidence that silently
    belongs to a different writer is worse than an error, and the write-once
    contract R-33 exists to guarantee is precisely this one.

    The fix loops back to the single decision site at the top, so both halves of
    that contract hold under a race: a differing actual raises, an identical one
    is still the no-op. Both are asserted here.
    """
    sql_store.put_blueprint(blueprint, publish=True)
    stored = sql_store.put_dataset(dataset)
    run = sql_store.put_run(make_run("run-actual-race", stored))
    sql_store.upsert_step(run.id, StepRecord(node_id="check_docs", iteration=0, served={}))

    # The row as a losing writer saw it: served, with no actual yet.
    stale = SqlStore._step_row(sql_store, run.id, "check_docs", 0)
    assert stale is not None and stale.actual is None

    winner = sql_store.set_step_actual(run.id, "check_docs", 0, {"result": "X-from-A"})
    assert winner.actual == {"result": "X-from-A"}

    honest = SqlStore._step_row
    armed = {"stale": False}
    reads: list[int] = []

    def blind(self: SqlStore, run_id: str, node_id: str, iteration: int) -> Any:
        reads.append(len(reads))
        if armed["stale"]:
            armed["stale"] = False
            return stale  # what the loser read before the winner committed
        return honest(self, run_id, node_id, iteration)

    monkeypatch.setattr(SqlStore, "_step_row", blind)

    armed["stale"] = True
    with pytest.raises(StoreError):
        sql_store.set_step_actual(run.id, "check_docs", 0, {"result": "Y-from-B"})
    assert len(reads) == 2, "the losing writer did not loop back to the decision site"

    armed["stale"] = True
    replayed = sql_store.set_step_actual(run.id, "check_docs", 0, {"result": "X-from-A"})
    assert replayed.actual == {"result": "X-from-A"}

    read_back = sql_store.get_run(run.id)
    assert read_back is not None
    assert read_back.steps[0].actual == {"result": "X-from-A"}, "the winner's value must survive"


def test_two_concurrent_first_skeleton_writes_converge(
    sql_store: SqlStore, skeleton: Skeleton, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loser re-enters the update branch, which is where it belonged.

    Same retry as ``put_blueprint``, with nothing to compare - a skeleton is an
    in-progress fill and re-filling a section is explicitly allowed (ruling
    R-06) - so convergence here means last-write-wins on one row rather than a
    raw driver error.
    """
    first = sql_store.put_skeleton(skeleton)
    assert first.parts == {}

    honest = SqlStore._existing_skeleton
    hidden: list[int] = []

    def blind(self: SqlStore, conn: Any, skeleton_id: Any) -> Any:
        hidden.append(len(hidden))
        if len(hidden) == 1:
            return None  # the other writer's row, not yet visible
        return honest(self, conn, skeleton_id)

    monkeypatch.setattr(SqlStore, "_existing_skeleton", blind)

    filled = sql_store.put_skeleton(
        skeleton.model_copy(update={"parts": {"provenance": {"title": "raced"}}})
    )
    assert filled.parts == {"provenance": {"title": "raced"}}
    assert len(hidden) == 2, "the losing insert did not retry"
    assert sql_store.get_skeleton(str(skeleton.id)) == filled


def test_two_concurrent_first_run_writes_converge(
    sql_store: SqlStore,
    blueprint: Blueprint,
    dataset: Dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One run row, not a primary-key error.

    The companion to ``test_a_run_pinned_to_a_missing_dataset_is_not_retried_into_silence``,
    which covers the other branch of the same ``except``: that one asserts an
    unrelated ``IntegrityError`` still surfaces, this one asserts the race the
    retry exists for actually converges.
    """
    sql_store.put_blueprint(blueprint, publish=True)
    stored = sql_store.put_dataset(dataset)
    sql_store.put_run(make_run("run-converge", stored))

    honest = SqlStore._existing_run
    hidden: list[int] = []

    def blind(self: SqlStore, conn: Any, run_id: str) -> Any:
        hidden.append(len(hidden))
        if len(hidden) == 1:
            return None
        return honest(self, conn, run_id)

    monkeypatch.setattr(SqlStore, "_existing_run", blind)

    raced = sql_store.put_run(make_run("run-converge", stored, status="finished"))
    assert raced.status == "finished"
    assert len(hidden) == 2, "the losing insert did not retry"
    assert sql_store.health().counts.runs == 1
    assert sql_store.get_run("run-converge") == raced
