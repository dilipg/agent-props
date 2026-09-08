"""The storage conformance suite: one suite, every backend, written against the Protocol.

`docs/build-handoff.md`'s testing strategy calls this the second of four layers
and says it exactly once: "One suite, parameterised over three backends. Written
once against the Protocol, never per-adapter." Nothing in this file names an
adapter, imports `sql.py`, or knows what dialect it is talking to. The ``store``
fixture in `conftest.py` decides that, from ``--store``.

M3's acceptance criterion is one test - :func:`test_the_acceptance_journey` -
because the criterion is one journey. Ruling R-05 adds the three clauses that
follow it: a skeleton round trip including ``mark_skeleton_submitted``, a run
plus two ``upsert_step`` calls on the same key, and ``health()`` with its
counts. The rest of the file is the parts of the Protocol those two do not
reach.

**Identical results, never identical query plans.** Contracts section 7 says so
of the label and ``q`` filters, whose SQLite implementation is a scan where
Postgres has a GIN index. Every assertion here is about returned rows.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from agentprops.models import (
    Blueprint,
    Dataset,
    DatasetQuery,
    ModelInfo,
    RunQuery,
    Skeleton,
    StepRecord,
    Warning,
)
from agentprops.storage import (
    PublishedVersionImmutableError,
    RecordNotFoundError,
    Store,
    StoreError,
)
from integration.conftest import FROZEN_NOW, make_run

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------


def test_the_adapter_satisfies_the_protocol(store: Store) -> None:
    """``isinstance`` sees the method names; ``mypy --strict`` sees the signatures.

    The static half lives in `storage/sql.py` as ``_protocol_conformance``, so
    an argument name drifting from contracts section 6 fails the type check
    rather than nothing at all.
    """
    assert isinstance(store, Store)


def test_the_acceptance_journey(store: Store, blueprint: Blueprint, dataset: Dataset) -> None:
    """M3's acceptance criterion, in the order the milestone states it.

    Writes a blueprint, publishes it, fails to mutate it, writes a dataset,
    edits it, reads back both versions distinctly, archives it, confirms it is
    hidden from find and still readable by id and version, and confirms no row
    was deleted.

    The last clause is checked two ways: here, by counting rows and re-reading
    the superseded version after the archive; and statically, in
    `tests/unit/test_storage_no_delete.py`, which reads the AST of every module
    in `storage/` and asserts none of them issues a ``DELETE``.
    """
    # writes a blueprint
    draft = store.put_blueprint(blueprint, publish=False)
    assert draft.status == "draft"

    # publishes it
    published = store.put_blueprint(blueprint, publish=True)
    assert published.status == "published"
    assert store.get_blueprint(blueprint.agent_id, None) == published

    # fails to mutate it
    with pytest.raises(PublishedVersionImmutableError):
        store.put_blueprint(blueprint.model_copy(update={"description": "reworded"}), publish=True)
    assert store.get_blueprint(blueprint.agent_id, blueprint.version) == published

    # writes a dataset
    first = store.put_dataset(dataset)
    assert first.version == 1

    # edits it
    second = store.put_dataset(dataset.model_copy(update={"narrative": "A different world."}))
    assert second.version == 2

    # reads back both versions distinctly
    stored_first = store.get_dataset(str(dataset.id), 1)
    stored_second = store.get_dataset(str(dataset.id), 2)
    assert stored_first == first
    assert stored_second == second
    assert stored_first is not None and stored_second is not None
    assert stored_first.narrative != stored_second.narrative

    # archives it
    summary = store.set_archived(str(dataset.id), True)
    assert summary.archived is True
    assert summary.version == 2

    # confirms it is hidden from find
    assert store.find_datasets(DatasetQuery()) == []

    # and still readable by id and by version
    latest = store.get_dataset(str(dataset.id), None)
    assert latest is not None and latest.version == 2 and latest.archived is True
    pinned = store.get_dataset(str(dataset.id), 1)
    assert pinned is not None and pinned.version == 1
    assert pinned.narrative == dataset.narrative

    # confirms no code path deleted a row
    assert store.health().counts.datasets == 2
    assert store.health().counts.blueprints == 1


# --------------------------------------------------------------------------
# blueprints
# --------------------------------------------------------------------------


def test_a_draft_is_overwritten_in_place(store: Store, blueprint: Blueprint) -> None:
    """Only ``published`` is immutable. BP-016 says nothing about a draft."""
    store.put_blueprint(blueprint, publish=False)
    reworded = store.put_blueprint(
        blueprint.model_copy(update={"description": "reworded"}), publish=False
    )
    assert reworded.description == "reworded"
    assert store.get_blueprint(blueprint.agent_id, blueprint.version) == reworded


def test_publishing_promotes_a_draft(store: Store, blueprint: Blueprint) -> None:
    store.put_blueprint(blueprint, publish=False)
    assert store.get_blueprint(blueprint.agent_id, None) is None
    published = store.put_blueprint(blueprint, publish=True)
    assert store.get_blueprint(blueprint.agent_id, None) == published


def test_an_identical_republish_is_a_no_op_success(store: Store, blueprint: Blueprint) -> None:
    """Ruling R-29. BP-016 fires only when the document *differs*.

    Contracts 3.1's literal wording rejects "any upsert against an existing
    published ``{agent_id, version}``", which would make ``dataset_import``
    non-idempotent and would fail a CI pipeline that publishes on every run.
    Immutability is fully preserved because nothing changes.
    """
    published = store.put_blueprint(blueprint, publish=True)
    assert store.put_blueprint(blueprint, publish=True) == published
    assert store.health().counts.blueprints == 1


def test_a_reordered_republish_is_not_a_difference(store: Store, blueprint: Blueprint) -> None:
    """Ruling R-29 compares canonically, so key order is not a difference.

    Belt and braces on this path: the model normalises key order on the way
    out, so a re-ordered submission can only *become* identical. The comparison
    is canonical anyway, because the day a document reaches storage without
    passing through the model is the day literal-byte comparison starts
    rejecting re-publishes for no reason.
    """
    published = store.put_blueprint(blueprint, publish=True)
    raw = blueprint.model_dump(mode="json", exclude_unset=True)
    reordered = Blueprint.model_validate(dict(reversed(list(raw.items()))))
    assert store.put_blueprint(reordered, publish=True) == published


def test_demoting_a_published_version_is_refused(store: Store, blueprint: Blueprint) -> None:
    """``publish=False`` over a published version is a mutation of it.

    The stored ``status`` is normalised from the ``publish`` flag, so the
    submitted document differs from the stored one in exactly that field - and
    BP-016 treats it like any other difference. Un-publishing a version that
    datasets may already reference is not a thing this store does quietly.
    """
    store.put_blueprint(blueprint, publish=True)
    with pytest.raises(PublishedVersionImmutableError):
        store.put_blueprint(blueprint, publish=False)
    stored = store.get_blueprint(blueprint.agent_id, blueprint.version)
    assert stored is not None and stored.status == "published"


def test_get_blueprint_by_version_ignores_status(store: Store, blueprint: Blueprint) -> None:
    """An explicit version reads a draft; an omitted one does not.

    ``blueprint_get`` documents "latest published when version omitted", and the
    authoring flow needs to read back the draft it is working on.
    """
    draft = store.put_blueprint(blueprint, publish=False)
    assert store.get_blueprint(blueprint.agent_id, blueprint.version) == draft
    assert store.get_blueprint(blueprint.agent_id, None) is None


def test_get_blueprint_picks_the_newest_published_by_semver(
    store: Store, blueprint: Blueprint
) -> None:
    """``1.10.0`` is newer than ``1.9.0``, which ``ORDER BY`` on TEXT gets wrong."""
    for version in ("1.9.0", "1.10.0", "1.2.0"):
        store.put_blueprint(blueprint.model_copy(update={"version": version}), publish=True)
    store.put_blueprint(blueprint.model_copy(update={"version": "2.0.0"}), publish=False)
    latest = store.get_blueprint(blueprint.agent_id, None)
    assert latest is not None and latest.version == "1.10.0"


def test_list_blueprints_filters_by_status(store: Store, blueprint: Blueprint) -> None:
    store.put_blueprint(blueprint, publish=True)
    store.put_blueprint(blueprint.model_copy(update={"version": "1.1.0"}), publish=False)
    assert [(row.version, row.status) for row in store.list_blueprints(None)] == [
        ("1.0.0", "published"),
        ("1.1.0", "draft"),
    ]
    assert [row.version for row in store.list_blueprints("draft")] == ["1.1.0"]
    assert [row.version for row in store.list_blueprints("published")] == ["1.0.0"]
    assert store.list_blueprints("nonsense") == []


def test_list_blueprints_carries_the_description(store: Store, blueprint: Blueprint) -> None:
    """Ruling R-05's shape: ``{agent_id, version, status, description}``."""
    store.put_blueprint(blueprint, publish=True)
    (row,) = store.list_blueprints(None)
    assert row.agent_id == blueprint.agent_id
    assert row.description == blueprint.description


