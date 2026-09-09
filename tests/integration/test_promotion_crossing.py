"""Acceptance clause 3: a dataset exported from SQLite imports into Postgres and validates.

"A dataset exported from a SQLite store imports into a Postgres store and
validates." The crossing is the test, so the two stores here are real and
different: a file-backed SQLite database in ``tmp_path`` and the session's
container-backed server.

Parameterised over **both** shared backends rather than only the one the clause
names. The clause names Postgres because that is what `docker-compose.yml`'s
``shared`` profile runs, but the promotion path has no idea which backend it is
writing to and the whole point of the milestone is that this is true - so
asserting it for one and not the other would leave the interesting half
untested. Each destination skips with a reason when its server is unreachable.

What "and validates" means, precisely
-------------------------------------

Not "and is accepted". ``dataset_import`` re-runs the **whole** ``BP-*`` and
``DS-*`` catalogue against the receiving store, and two of those rules -
DS-001 and DS-031 - are existence checks that only the receiving store can
answer. So the assertions here are about three things:

1. the import succeeds, and the datasets are readable on the far side;
2. the stored documents are **byte-identical** to the ones that left, which is
   what ruling R-08's ``exclude_unset`` round-trip criterion was for;
3. ``dataset_find`` returns the same rows in the same order on both sides,
   which is what ruling R-09 was for - ``created_at`` comes from
   ``provenance.created_at``, so it survives the crossing and the total
   ``(created_at, id)`` ordering survives with it.

The third is the one that would have been quietly wrong with a
``DEFAULT now()`` column, and it is the reason that column has no default.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo import MongoClient
from sqlalchemy import Engine

from agentprops.models import Blueprint, Dataset, DatasetQuery
from agentprops.service import FrozenClock, ServiceContext, promotion, sqlite_context
from agentprops.service.promotion import BUNDLE_FORMAT, BUNDLE_FORMAT_VERSION
from agentprops.storage import METADATA, MongoStore, SqlStore, Store
from integration.conftest import MONGO_TEST_DATABASE

pytestmark = pytest.mark.integration

AGENT = "location-onboarding"

#: The two backends `docker-compose.yml` calls "shared" or "local" - which is
#: to say, everything that is not the SQLite file the export came from.
DESTINATIONS = ("postgres", "mongo")


@pytest.fixture
def local(tmp_path: Any) -> Iterator[ServiceContext]:
    """The developer's machine: a SQLite file, with the two golden datasets in it.

    Written through the store rather than through ``dataset_submit``, because
    what is being tested is the crossing rather than the authoring flow, and the
    golden fixtures are already the output of that flow.
    """
    context = sqlite_context(
        tmp_path / "local.db", clock=FrozenClock(datetime(2026, 9, 8, 12, 0, tzinfo=UTC))
    )
    from conftest import load_document

    context.store.put_blueprint(
        Blueprint.model_validate(load_document("blueprints/location-onboarding-1.0.0.json")),
        publish=True,
    )
    for relative in ("datasets/priya-missing-docs.json", "datasets/arun-escalated.json"):
        context.store.put_dataset(Dataset.model_validate(load_document(relative)))
    try:
        yield context
    finally:
        store = context.store
        if isinstance(store, SqlStore):
            store.dispose()


@pytest.fixture(params=DESTINATIONS, ids=lambda name: str(name))
def shared(request: pytest.FixtureRequest) -> ServiceContext:
    """An **empty** store on one of the two shared backends, as a context.

    Named ``shared`` rather than ``store`` deliberately: the ``store`` fixture is
    parameterised over ``--store`` by `conftest.py`, and this suite wants both
    destinations on every run rather than the one that was selected.
    """
    clock = FrozenClock(datetime(2026, 9, 8, 12, 0, tzinfo=UTC))
    return ServiceContext(store=_empty(request, str(request.param)), clock=clock)


def _empty(request: pytest.FixtureRequest, backend: str) -> Store:
    if backend == "postgres":
        engine: Engine = request.getfixturevalue("postgres_engine")
        METADATA.drop_all(engine)
        METADATA.create_all(engine)
        return SqlStore(engine)
    client: MongoClient[dict[str, Any]] = request.getfixturevalue("mongo_client")
    client.drop_database(MONGO_TEST_DATABASE)
    adapter = MongoStore(client, MONGO_TEST_DATABASE)
    adapter.create_schema()
    return adapter


def _bundle(context: ServiceContext) -> dict[str, Any]:
    reply = promotion.export_bundle(context, AGENT, None)
    assert reply.ok, reply
    bundle: dict[str, Any] = reply.model_dump(mode="json")["data"]["bundle"]
    return bundle


def test_a_sqlite_export_imports_into_a_shared_store_and_validates(
    local: ServiceContext, shared: ServiceContext
) -> None:
    """The clause, end to end.

    The import envelope names what it wrote, so a bundle that arrived and stored
    nothing cannot pass: both datasets have to come back with a version.
    """
    bundle = _bundle(local)
    assert bundle["format"] == BUNDLE_FORMAT
    assert bundle["format_version"] == BUNDLE_FORMAT_VERSION
    assert len(bundle["blueprints"]) == 1
    assert len(bundle["datasets"]) == 2

    reply = promotion.import_bundle(shared, bundle)
    assert reply.ok, reply
    imported = reply.model_dump(mode="json")["data"]["imported"]
    assert imported["blueprints"] == [{"agent_id": AGENT, "version": "1.0.0"}]
    assert [row["version"] for row in imported["datasets"]] == [1, 1]


def test_the_documents_survive_the_crossing_byte_for_byte(
    local: ServiceContext, shared: ServiceContext
) -> None:
    """Ruling R-08's round-trip criterion, across two different backends.

    The golden fixtures omit many optional fields - ``tool_name`` on three
    nodes, ``max_iterations`` on eight, ``input`` on every pool entry - and the
    documents are written with ``exclude_unset=True`` on both sides so those
    stay absent rather than arriving as ``null``. A single reintroduced ``null``
    anywhere in either adapter fails this.
    """
    bundle = _bundle(local)
    assert promotion.import_bundle(shared, bundle).ok

    for document in bundle["datasets"]:
        arrived = shared.store.get_dataset(document["id"], None)
        assert arrived is not None, document["id"]
        assert arrived.model_dump(mode="json", exclude_unset=True) == document


def test_dataset_find_returns_the_same_rows_in_the_same_order(
    local: ServiceContext, shared: ServiceContext
) -> None:
    """Ruling R-09's actual purpose, tested across the crossing it was written for.

    ``datasets.created_at`` has no ``DEFAULT now()`` because it is populated
    from ``provenance.created_at`` - authored content - so the total
    ``(created_at, id)`` ordering is reproducible on the far side. A defaulted
    column would be re-stamped on import and this ordering would be whatever
    the import happened to write first.

    Compared as whole summaries rather than as ids, so a lost ``author``, a
    truncated ``narrative_excerpt`` or a shifted ``created_at`` fails here too.
    """
    bundle = _bundle(local)
    assert promotion.import_bundle(shared, bundle).ok

    before = local.store.find_datasets(DatasetQuery(agent_id=AGENT))
    after = shared.store.find_datasets(DatasetQuery(agent_id=AGENT))
    assert [row.id for row in before] == [row.id for row in after]
    assert before == after


def test_a_re_export_from_the_shared_store_is_the_same_bundle(
    local: ServiceContext, shared: ServiceContext
) -> None:
    """Export, import, export - and the bytes come back the same.

    The strongest statement of the whole promotion path, and it holds only if
    every part of it is stable: the document dump, the dataset ordering
    (``created_at``, ``id``), the blueprint ordering (semver), and the bundle
    key order. Any one of them varying per backend shows up here as a diff.

    It also pins the one thing that *could* legitimately have differed and does
    not: the version numbers. ``put_dataset`` allocates them, so an import into
    an empty store re-derives ``version: 1`` - the same value the export
    carried.
    """
    bundle = _bundle(local)
    assert promotion.import_bundle(shared, bundle).ok
    assert _bundle(shared) == bundle


def test_a_second_import_of_the_same_bundle_is_accepted(
    local: ServiceContext, shared: ServiceContext
) -> None:
    """Re-importing is safe, and the two halves are safe for different reasons.

    The blueprint is a byte-identical re-publish, which ruling R-29 makes a
    no-op success rather than a BP-016 failure - without that, a second import
    would be refused and a partially-completed promotion could never be
    retried. The dataset is copy-on-write, so it becomes version 2 of the same
    lineage rather than overwriting version 1, and ``dataset_find`` still
    returns one row per lineage.
    """
    bundle = _bundle(local)
    assert promotion.import_bundle(shared, bundle).ok
    again = promotion.import_bundle(shared, bundle)
    assert again.ok, again
    assert [
        row["version"] for row in again.model_dump(mode="json")["data"]["imported"]["datasets"]
    ] == [2, 2]
    assert len(shared.store.find_datasets(DatasetQuery(agent_id=AGENT))) == 2


def test_a_dataset_whose_supersedes_stayed_behind_is_refused(
    local: ServiceContext, shared: ServiceContext
) -> None:
    """The rule only the receiving store can answer, and the reason import validates.

    DS-031 checks that ``provenance.supersedes`` names a dataset that exists.
    In the store the bundle left, it did. Here it does not - and a document that
    was valid where it came from is not necessarily valid where it arrives,
    which is the whole argument for re-running validation rather than trusting
    the bundle.

    The finding's pointer names **which** dataset in the bundle, which is what
    the ``/datasets/1`` prefix is for: a caller with a five-dataset bundle
    should not have to guess.
    """
    bundle = _bundle(local)
    bundle["datasets"][1]["provenance"]["supersedes"] = "3f8c1a20-0000-4000-8000-0000000000ff"

    reply = promotion.import_bundle(shared, bundle)
    payload = reply.model_dump(mode="json")
    assert payload["ok"] is False
    assert [finding["rule"] for finding in payload["errors"]] == ["DS-031"]
    assert payload["errors"][0]["pointer"] == "/datasets/1/provenance/supersedes"
    assert shared.store.find_datasets(DatasetQuery()) == [], "a refused import wrote a dataset"


def test_a_refused_import_publishes_no_blueprint(
    local: ServiceContext, shared: ServiceContext
) -> None:
    """ "Nothing is written until all of it passes", asserted on the half that lasts.

    A published blueprint version is immutable under BP-016, so a failed import
    that published one has permanently changed the store it was rejecting. That
    is what ``_BundleResolver`` exists to prevent - it answers DS-001 from the
    bundle so the datasets can be validated *before* the blueprints are written
    - and this is the test that says so. Without it the obvious implementation
    passes every other test in this file.
    """
    bundle = _bundle(local)
    bundle["datasets"][0]["provenance"]["title"] = "   "

    reply = promotion.import_bundle(shared, bundle)
    payload = reply.model_dump(mode="json")
    assert payload["ok"] is False
    assert "DS-025" in {finding["rule"] for finding in payload["errors"]}
    assert shared.store.get_blueprint(AGENT, "1.0.0") is None, (
        "a refused import published a blueprint version, which BP-016 makes permanent"
    )
