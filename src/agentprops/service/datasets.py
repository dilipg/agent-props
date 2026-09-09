"""Dataset reads, the archive flag, and ``dataset_validate``.

M4 owns the dataset **read** path plus the two archive flips. There is no
dataset *write* here: ``dataset_submit`` is the only thing that writes a
dataset and it lands at M5 with the skeleton pipeline, which is also why
nothing in this module stamps ``validated_at`` from the clock.

``dataset_validate`` lands here rather than at M5 because ruling R-15 assigns
it to M4 "beside ``blueprint_validate``, which M9 needs for cross-cutting
validation", and no other milestone owns it.

Three properties of this module come straight from the ground rules and are
each one line of code that would be easy to write the other way.

**An archived dataset is still returned by ``dataset_get``.** The service never
gates. The archive flag produces a ``dataset_archived`` *warning* on the
response - contracts 3.4's own vocabulary - and the dataset comes back in full.
That asymmetry with ``dataset_find``, which excludes archives, is deliberate and
lives in the store: a run holding a pin has to keep reading a dataset that was
archived after it started.

**Nothing here re-sorts or re-filters what the store returned.** Rulings R-35
and R-38 make ``find_datasets`` one row per lineage at its latest version, in a
total ``(created_at, id)`` order, and ruling R-36 makes ``q`` a substring match
with identical results on every backend. A Python ``sorted()`` or a list
comprehension over the rows here would undo one of those and M4's
byte-identical-output criterion is what would catch it - eventually, on a
different backend.

**``limit`` and ``offset`` are clamped at both ends, never refused.**
``DatasetQuery`` carries no ``ge``/``le`` constraint (ruling R-04) and its
docstring says "the service decides the defaults so the three storage adapters
do not each invent their own". So this is where the defaults are.

Both ends matter and only one of them was obvious. A *negative* ``limit`` is a
per-backend accident (SQLite reads ``LIMIT -1`` as "no limit", Postgres rejects
it), so it clamps to zero. A value *above* ``2**63 - 1`` is worse than an
accident: it is outside every backend's integer column, pysqlite raises
``OverflowError``, and the MCP SDK turns that into a protocol error rather than
an envelope. `limits.py` carries the reproduction and the reasoning; the fix is
one `clamp` call, and :func:`test_paginate_clamps_at_the_int64_boundary` tests
**both** ends of the range rather than the one this module's first version
happened to think of.
"""

from __future__ import annotations

from typing import Any, Final

from agentprops.models import WARNING_DATASET_ARCHIVED, Dataset, DatasetQuery
from agentprops.service.context import ServiceContext
from agentprops.service.documents import read_document
from agentprops.service.envelope import Reply, not_found, success, warning
from agentprops.service.limits import DEFAULT_PAGE_LIMIT, page, storable
from agentprops.storage import RecordNotFoundError
from agentprops.validation import envelope as validation_envelope
from agentprops.validation import validate_dataset

__all__ = [
    "DEFAULT_FIND_LIMIT",
    "archive",
    "document",
    "find",
    "get",
    "paginate",
    "restore",
    "validate",
]

#: What ``dataset_find`` returns when the caller names no ``limit``. A page
#: size, not a cap: an explicit ``limit`` of 5000 is honoured.
#:
#: Bound to :data:`~agentprops.service.limits.DEFAULT_PAGE_LIMIT` by assignment
#: rather than repeated, so ``dataset_find`` and ``run_find`` cannot drift into
#: two different page sizes. The name stays because it is this module's
#: documented surface.
DEFAULT_FIND_LIMIT: Final = DEFAULT_PAGE_LIMIT


def find(context: ServiceContext, query: DatasetQuery) -> Reply:
    """Dataset summaries: one row per lineage at its latest version, archives excluded.

    Takes a ``DatasetQuery`` because that type is *defined* as
    ``dataset_find``'s parameters (ruling R-05), so the tool function has
    somewhere to put its parsed arguments without a second parallel shape. The
    pagination fields are the only ones this function rewrites, and
    :func:`paginate` is where the defaults ruling R-04 keeps out of the models
    actually live.
    """
    rows = context.store.find_datasets(paginate(query))
    return success("datasets", [row.model_dump(mode="json") for row in rows])