def test_an_unknown_blueprint_reads_as_none(store: Store) -> None:
    assert store.get_blueprint("nobody", None) is None
    assert store.get_blueprint("nobody", "1.0.0") is None
    assert store.list_blueprints(None) == []


# --------------------------------------------------------------------------
# datasets: versioning and copy-on-write
# --------------------------------------------------------------------------


def test_the_store_allocates_the_version_and_ignores_the_models(
    store: Store, published: Blueprint, dataset: Dataset
) -> None:
    """Monotonic from 1, whatever the submitted document claims.

    A dataset arriving from ``dataset_import`` or from a hand-edited file can
    carry any ``version`` at all; the store is the only party that knows what
    is already there.
    """
    assert store.put_dataset(dataset.model_copy(update={"version": 97})).version == 1
    assert store.put_dataset(dataset.model_copy(update={"version": 97})).version == 2
    assert store.put_dataset(dataset).version == 3


def test_both_versions_stay_independently_readable(
    store: Store, published: Blueprint, dataset: Dataset
) -> None:
    """Copy-on-write: an edit adds a version, it does not change one.

    This is what makes a run's pin meaningful - it reads the version it started
    with for its whole life, however many times the dataset is edited after.
    """
    first = store.put_dataset(dataset)
    second = store.put_dataset(dataset.model_copy(update={"seed": 999}))
    assert store.get_dataset(str(dataset.id), 1) == first
    assert store.get_dataset(str(dataset.id), 2) == second
    assert first.seed == dataset.seed
    assert second.seed == 999


