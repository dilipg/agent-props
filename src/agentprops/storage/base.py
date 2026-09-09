"""The ``Store`` Protocol every adapter implements, and the two guards it raises.

`docs/contracts.md` section 6 is the authority for the shape below: the method
names, the argument names and the return types are that section verbatim. The
conformance suite in `tests/integration/` is written once against this Protocol
and parameterised over backends, so an adapter is finished when it passes that
suite - not when it looks finished.

**There is no ``delete_*`` method, and there never will be.** Ground rule 6 is
"no hard delete"; archive is a flag (:meth:`Store.set_archived`) and a dataset
edit is copy-on-write (:meth:`Store.put_dataset`). Two tests in
`tests/unit/test_storage_no_delete.py` hold the line: one asserts this Protocol
declares no method whose name starts with ``delete``, the other reads the AST of
every module in this package and asserts none of them issues a SQL ``DELETE``.

What this Protocol deliberately does **not** do
-----------------------------------------------

**It does not validate** (ruling R-23). `service/` validates the raw document
and constructs the model only after validation passes, so by the time a
document reaches an adapter it is trusted - which is ground rule 7, "validation
is strict at write time and absent at read time", read from the storage side.
An adapter that re-ran the catalogue would be doing `service/`'s job with none
of `service/`'s context.

**It does not read a clock** (ruling R-09). A timestamp reaches a row from one
of exactly four places: a DB column default, the client, authored content in
the document, or ``Seeded.timestamp()``. Two consequences are visible in the
DDL and are worth stating here because they look like oversights otherwise:
``datasets.created_at`` is populated from ``provenance.created_at`` rather than
``now()``, which is what makes ``find_datasets``'s ordering by
``(created_at, id)`` survive an export/import cycle; and ``validated_at`` and
the run timestamps arrive on the model from `service/`'s injected ``Clock``.

**It does not mint an id** (ruling R-10). Dataset and skeleton ids come from
``Seeded.uuid()`` at M5/M7. A store persists whatever id it is handed.

**It does not gate** (ground rule 3). Nothing here returns a policy verdict, so
nothing here refuses to serve because something looked wrong.

Errors
------

Everything a *user* can cause is a structured error with a rule id, raised
nowhere and returned by `service/`. The two exceptions defined here are
therefore **programming-error guards**, in the same sense as the two ``raise``
statements `validation/` allows: each one fires only where `service/` should
already have returned a rule id, and each one refuses to destroy data rather
than doing so quietly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from agentprops.models import (
    Blueprint,
    BlueprintSummary,
    Dataset,
    DatasetQuery,
    DatasetSummary,
    Run,
    RunQuery,
    RunSummary,
    Skeleton,
    StepRecord,
    StoreHealth,
    Warning,
)

__all__ = [
    "STATUS_DRAFT",
    "STATUS_PUBLISHED",
    "PublishedVersionImmutableError",
    "RecordNotFoundError",
    "Store",
    "StoreError",
]

#: The two values ``blueprints.status`` may hold, per the DDL's ``CHECK``.
#: BP-016 makes ``published`` immutable; a ``draft`` is freely overwritten.
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"


class StoreError(RuntimeError):
    """A storage invariant was violated by the caller.

    Not a user-facing error. Every subclass below marks a condition `service/`
    is responsible for turning into a rule id before storage is reached, so
    seeing one in a log means a write path skipped validation.
    """


class PublishedVersionImmutableError(StoreError):
    """A differing document was written over a published ``{agent_id, version}``.

    BP-016, enforced a second time at the last possible moment. `service/`
    reports BP-016 as a rule id through the ``Resolver``; if a differing
    document still arrives here, the alternative to raising is losing the
    published version, so this raises.

    Ruling R-29 is what makes the *identical* case different: a byte-identical
    re-publish is a no-op success returning the stored blueprint, compared
    canonically (``json.dumps(sort_keys=True)``) so key re-ordering is not a
    difference. Only a differing document reaches this exception.
    """


class RecordNotFoundError(StoreError):
    """A write named a row that does not exist.

    Raised by the two methods whose return type leaves no room for "not found"
    - :meth:`Store.set_archived`, which must return a ``DatasetSummary``, and
    :meth:`Store.mark_skeleton_submitted`, whose ``bool`` answers "did this call
    claim the skeleton", a question an absent skeleton does not have a false
    answer to. The read methods never raise this: they return ``None``,
    including for an id that is not a well-formed UUID, because a malformed
    filter should produce no rows rather than an exception.
    """


@runtime_checkable
class Store(Protocol):
    """Everything the service layer may ask of a storage backend.

    ``runtime_checkable`` so a test can assert an adapter satisfies the shape at
    runtime; the *signatures* are checked statically by ``mypy --strict``,
    which is the half ``isinstance`` cannot see.
    """

    # blueprints

    def put_blueprint(self, bp: Blueprint, publish: bool) -> Blueprint:
        """Write a blueprint at ``{bp.agent_id, bp.version}``.

        ``publish`` decides the stored ``status``, and the stored document's
        ``status`` field is normalised to match it, so a row and the document
        inside it can never disagree. Returns the blueprint as stored.

        A ``draft`` is freely overwritten. A ``published`` version is immutable
        (BP-016): an identical re-write is a no-op success returning the stored
        blueprint (ruling R-29), and a differing one raises
        :class:`PublishedVersionImmutableError`.
        """
        ...

    def get_blueprint(self, agent_id: str, version: str | None) -> Blueprint | None:
        """One blueprint, or ``None``.

        With ``version``, that exact version whatever its status. Without it,
        the latest *published* version - which is what ``blueprint_get``
        documents ("Latest published when version omitted") and what a running
        agent needs.
        """
        ...

    def list_blueprints(self, status: str | None) -> list[BlueprintSummary]:
        """Summaries, optionally filtered by status, in a deterministic order."""
        ...

    # datasets: copy-on-write, never mutate in place

    def put_dataset(self, ds: Dataset) -> Dataset:
        """Write a new version of ``ds.id`` and return it, version filled in.

        The store allocates the version - ``max(version) + 1`` for that id, so
        it is monotonic - and ``ds.version`` is ignored. Nothing is ever
        overwritten: every earlier version stays independently readable by
        :meth:`get_dataset`, which is what lets a run pinned to version 1 keep
        reading version 1 for its whole life.

        ``ds.archived`` is ignored too, on every version after the first: the
        flag belongs to the lineage, not to a version (ruling R-34), so a new
        version inherits whatever :meth:`set_archived` last set. Otherwise an
        edit would silently un-hide an archived dataset, and a half-archived
        lineage has no coherent answer for :meth:`find_datasets`, which returns
        one row per lineage.
        """
        ...

    def get_dataset(self, dataset_id: str, version: int | None) -> Dataset | None:
        """One dataset version, or ``None``. The latest when ``version`` is omitted.

        Returns archived datasets. That asymmetry with :meth:`find_datasets` is
        deliberate: a run holding a pin must keep reading a dataset that has
        since been archived.
        """
        ...

    def find_datasets(self, q: DatasetQuery) -> list[DatasetSummary]:
        """Discovery. One row per dataset **lineage** - its latest version -
        **excluding archived**, ordered deterministically by ``(created_at, id)``.

        The lineage grain is ruling R-38, and it is forced rather than chosen:
        ``created_at`` comes from ``provenance.created_at`` (ruling R-09), which
        is identical across every version of one dataset, so per-version rows
        would share both sort keys and the specified ordering could not be
        total. ``get_dataset(dataset_id, version)`` reaches a version.
        """
        ...

    def set_archived(self, dataset_id: str, archived: bool) -> DatasetSummary:
        """Flip the archive flag across every version of ``dataset_id``.

        A flag, not a deletion, and not a new version: archiving must not
        change what a pinned run reads. It takes no version because the flag is
        lineage-level (ruling R-34), which is also why :meth:`put_dataset`
        inherits it. Raises :class:`RecordNotFoundError` if no such dataset
        exists.
        """
        ...

    # skeletons: partial fill state

    def put_skeleton(self, sk: Skeleton) -> Skeleton:
        """Write a skeleton, replacing any earlier state for the same id.

        Re-filling a section is explicitly allowed (ruling R-06), so this is an
        upsert rather than an append.
        """
        ...

    def get_skeleton(self, skeleton_id: str) -> Skeleton | None:
        """One skeleton, or ``None`` - including for a malformed id."""
        ...

    def mark_skeleton_submitted(self, skeleton_id: str, dataset_id: str) -> bool:
        """Claim a skeleton for ``dataset_id``. A **compare-and-set** (ruling R-47).

        Succeeds, and returns ``True``, only if ``submitted_as`` is currently
        null. Returns ``False`` when the skeleton has already been claimed - by
        this dataset id or by any other - which is the signal the losing caller
        of two concurrent submits needs. Raises
        :class:`RecordNotFoundError` for an unknown skeleton.

        **This replaced a no-op replay tolerance**, and the change is what makes
        SK-005 an invariant the store can hold rather than one the catalogue
        merely asserts. Before it, two concurrent ``dataset_submit`` calls both
        read ``submitted_as`` as null, both passed SK-005, and both wrote - the
        loser receiving a success envelope for a dataset version it did not mean
        to create, with nothing in the response saying so. A duplicate row is
        not corruption; a silent one is worse than an error.

        A second *sequential* submit is still refused by SK-005 before reaching
        here, so this is the concurrent path's guard rather than its only one.
        Ruling R-48 deliberately does **not** extend the same treatment to
        ``put_skeleton``: a lost fill is visible in the next response's
        ``remaining`` and self-correcting, while a lost submit is silent.
        """
        ...

    # runs

    def put_run(self, run: Run) -> Run:
        """Write a run, keyed by its client-generated id, and return it as stored.

        ``path`` has no column: it is reconstructed from ``run_steps`` in
        ``seq`` order, because the ordered sequence of calls carrying a run id
        *is* the traversal and an agent must not be able to declare a path it
        did not take. Any ``steps`` carried on the model are written through
        :meth:`upsert_step`, so they inherit its idempotency.
        """
        ...

    def get_run(self, run_id: str) -> Run | None:
        """One run with its steps and reconstructed path, or ``None``."""
        ...

    def find_runs(self, q: RunQuery) -> list[RunSummary]:
        """Run summaries - the ``runs`` columns minus ``outcome`` (ruling R-05)."""
        ...

    def upsert_step(self, run_id: str, step: StepRecord) -> StepRecord:
        """Record a served step. **Idempotent** on ``(run_id, node_id, iteration)``.

        A second call with the same key is a no-op that returns the existing
        record, which is what makes ``fetch_step`` idempotent at the storage
        layer rather than in application code: a retry an hour later resolves
        to the same fixture and the same ``seq``.

        ``seq`` is allocated here - a per-run monotonic counter, guarded by
        ``UNIQUE (run_id, seq)`` (ruling R-37) - because it is an authoritative
        fact about serve order rather than caller data. Any ``seq`` on the
        incoming model is ignored.

        This records **what was served**, and only that. Writing ``actual``
        later is :meth:`set_step_actual`'s job, because a no-op repeat cannot
        also be a merge.
        """
        ...

    def set_run_warnings(self, run_id: str, warnings: Sequence[Warning]) -> list[Warning]:
        """Replace a run's warning list. Touches **that column and nothing else**.

        An addition to `docs/contracts.md` section 6, authorised by ruling R-53
        - which requires a diverging ``run_start`` to warn "attached to the
          response *and* to the stored run" - and by R-54(b), which accepts the
        merge as read-modify-write. It exists because the obvious
        implementation of both, ``put_run`` with an edited copy of the run, is
        **wrong in a way nothing today would catch**: :meth:`put_run` writes
        every column from the model it is handed, so a ``fetch_step`` adding a
        ``pool_exhausted`` warning from a snapshot read before M8's
        ``run_finish`` committed would revert ``status`` to ``running`` and null
        ``outcome`` and ``finished_at``. A run that un-finishes itself under
        load is the most expensive shape of bug this build can ship, and
        ``run_finish`` not existing yet is the only reason it is not one.

        Narrowing the write to one column is what makes the residue R-54(b)
        accepts *actually* what R-54(b) describes: two concurrent warning
        writers can lose one advisory entry, and nothing else can be touched.
        Note what a transaction would **not** buy here - pysqlite defers
        ``BEGIN`` until the first DML and Postgres under READ COMMITTED behaves
        the same way (ruling R-37), so wrapping a read and a write does not lock
        the value read. The column bound is the guarantee; the transaction is
        not.

        The caller computes the merged list, because deduplication is policy -
        R-54(b) fixes the key at ``(code, node_id, iteration)`` - and this
        Protocol does not decide policy. Returns the list as stored, and raises
        :class:`RecordNotFoundError` for an unknown run, for the reason
        :meth:`set_archived` does: the return type leaves no room for "not
        found".
        """
        ...

    def set_step_actual(
        self, run_id: str, node_id: str, iteration: int, actual: Mapping[str, Any]
    ) -> StepRecord:
        """Record **what the agent did** at a step. Write-once per key.

        Ruling R-33's method, on the Protocol from M3 so that M7's adapters
        implement it rather than M8 reopening three signed-off ones. The
        division of labour with :meth:`upsert_step` is deliberate and both
        halves matter: ``upsert_step``'s repeat must stay a literal no-op,
        because M6's gate is that "fetching the same step key twice returns
        byte-identical fixtures and advances nothing", so it can never be the
        thing that merges in an ``actual``.

        Raises :class:`RecordNotFoundError` when no step record exists - an
        actual cannot be reported for a step that was never served - and
        :class:`StoreError` when a *different* actual is already recorded.
        Re-recording an identical one is a no-op success, the same replay
        tolerance :meth:`mark_skeleton_submitted` and BP-016 have.

        Ground rule 1 is untouched: this writes to a run, never to a dataset.
        """
        ...

    def health(self) -> StoreHealth:
        """Liveness plus row counts. Never raises, never gates.

        ``counts.datasets`` **includes archived datasets** (ruling R-05): this
        is a store-health number, not a discovery number.
        """
        ...