def paginate(query: DatasetQuery) -> DatasetQuery:
    """``query`` with ``limit`` and ``offset`` defaulted and clamped to ``[0, 2**63-1]``.

    Separate and public so the clamping has a test that does not need a store,
    and so M7's ``dataset_export`` gets the same defaults rather than its own.
    The two decisions themselves - the default page size and both ends of the
    clamp - moved to :func:`~agentprops.service.limits.page` at M6, so
    ``run_find`` shares them rather than repeating them.

    The upper clamp is a *representability* bound rather than a policy cap - see
    `limits.py`. A ``limit`` of ``2**63 - 1`` already means "every row there will
    ever be", so a caller asking for more is asking for the same thing and
    nothing is refused.
    """
    limit, offset = page(query.limit, query.offset)
    return query.model_copy(update={"limit": limit, "offset": offset})


def get(context: ServiceContext, dataset_id: str, version: int | None) -> Reply:
    """One dataset version in full. The latest when ``version`` is omitted.

    Archived datasets are returned, with a ``dataset_archived`` warning.

    A ``version`` outside the range a row can hold is a **miss**, not a clamp: it
    is not a large version, it is no version, and clamping an identifier would
    answer a question the caller did not ask. Without the check the value reaches
    pysqlite and raises ``OverflowError`` - see `limits.py`.
    """
    if version is not None and not storable(version):
        return not_found("dataset", field="dataset_id", dataset_id=dataset_id, version=version)
    dataset = context.store.get_dataset(dataset_id, version)
    if dataset is None:
        return not_found("dataset", field="dataset_id", dataset_id=dataset_id, version=version)
    warnings = (
        [warning(WARNING_DATASET_ARCHIVED, dataset_id=str(dataset.id), version=dataset.version)]
        if dataset.archived
        else []
    )
    return success("dataset", document(dataset), warnings)


def archive(context: ServiceContext, dataset_id: str) -> Reply:
    """Hide a dataset lineage from discovery. Lineage-level (ruling R-34)."""
    return _set_archived(context, dataset_id, archived=True)


def restore(context: ServiceContext, dataset_id: str) -> Reply:
    """Un-hide a dataset lineage."""
    return _set_archived(context, dataset_id, archived=False)


def _set_archived(context: ServiceContext, dataset_id: str, *, archived: bool) -> Reply:
    """The one write both flips share.

    ``set_archived`` raises ``RecordNotFoundError`` for an unknown id because
    its return type - a ``DatasetSummary`` - leaves no room for "not found".
    That is a *user-causable* condition (any string can be passed as a dataset
    id), so it becomes an ``AP-004`` envelope here. There is a test for the
    raising side.
    """
    try:
        summary = context.store.set_archived(dataset_id, archived)
    except RecordNotFoundError:
        return not_found("dataset", field="dataset_id", dataset_id=dataset_id)
    return success("summary", summary.model_dump(mode="json"))


def validate(context: ServiceContext, payload: object, text: str = "") -> Reply:
    """``{ok, errors}`` for a dataset document. Stores nothing.

    ``text`` is the raw JSON boundary ruling R-20 needs: duplicate keys only
    exist before parsing, so DS-013 can only fire on this path.
    :mod:`agentprops.service.documents` carries the evidence for why the parsed
    path cannot reach it.

    The envelope is the validator's own, so a document whose only findings are
    warnings (DS-007, DS-027, DS-032) reports ``ok: true`` with them in
    ``errors`` (ruling R-13).
    """
    resolved = read_document("dataset", payload, text_field="dataset_json", text=text)
    if not resolved.ok:
        return validation_envelope(list(resolved.findings))
    return validate_dataset(
        resolved.value, context.resolver, duplicate_keys=resolved.duplicate_keys
    )


def document(dataset: Dataset) -> dict[str, Any]:
    """The stored document, as submitted (ruling R-08's ``exclude_unset``).

    Public for the reason ``service/blueprints.py::document`` is: a resource
    serves the same bytes ``dataset_get`` does, from one function.
    """
    document: dict[str, Any] = dataset.model_dump(mode="json", exclude_unset=True)
    return document
