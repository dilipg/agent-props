"""The losing side of every Mongo allocation race, forced.

The mirror of `test_sql_allocation_races.py`, which is deliberately SQL-only and
says so: it monkeypatches `SqlStore` internals and the mechanism it forces is a
SQL unique constraint. Which left the same branches in `mongo.py` **never
executed** - the ones that decide whether a real fault gets buried under eight
retries, and the ones that decide whether a losing writer receives the winner's
value.

Those branches are the whole reason the retry pattern is portable. Rulings R-37
and R-39 both turn on "the guard is a unique key on every backend"; the guard
being a *different exception class* on this one is exactly the kind of thing
that looks handled and is not. So the same technique, against the same
Protocol, with `DuplicateKeyError` where the SQL file has `IntegrityError`.

**How the races are forced, stated plainly** - the same sentence M3 recorded, for
the same reason. One internal read is monkeypatched to return a stale answer
once, which is exactly what the loser of a real race sees. Nothing else is
mocked: the `insert_one` that follows, the unique index that rejects it, the
`DuplicateKeyError` and the retry all run against a real MongoDB. What that
proves is that the *recovery* is correct given a stale read, not that a stale
read is reachable - and reachability is a property of the pattern rather than of
this backend, established at M3 by replaying two interleaved connections.

The suite skips with a reason when no Mongo is reachable, like every other
container-backed suite here.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from agentprops.models import Blueprint, Dataset, StepRecord
from agentprops.storage import MongoStore, StoreError
from integration.conftest import MONGO_TEST_DATABASE, make_run

pytestmark = pytest.mark.integration


@pytest.fixture
def mongo_store(request: pytest.FixtureRequest) -> Iterator[MongoStore]:
    """An empty Mongo store, typed as the adapter rather than as the Protocol.

    The Protocol is what the conformance suite is written against; these tests
    are about the adapter's internals, and saying so in the type is more honest
    than reaching through a ``Store``.
    """
    client: MongoClient[dict[str, Any]] = request.getfixturevalue("mongo_client")
    client.drop_database(MONGO_TEST_DATABASE)
    store = MongoStore(client, MONGO_TEST_DATABASE)
    store.create_schema()
    yield store


def test_a_lost_version_race_reallocates_and_stays_monotonic(
    mongo_store: MongoStore,
    blueprint: Blueprint,
    dataset: Dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loser of the race takes the next version, not a duplicate.

    Simulated by making the first version read return a stale answer, which is
    exactly what a second writer sees when both read the maximum before either
    inserts. The unique ``_id`` of ``{id, version}`` rejects the duplicate, the
    insert is retried against a fresh read, and the result is ``2``.

    The mechanism is not a transaction and never was: a read outside the write
    takes no lock on any of the three backends (ruling R-37), so the *key* is
    the guard and the retry is what makes it usable.
    """
    mongo_store.put_blueprint(blueprint, publish=True)
    assert mongo_store.put_dataset(dataset).version == 1

    honest = MongoStore._next_dataset_version
    reads: list[int] = []

    def stale(self: MongoStore, dataset_id: uuid.UUID) -> int:
        reads.append(len(reads))
        if len(reads) == 1:
            return 1  # the version that already exists: the race, lost
        return int(honest(self, dataset_id))

    monkeypatch.setattr(MongoStore, "_next_dataset_version", stale)

    second = mongo_store.put_dataset(dataset)
    assert second.version == 2
    assert len(reads) == 2, "the losing insert did not retry"
    assert mongo_store.get_dataset(str(dataset.id), 1) is not None
    assert mongo_store.health().counts.datasets == 2