def test_get_dataset_defaults_to_the_latest_version(
    store: Store, published: Blueprint, dataset: Dataset
) -> None:
    store.put_dataset(dataset)
    third = store.put_dataset(store.put_dataset(dataset))
    assert store.get_dataset(str(dataset.id), None) == third
    assert third.version == 3


def test_an_unknown_version_reads_as_none(
    store: Store, published: Blueprint, dataset: Dataset
) -> None:
    store.put_dataset(dataset)
    assert store.get_dataset(str(dataset.id), 2) is None


def test_a_malformed_dataset_id_reads_as_none(store: Store) -> None:
    """A malformed id is a miss, not an exception.

    Ids arrive as tool inputs. ``RunQuery.dataset_id``'s docstring settles the
    principle for the whole read path: "an unparseable filter should return no
    rows, not raise".
    """
    assert store.get_dataset("not-a-uuid", None) is None
    assert store.get_dataset("", 1) is None


def test_the_stored_document_round_trips_byte_for_byte(
    store: Store,
    published: Blueprint,
    dataset: Dataset,
    dataset_document: dict[str, object],
) -> None:
    """Ruling R-08's criterion, applied to what storage keeps.

    The golden fixture omits many optional fields - ``max_iterations`` on eight
    nodes, ``input`` on every pool entry. The document column is written with
    ``exclude_unset=True`` so those stay absent rather than coming back as
    ``null``, which is what makes ``dataset_export`` byte-stable and M7's
    "exported from SQLite, imported into Postgres" gate possible.
    """
    store.put_dataset(dataset)
    stored = store.get_dataset(str(dataset.id), 1)
    assert stored is not None
    assert stored.model_dump(mode="json", exclude_unset=True) == dataset_document


def test_created_at_is_the_authored_timestamp(
    store: Store, published: Blueprint, dataset: Dataset
) -> None:
    """Ruling R-09: the column is populated from ``provenance.created_at``.

    Not ``now()``. One value, authored in the document, so the ordering
    ``find_datasets`` promises is reproducible across an export/import cycle -
    a ``DEFAULT now()`` column would be re-stamped on re-import.
    """
    store.put_dataset(dataset)
    (summary,) = store.find_datasets(DatasetQuery())
    assert summary.created_at == dataset.provenance.created_at


# --------------------------------------------------------------------------
# datasets: find
# --------------------------------------------------------------------------


@pytest.fixture
def two_datasets(
    store: Store, published: Blueprint, dataset: Dataset, other_dataset: Dataset
) -> tuple[Dataset, Dataset]:
    """Both golden datasets, written newest-authored first.

    Insertion order is deliberately the reverse of ``created_at`` order, so an
    ordering assertion cannot pass by accident.
    """
    later = store.put_dataset(other_dataset)
    earlier = store.put_dataset(dataset)
    return earlier, later


def test_find_orders_by_created_at_then_id(
    store: Store, two_datasets: tuple[Dataset, Dataset]
) -> None:
    """``dataset_find`` promises deterministic ordering by ``(created_at, id)``."""
    earlier, later = two_datasets
    assert [row.id for row in store.find_datasets(DatasetQuery())] == [earlier.id, later.id]


def test_find_returns_one_row_per_id_at_its_latest_version(
    store: Store, published: Blueprint, dataset: Dataset
) -> None:
    """Discovery lists datasets, not versions.

    A row per version would make the promised ``(created_at, id)`` ordering
    ambiguous - every version of a dataset shares both values - and would
    return five near-identical rows for a dataset edited five times.
    """
    store.put_dataset(dataset)
    store.put_dataset(dataset)
    rows = store.find_datasets(DatasetQuery())
    assert [(row.id, row.version) for row in rows] == [(dataset.id, 2)]


def test_find_carries_provenance_and_an_excerpt_but_no_fixtures(
    store: Store, published: Blueprint, dataset: Dataset
) -> None:
    """Contracts 2.2.1: a reviewer judges a dataset without fetching it."""
    store.put_dataset(dataset)
    (row,) = store.find_datasets(DatasetQuery())
    assert row.title == dataset.provenance.title
    assert row.intent == dataset.provenance.intent
    assert row.author == dataset.provenance.author
    assert row.labels == dataset.labels
    assert row.blueprint == dataset.blueprint
    assert row.narrative_excerpt == dataset.narrative[:200]
    assert len(row.narrative_excerpt) < len(dataset.narrative)


def test_find_filters_by_agent_and_blueprint_version(
    store: Store, two_datasets: tuple[Dataset, Dataset]
) -> None:
    assert len(store.find_datasets(DatasetQuery(agent_id="location-onboarding"))) == 2
    assert store.find_datasets(DatasetQuery(agent_id="something-else")) == []
    assert len(store.find_datasets(DatasetQuery(blueprint_version="1.0.0"))) == 2
    assert store.find_datasets(DatasetQuery(blueprint_version="9.9.9")) == []


def test_find_filters_by_author_handle(store: Store, two_datasets: tuple[Dataset, Dataset]) -> None:
    """``author`` filters on ``provenance.author.handle``, per contracts section 4."""
    earlier, later = two_datasets
    assert [row.id for row in store.find_datasets(DatasetQuery(author="pnair"))] == [earlier.id]
    assert [row.id for row in store.find_datasets(DatasetQuery(author="claude-code"))] == [later.id]
    assert store.find_datasets(DatasetQuery(author="Priya Nair")) == []


def test_find_filters_by_labels_and_every_dimension_must_match(
    store: Store, two_datasets: tuple[Dataset, Dataset]
) -> None:
    """The label filter is an AND across dimensions.

    On SQLite this is JSON extraction per dimension, which is contracts section
    7's stated fallback for the absent ``datasets_labels_gin`` index; on
    Postgres the identical expression compiles to ``labels ->> 'tier'`` and the
    GIN index accelerates it. Same results, different plan.
    """
    earlier, later = two_datasets
    assert [row.id for row in store.find_datasets(DatasetQuery(labels={"tier": "regional"}))] == [
        earlier.id
    ]
    assert [
        row.id
        for row in store.find_datasets(
            DatasetQuery(labels={"tier": "single-unit", "outcome": "escalated"})
        )
    ] == [later.id]
    assert (
        store.find_datasets(DatasetQuery(labels={"tier": "regional", "outcome": "escalated"})) == []
    )
    assert store.find_datasets(DatasetQuery(labels={"tier": "enterprise"})) == []
    assert store.find_datasets(DatasetQuery(labels={"no-such-dimension": "x"})) == []


def test_find_q_is_a_case_insensitive_substring_of_title_or_intent(
    store: Store, two_datasets: tuple[Dataset, Dataset]
) -> None:
    """``q`` is "a substring match over ``title`` and ``intent``"."""
    earlier, later = two_datasets
    assert [row.id for row in store.find_datasets(DatasetQuery(q="fssai"))] == [earlier.id]
    assert [row.id for row in store.find_datasets(DatasetQuery(q="FIRST-TIME"))] == [later.id]
    assert [row.id for row in store.find_datasets(DatasetQuery(q="compliant-branch"))] == [later.id]
    assert len(store.find_datasets(DatasetQuery(q="franchisee"))) == 2
    assert store.find_datasets(DatasetQuery(q="no such words")) == []