def test_a_duplicate_that_is_not_the_version_race_surfaces_immediately(
    mongo_store: MongoStore,
    blueprint: Blueprint,
    dataset: Dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``continue``-versus-``raise`` discrimination, which is the branch that matters.

    ``put_dataset`` retries a :class:`DuplicateKeyError` **only** when the
    version it tried now exists - that is the race. Anything else is a real
    fault and is re-raised at once, so it is not buried under eight attempts and
    reported as an allocation failure at the end.

    Forced by making the write itself raise a ``DuplicateKeyError`` that has
    nothing to do with the version, while the version read stays honest: the
    row it names does not exist, so ``_dataset_version_exists`` says no and the
    error must come straight back. Asserted as **not** a ``StoreError``,
    because ``StoreError`` is the message the retry budget produces when it
    gives up, and receiving that for a foreign-key-shaped fault is precisely the
    burial this branch prevents.
    """
    mongo_store.put_blueprint(blueprint, publish=True)

    def unrelated(*_: Any, **__: Any) -> None:
        raise DuplicateKeyError("E11000 duplicate key error: some other unique index")

    monkeypatch.setattr(type(mongo_store._datasets), "insert_one", unrelated)

    with pytest.raises(DuplicateKeyError):
        mongo_store.put_dataset(dataset)
    assert mongo_store.health().counts.datasets == 0


def test_a_lost_seq_race_reallocates_and_stays_monotonic(
    mongo_store: MongoStore,
    blueprint: Blueprint,
    dataset: Dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ruling R-37's constraint, on the backend where it is an index rather than a table.

    The unique ``{run_id, seq}`` index is what makes ``max(seq) + 1`` sound. A
    stale allocation read makes the second step attempt ``seq = 1``, the index
    rejects it, and the retry takes ``2``.
    """
    mongo_store.put_blueprint(blueprint, publish=True)
    pinned = mongo_store.put_dataset(dataset)
    run = mongo_store.put_run(make_run("mongo-seq-race", pinned))
    first = mongo_store.upsert_step(
        run.id, StepRecord(node_id="receive_request", iteration=0, served={})
    )
    assert first.seq == 1

    honest = MongoStore._next_step_seq
    reads: list[int] = []

    def stale(self: MongoStore, run_id: str) -> int:
        reads.append(len(reads))
        if len(reads) == 1:
            return 1  # the seq that already exists: the race, lost
        return int(honest(self, run_id))

    monkeypatch.setattr(MongoStore, "_next_step_seq", stale)

    second = mongo_store.upsert_step(
        run.id, StepRecord(node_id="check_docs", iteration=0, served={})
    )
    assert second.seq == 2
    assert len(reads) == 2, "the losing insert did not retry"

    stored = mongo_store.get_run(run.id)
    assert stored is not None
    assert [step.seq for step in stored.steps] == [1, 2]


def test_a_step_key_that_appears_mid_race_becomes_the_no_op(
    mongo_store: MongoStore,
    blueprint: Blueprint,
    dataset: Dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other retry outcome: another writer served this same step first.

    ``upsert_step``'s repeat must be a literal no-op returning the existing
    record (M6's gate: "fetching the same step key twice returns byte-identical
    fixtures and advances nothing"), so a writer whose existence check came back
    blind must converge on *that* rather than overwrite. Forced by blinding the
    step read once, which is what the second of two concurrent writers of one key
    sees.
    """
    mongo_store.put_blueprint(blueprint, publish=True)
    pinned = mongo_store.put_dataset(dataset)
    run = mongo_store.put_run(make_run("mongo-step-race", pinned))
    served = mongo_store.upsert_step(
        run.id, StepRecord(node_id="check_docs", iteration=0, served={"docs": ["fssai"]})
    )

    honest = MongoStore._step_doc
    calls: list[int] = []

    def blind(self: MongoStore, run_id: str, node_id: str, iteration: int) -> Any:
        calls.append(len(calls))
        if len(calls) == 1:
            return None  # the row is there; this writer cannot see it yet
        return honest(self, run_id, node_id, iteration)

    monkeypatch.setattr(MongoStore, "_step_doc", blind)

    again = mongo_store.upsert_step(
        run.id, StepRecord(node_id="check_docs", iteration=0, served={"docs": ["OVERWRITTEN"]})
    )
    assert again == served, "the losing writer did not converge on the existing record"
    assert again.served == {"docs": ["fssai"]}

    stored = mongo_store.get_run(run.id)
    assert stored is not None and len(stored.steps) == 1


def test_a_lost_set_step_actual_race_raises_rather_than_returning_the_winners_value(
    mongo_store: MongoStore,
    blueprint: Blueprint,
    dataset: Dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect M3's fix round 1 shipped on the SQL side, checked on this one.

    ``set_step_actual``'s ``UPDATE`` is conditional on ``actual`` being null, so
    a writer that lost the race changes nothing - and it then **loops back**
    rather than reading the record and returning it, because the record it would
    read holds the winner's value. A step's recorded actual is evidence, and
    evidence that silently belongs to a different writer is worse than an error.

    Forced by making the first read report no actual when one is already there,
    which is what the loser of a genuine race sees. There is exactly one
    decision site, at the top of the loop, and this is the branch of it that
    raises.
    """
    mongo_store.put_blueprint(blueprint, publish=True)
    pinned = mongo_store.put_dataset(dataset)
    run = mongo_store.put_run(make_run("mongo-actual-race", pinned))
    mongo_store.upsert_step(run.id, StepRecord(node_id="check_docs", iteration=0, served={}))
    winner = mongo_store.set_step_actual(run.id, "check_docs", 0, {"called": True})

    honest = MongoStore._step_doc
    calls: list[int] = []

    def stale(self: MongoStore, run_id: str, node_id: str, iteration: int) -> Any:
        calls.append(len(calls))
        found = honest(self, run_id, node_id, iteration)
        if len(calls) == 1 and found is not None:
            return {**found, "actual": None}  # the winner's write is not visible yet
        return found

    monkeypatch.setattr(MongoStore, "_step_doc", stale)

    with pytest.raises(StoreError, match="already recorded a different actual"):
        mongo_store.set_step_actual(run.id, "check_docs", 0, {"called": False})

    stored = mongo_store.get_run(run.id)
    assert stored is not None
    assert stored.steps[0].actual == {"called": True}, "the winner's evidence was overwritten"
    assert stored.steps[0] == winner


def test_two_concurrent_first_writes_of_one_blueprint_converge(
    mongo_store: MongoStore, blueprint: Blueprint, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-29's no-op success, reached the way a CI pipeline reaches it.

    Two identical publishes race; the loser's ``insert_one`` hits the unique
    ``_id`` and it must retry into the "record exists" branch, where a
    byte-identical re-publish is a success returning the stored blueprint.
    Without the retry it surfaces a raw driver error instead - which is not a
    thing a caller publishing on every build should have to handle.
    """
    mongo_store.put_blueprint(blueprint, publish=True)

    honest = MongoStore._blueprint_doc
    calls: list[int] = []

    def blind(self: MongoStore, agent_id: str, version: str) -> Any:
        calls.append(len(calls))
        if len(calls) == 1:
            return None  # the record is there; this writer cannot see it yet
        return honest(self, agent_id, version)

    monkeypatch.setattr(MongoStore, "_blueprint_doc", blind)

    stored = mongo_store.put_blueprint(blueprint, publish=True)
    assert stored == blueprint
    assert len(calls) == 2, "the losing insert did not retry"
    assert mongo_store.health().counts.blueprints == 1