def test_find_q_treats_wildcards_literally(
    store: Store, two_datasets: tuple[Dataset, Dataset]
) -> None:
    """A ``%`` is a percent sign and a ``_`` is an underscore, not wildcards.

    The filter is a ``LIKE`` on both dialects, so the term is escaped on both.
    Without that, ``q="%"`` would return every row and ``q="_"`` would return
    every row with at least one character - the search box silently doing the
    opposite of what it says.

    ``_`` is the sharper of the two here: neither title contains one, and
    `arun`'s *intent* does (it names ``fetch_store_profile``), so the escaped
    query returns exactly one row where the unescaped one would return two.
    """
    _, later = two_datasets
    assert store.find_datasets(DatasetQuery(q="%")) == []
    assert [row.id for row in store.find_datasets(DatasetQuery(q="_"))] == [later.id]


def test_find_combines_filters(store: Store, two_datasets: tuple[Dataset, Dataset]) -> None:
    earlier, _ = two_datasets
    rows = store.find_datasets(
        DatasetQuery(agent_id="location-onboarding", labels={"tier": "regional"}, q="licence")
    )
    assert [row.id for row in rows] == [earlier.id]


def test_find_paginates(store: Store, two_datasets: tuple[Dataset, Dataset]) -> None:
    """``limit`` and ``offset`` are unconstrained here; `service/` owns defaults."""
    earlier, later = two_datasets
    assert [row.id for row in store.find_datasets(DatasetQuery(limit=1))] == [earlier.id]
    assert [row.id for row in store.find_datasets(DatasetQuery(offset=1))] == [later.id]
    assert [row.id for row in store.find_datasets(DatasetQuery(limit=1, offset=1))] == [later.id]
    assert store.find_datasets(DatasetQuery(offset=2)) == []


# --------------------------------------------------------------------------
# datasets: archive
# --------------------------------------------------------------------------


def test_archive_is_a_flag_across_the_whole_lineage(
    store: Store, published: Blueprint, dataset: Dataset
) -> None:
    """Every version of the id is flipped, and no version is added.

    ``set_archived`` takes no version, so it archives the dataset rather than
    one of its versions - and it must not bump the version, because a run
    pinned to version 1 has to keep reading version 1.
    """
    store.put_dataset(dataset)
    store.put_dataset(dataset)
    summary = store.set_archived(str(dataset.id), True)
    assert summary.version == 2
    for version in (1, 2):
        stored = store.get_dataset(str(dataset.id), version)
        assert stored is not None and stored.archived is True
    assert store.health().counts.datasets == 2


def test_archive_writes_the_flag_into_the_stored_document(
    store: Store, published: Blueprint, dataset: Dataset
) -> None:
    """The column and the document never disagree.

    Which means an exported dataset carries its true archive state, and nothing
    downstream has to know which of the two copies wins.
    """
    store.put_dataset(dataset)
    store.set_archived(str(dataset.id), True)
    stored = store.get_dataset(str(dataset.id), 1)
    assert stored is not None
    assert stored.model_dump(mode="json", exclude_unset=True)["archived"] is True


def test_unarchiving_restores_it_to_find(
    store: Store, published: Blueprint, dataset: Dataset
) -> None:
    store.put_dataset(dataset)
    store.set_archived(str(dataset.id), True)
    assert store.find_datasets(DatasetQuery()) == []
    summary = store.set_archived(str(dataset.id), False)
    assert summary.archived is False
    assert [row.id for row in store.find_datasets(DatasetQuery())] == [dataset.id]


def test_archiving_an_unknown_dataset_raises(store: Store) -> None:
    """``set_archived`` returns a ``DatasetSummary``, so it has no way to say "no".

    A programming-error guard: `service/` checks existence and returns a
    structured error. See `storage/base.py`.
    """
    with pytest.raises(RecordNotFoundError):
        store.set_archived("3f8c1a20-0000-4000-8000-00000000ffff", True)
    with pytest.raises(RecordNotFoundError):
        store.set_archived("not-a-uuid", True)


def test_an_archived_dataset_is_still_servable_to_a_pinned_run(
    store: Store, published: Blueprint, dataset: Dataset
) -> None:
    """The asymmetry between ``find_datasets`` and ``get_dataset``, stated as a test.

    A run holding a pin must keep reading a dataset that has since been
    archived - contracts 3.4 has a ``dataset_archived`` warning for exactly
    that, and a warning is not a refusal.
    """
    store.put_dataset(dataset)
    run = store.put_run(make_run("run-pinned-0001", dataset))
    store.set_archived(str(dataset.id), True)
    assert store.find_datasets(DatasetQuery()) == []
    pinned = store.get_dataset(str(run.pin.dataset_id), run.pin.dataset_version)
    assert pinned is not None and pinned.version == 1


# --------------------------------------------------------------------------
# skeletons (ruling R-05)
# --------------------------------------------------------------------------


def test_skeleton_round_trip_and_submission(
    store: Store, published: Blueprint, dataset: Dataset, skeleton: Skeleton
) -> None:
    """Ruling R-05's first added clause: a skeleton round trip, including submission."""
    written = store.put_skeleton(skeleton)
    assert written == skeleton
    assert store.get_skeleton(str(skeleton.id)) == written
    assert [section.id for section in written.manifest] == [
        "provenance",
        "entities",
        "nodes.core",
        "nodes.branches",
        "expected",
    ]
    assert written.parts == {}
    assert written.submitted_as is None

    store.put_dataset(dataset)
    store.mark_skeleton_submitted(str(skeleton.id), str(dataset.id))
    submitted = store.get_skeleton(str(skeleton.id))
    assert submitted is not None and submitted.submitted_as == dataset.id


def test_a_section_may_be_refilled(store: Store, skeleton: Skeleton) -> None:
    """Ruling R-06: re-filling an already-filled section is explicitly allowed.

    PRD 6 flow B depends on it - a rejection returns errors scoped to one
    section so the LLM repairs that part rather than regenerating everything.
    """
    store.put_skeleton(skeleton)
    filled = store.put_skeleton(
        skeleton.model_copy(update={"parts": {"provenance": {"title": "first attempt"}}})
    )
    assert filled.parts == {"provenance": {"title": "first attempt"}}
    repaired = store.put_skeleton(
        skeleton.model_copy(update={"parts": {"provenance": {"title": "repaired"}}})
    )
    assert repaired.parts == {"provenance": {"title": "repaired"}}
    assert store.get_skeleton(str(skeleton.id)) == repaired


def test_marking_the_same_dataset_twice_is_a_no_op(
    store: Store, published: Blueprint, dataset: Dataset, skeleton: Skeleton
) -> None:
    store.put_skeleton(skeleton)
    store.put_dataset(dataset)
    store.mark_skeleton_submitted(str(skeleton.id), str(dataset.id))
    store.mark_skeleton_submitted(str(skeleton.id), str(dataset.id))
    stored = store.get_skeleton(str(skeleton.id))
    assert stored is not None and stored.submitted_as == dataset.id


def test_marking_a_different_dataset_is_refused(
    store: Store, published: Blueprint, dataset: Dataset, other_dataset: Dataset, skeleton: Skeleton
) -> None:
    """SK-005 is M5's rule; this is the guard that stops lineage being overwritten."""
    store.put_skeleton(skeleton)
    store.put_dataset(dataset)
    store.mark_skeleton_submitted(str(skeleton.id), str(dataset.id))
    with pytest.raises(StoreError):
        store.mark_skeleton_submitted(str(skeleton.id), str(other_dataset.id))
    stored = store.get_skeleton(str(skeleton.id))
    assert stored is not None and stored.submitted_as == dataset.id


def test_an_unknown_skeleton_reads_as_none_and_refuses_a_mark(store: Store) -> None:
    assert store.get_skeleton("3f8c1a20-0000-4000-8000-00000000ffff") is None
    assert store.get_skeleton("not-a-uuid") is None
    with pytest.raises(RecordNotFoundError):
        store.mark_skeleton_submitted(
            "3f8c1a20-0000-4000-8000-00000000ffff", "3f8c1a20-0000-4000-8000-000000000001"
        )


# --------------------------------------------------------------------------
# runs and steps (ruling R-05)
# --------------------------------------------------------------------------


@pytest.fixture
def pinned(store: Store, published: Blueprint, dataset: Dataset) -> Dataset:
    """One stored dataset version for a run to pin."""
    return store.put_dataset(dataset)


def test_run_round_trip(store: Store, pinned: Dataset) -> None:
    """Ruling R-05's second added clause, first half: a run.

    ``declared_blueprint_version`` is carried. Contracts section 2.3 has the
    field and the section 7 DDL has no column for it, which is reported as a
    discrepancy in the M3 report; the column exists because that field is the
    input to the ``blueprint_version_mismatch`` warning, which ground rule 3
    requires be attached to the stored run.
    """
    run = make_run(
        "run-0001",
        pinned,
        declared_blueprint_version="1.1.0",
        model=ModelInfo(provider="anthropic", name="claude-opus-5", version="20260401"),
        warnings=[Warning(code="blueprint_version_mismatch", detail={"declared": "1.1.0"})],
        external_refs={"otel_trace_id": "trace-1", "langfuse_run_id": None},
        outcome={"status": "pending"},
    )
    written = store.put_run(run)
    assert written == run
    assert store.get_run(run.id) == written
    assert written.pin.dataset_id == pinned.id
    assert written.pin.dataset_version == 1


def test_an_unknown_run_reads_as_none(store: Store) -> None:
    assert store.get_run("no-such-run") is None
    assert store.find_runs(RunQuery()) == []


def test_upsert_step_is_idempotent_on_the_same_key(store: Store, pinned: Dataset) -> None:
    """Ruling R-05's second added clause, second half.

    Two calls with the same ``(run_id, node_id, iteration)``: the second is a
    no-op that returns the existing record. The stored ``served`` fixture is
    *not* replaced, which is the point - a retry an hour later resolves to the
    same fixture, and the guarantee is the primary key's rather than
    application code's.
    """
    run = store.put_run(make_run("run-0002", pinned))
    first = store.upsert_step(
        run.id, StepRecord(node_id="fetch_store_profile", iteration=0, served={"store_id": "s-1"})
    )
    second = store.upsert_step(
        run.id,
        StepRecord(node_id="fetch_store_profile", iteration=0, served={"store_id": "OVERWRITTEN"}),
    )
    assert second == first
    assert second.served == {"store_id": "s-1"}
    assert second.seq == first.seq

    stored = store.get_run(run.id)
    assert stored is not None and len(stored.steps) == 1


def test_seq_is_allocated_per_run_and_monotonic(store: Store, pinned: Dataset) -> None:
    """``seq`` is ``NOT NULL`` in the DDL and optional on the model.

    The store allocates it - a per-run counter - rather than passing the
    model's ``None``, and a caller-supplied value is ignored, because ``seq`` is
    the store's own record of the order steps were served in.
    """
    first = store.put_run(make_run("run-0003", pinned))
    second = store.put_run(make_run("run-0004", pinned))

    steps = [
        store.upsert_step(first.id, StepRecord(node_id="receive_request", iteration=0, served={})),
        store.upsert_step(
            first.id, StepRecord(node_id="request_docs", iteration=0, served={}, seq=999)
        ),
        store.upsert_step(first.id, StepRecord(node_id="request_docs", iteration=1, served={})),
    ]
    assert [step.seq for step in steps] == [1, 2, 3]

    other = store.upsert_step(
        second.id, StepRecord(node_id="receive_request", iteration=0, served={})
    )
    assert other.seq == 1


def test_fetched_at_falls_back_to_the_column_default(store: Store, pinned: Dataset) -> None:
    """Ruling R-09's first legitimate timestamp source: a DB column default.

    ``StepRecord.fetched_at`` is optional and ``run_steps.fetched_at`` is
    ``NOT NULL DEFAULT now()``, so a step recorded without one gets the
    database's - which is why nothing in `storage/` reads a clock.
    """
    run = store.put_run(make_run("run-0005", pinned))
    defaulted = store.upsert_step(run.id, StepRecord(node_id="check_docs", iteration=0, served={}))
    assert defaulted.fetched_at is not None

    supplied = store.upsert_step(
        run.id,
        StepRecord(node_id="assign_training", iteration=0, served={}, fetched_at=FROZEN_NOW),
    )
    assert supplied.fetched_at == FROZEN_NOW


def test_the_path_is_reconstructed_from_the_steps(store: Store, pinned: Dataset) -> None:
    """No column holds ``path``, and that is the design.

    The ordered sequence of calls carrying a run id *is* the traversal, so
    branch selection is observed rather than declared and an agent cannot
    report a path it did not take. A declared ``path`` on the way in is
    therefore ignored, and ``put_run`` returns the reconstruction so that a
    write and a read agree.
    """
    run = store.put_run(make_run("run-0006", pinned))
    assert run.path == []
    for node_id, iteration in (("receive_request", 0), ("request_docs", 0), ("request_docs", 1)):
        store.upsert_step(run.id, StepRecord(node_id=node_id, iteration=iteration, served={}))
    stored = store.get_run(run.id)
    assert stored is not None
    assert [(step.node_id, step.iteration) for step in stored.path] == [
        ("receive_request", 0),
        ("request_docs", 0),
        ("request_docs", 1),
    ]
    assert [step.at for step in stored.path] == [step.fetched_at for step in stored.steps]


def test_put_run_writes_carried_steps_idempotently(store: Store, pinned: Dataset) -> None:
    """Steps on the model go through the same idempotent path.

    So re-putting a run - which `service/` does on every status change - does
    not duplicate or renumber the steps it already has.
    """
    run = make_run(
        "run-0007",
        pinned,
        steps=[
            StepRecord(node_id="receive_request", iteration=0, served={"a": 1}),
            StepRecord(node_id="check_docs", iteration=0, served={"b": 2}),
        ],
    )
    written = store.put_run(run)
    assert [step.seq for step in written.steps] == [1, 2]

    again = store.put_run(run.model_copy(update={"status": "finished"}))
    assert again.status == "finished"
    assert [step.seq for step in again.steps] == [1, 2]
    assert [step.served for step in again.steps] == [{"a": 1}, {"b": 2}]


def test_find_runs_filters_and_orders_newest_first(store: Store, pinned: Dataset) -> None:
    """The order ``runs_lookup`` is built for: ``started_at DESC``, tie-broken by id."""
    store.put_run(make_run("run-old", pinned, run_class="eval", started_at=FROZEN_NOW))
    store.put_run(
        make_run(
            "run-new",
            pinned,
            model=ModelInfo(provider="anthropic", name="claude-opus-5", version="20260401"),
            started_at=FROZEN_NOW + timedelta(hours=1),
        )
    )
    assert [row.id for row in store.find_runs(RunQuery())] == ["run-new", "run-old"]
    assert [row.id for row in store.find_runs(RunQuery(run_class="eval"))] == ["run-old"]
    assert [row.id for row in store.find_runs(RunQuery(model="claude-opus-5"))] == ["run-new"]
    assert [row.id for row in store.find_runs(RunQuery(dataset_id=str(pinned.id)))] == [
        "run-new",
        "run-old",
    ]
    assert [row.id for row in store.find_runs(RunQuery(limit=1))] == ["run-new"]
    assert [row.id for row in store.find_runs(RunQuery(offset=1))] == ["run-old"]
    assert store.find_runs(RunQuery(agent_id="somebody-else")) == []


def test_a_run_summary_withholds_the_outcome(store: Store, pinned: Dataset) -> None:
    """Ruling R-05: ``RunSummary`` is the ``runs`` columns minus ``outcome``."""
    store.put_run(make_run("run-0008", pinned, outcome={"big": "payload"}))
    (row,) = store.find_runs(RunQuery())
    assert not hasattr(row, "outcome")
    assert row.dataset_ver == 1
    assert row.bp_version == pinned.blueprint.version


def test_a_malformed_dataset_id_filter_matches_nothing(store: Store, pinned: Dataset) -> None:
    store.put_run(make_run("run-0009", pinned))
    assert store.find_runs(RunQuery(dataset_id="not-a-uuid")) == []


# --------------------------------------------------------------------------
# health (ruling R-05)
# --------------------------------------------------------------------------


def test_health_on_an_empty_store(store: Store) -> None:
    health = store.health()
    assert health.healthy is True
    assert health.backend in {"sqlite", "postgres", "mongo"}
    assert (health.counts.blueprints, health.counts.datasets, health.counts.runs) == (0, 0, 0)


def test_health_counts_every_version_and_includes_archived(
    store: Store, published: Blueprint, dataset: Dataset, other_dataset: Dataset
) -> None:
    """Ruling R-05's third added clause.

    ``counts.datasets`` includes archived datasets: this is a store-health
    number, not a discovery number, and ``find_datasets`` remains the thing
    that hides archives. The counts are row counts, so an edited dataset counts
    twice.
    """
    store.put_dataset(dataset)
    store.put_dataset(dataset)
    store.put_dataset(other_dataset)
    store.put_run(make_run("run-0010", dataset))
    store.set_archived(str(dataset.id), True)

    health = store.health()
    assert health.healthy is True
    assert health.counts.blueprints == 1
    assert health.counts.datasets == 3
    assert health.counts.runs == 1
    assert store.find_datasets(DatasetQuery()) == [
        row for row in store.find_datasets(DatasetQuery()) if row.archived is False
    ]
    assert len(store.find_datasets(DatasetQuery())) == 1
