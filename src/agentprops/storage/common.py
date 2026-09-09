"""What every adapter must decide identically: the pure helpers and the mappers.

This module exists because of one sentence in ruling R-36 and one in R-39(a).
R-36: substring semantics for ``q`` "are the contract", and Postgres "must not
improve" them. R-39(a): the case fold moved into Python because "no SQL
expression folds case identically across all three backends - SQLite's
``lower()`` is ASCII-only, verified - so both the fold and the substring match
moved into Python". A second copy of that fold inside the Mongo adapter would
be the drift those rulings exist to prevent, and it would drift silently: every
fixture in the suite is ASCII except the one non-ASCII ``q`` case.

So everything two adapters must answer the same way lives here, once:

**The pure predicates and orderings.** :func:`folded` and :func:`q_matches` are
the ``q`` filter. :func:`semver_key` is ``list_blueprints``'s ordering, which
has to be semver-aware rather than lexicographic (ruling R-35). :func:`canonical`
is BP-016's identity comparison and ``set_step_actual``'s (ruling R-29).
:func:`slice_page` is what ``limit``/``offset`` mean when they cannot be pushed
into the query.

**The row-to-model mappers.** Every adapter reduces a stored record to a flat
``Mapping`` of the promoted fields and hands it to :func:`dataset_summary`,
:func:`skeleton_from`, :func:`step_from`, :func:`run_from` or
:func:`run_summary`. Contracts 2.2.1 puts a 200-character ``narrative``
excerpt in a dataset summary and ruling R-05 derives ``RunSummary`` from "the
``runs`` DDL columns minus ``outcome``"; both are decisions about the *shape*
the Protocol returns, not about a dialect, so an adapter that reimplemented
them could satisfy every type check and still return a different answer than
its sibling. The conformance suite asserts identical results across backends -
this is what makes that achievable rather than coincidental.

**The retry budgets.** Version allocation, ``seq`` allocation and the
first-write race are guarded by a unique key on all three backends (rulings
R-37 and R-39), so the number of attempts is a property of the pattern and not
of the driver.

A ``Mapping`` rather than a positional tuple, deliberately: SQLAlchemy hands
back a ``Row`` whose ``_mapping`` is one, pymongo hands back a ``dict``, and a
tuple would make the two adapters agree about field *order* as well as about
field names - which is one more thing to keep in sync for no benefit.

Nothing here does I/O, reads a clock, mints an id or imports a sibling layer.
`tests/unit/test_layering.py` asserts all four for every module in this
package.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Final

from agentprops.models import (
    Author,
    Blueprint,
    BlueprintRef,
    BlueprintSummary,
    Dataset,
    DatasetSummary,
    ModelInfo,
    PathStep,
    Run,
    RunPin,
    RunSummary,
    Skeleton,
    StepRecord,
    Warning,
)
from agentprops.storage.base import StoreError

__all__ = [
    "FIRST_WRITE_ATTEMPTS",
    "NARRATIVE_EXCERPT_CHARS",
    "SEQ_ALLOCATION_ATTEMPTS",
    "VERSION_ALLOCATION_ATTEMPTS",
    "blueprint_summary",
    "canonical",
    "dataset_summary",
    "folded",
    "labels_of",
    "parse_uuid",
    "q_matches",
    "require_uuid",
    "run_from",
    "run_summary",
    "semver_key",
    "skeleton_from",
    "slice_page",
    "step_from",
    "stored_document",
]

#: How many characters of ``narrative`` a :class:`DatasetSummary` carries.
#: Contracts section 2.2.1: "the first 200 characters of narrative".
NARRATIVE_EXCERPT_CHARS: Final = 200

#: Bounded retries for dataset version allocation. Reached only when another
#: writer wins the race for the same ``(id, version)`` that many times running.
#: One number for every adapter: the guard is a unique key on all three
#: backends, so the budget is a property of the pattern and not of the driver.
VERSION_ALLOCATION_ATTEMPTS: Final = 8

#: Bounded retries for ``run_steps.seq`` allocation, guarded by a unique
#: ``(run_id, seq)`` key (ruling R-37). Same shape, same reason, same number.
SEQ_ALLOCATION_ATTEMPTS: Final = 8

#: Attempts for a writer whose only race is two concurrent *first* writes of
#: one key - ``put_blueprint``, ``put_skeleton``, ``put_run``,
#: ``set_step_actual``. Two is enough: the loser's second pass takes the "row
#: exists" branch, which is where it belonged all along. On the last attempt the
#: driver's duplicate-key error is re-raised as itself rather than translated,
#: so a foreign-key violation still names the constraint it broke.
FIRST_WRITE_ATTEMPTS: Final = 2


# --------------------------------------------------------------------------
# pure predicates and orderings
# --------------------------------------------------------------------------


def canonical(value: Any) -> str:
    """A stable text form of a JSON value, for identity comparison (ruling R-29).

    Deliberately a duplicate of ``validation.jsonschemas.canonical`` rather than
    an import of it: `storage/` may import `models/` and nothing else sideways,
    and BP-016's idempotency check needs exactly the
    ``json.dumps(sort_keys=True)`` comparison R-29 names. Three lines duplicated
    across a layer boundary is the cheaper of the two prices.
    """
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def semver_key(version: str) -> tuple[int, tuple[int, ...], str]:
    """Sort key for a version string, newest last.

    BP-002 requires semver, and `service/` has already enforced it by the time a
    row exists - but a store must not raise on data it can be handed, so an
    unparseable version sorts *below* every parseable one and ties break
    lexicographically. Done in Python rather than in the query because
    ``ORDER BY`` on a ``TEXT`` column puts ``1.10.0`` before ``1.9.0``, and no
    portable expression fixes that on any of the three backends.
    """
    head = version.split("-", 1)[0].split("+", 1)[0]
    parts = head.split(".")
    if all(part.isdigit() for part in parts) and parts != [""]:
        return (1, tuple(int(part) for part in parts), version)
    return (0, (), version)


def folded(value: object) -> str:
    """The case-folded form used by the ``q`` filter, on every backend.

    One implementation, in Python, because there is no query expression that
    folds case identically on all three backends: SQLite's ``lower()`` is
    ASCII-only, Postgres's is locale-aware, and Mongo's ``$regex`` with ``i`` is
    a third answer. Folding the *term* in Python and the *column* in SQL - which
    is what M3 shipped first - is the worst of the three, because the two halves
    of one comparison then disagree with each other.

    Verified, not assumed: SQLite returns ``lower('BENGALŪRU')`` as
    ``'bengalŪru'``, while Python returns ``'bengalūru'``. Every current fixture
    is ASCII, so nothing was broken - it was a trap for whoever wrote the first
    non-ASCII dataset.

    ``str.casefold`` rather than ``str.lower``: it is the Unicode operation
    designed for caseless comparison (it folds ``ß`` to ``ss``), and picking the
    weaker one here would be choosing to be subtly wrong on purpose. It is also
    the reason an ``ILIKE`` prefilter on Postgres is **not** a superset of this
    match and therefore cannot be added - see `DECISIONS.md`.
    """
    return str(value).casefold()


def q_matches(term: str, *fields: object) -> bool:
    """Whether ``term`` appears, case-folded, in any of ``fields``.

    ``dataset_find``'s ``q``, as one predicate rather than as one expression per
    adapter. Contracts section 4: "``q`` is a case-folded substring match over
    ``title`` and ``intent``, and substring semantics are the contract on every
    backend (ruling R-36)". A ``%`` is a percent sign and a ``_`` is an
    underscore, which comes free from not being SQL.
    """
    needle = folded(term)
    return any(needle in folded(field) for field in fields)


def slice_page[ItemT](rows: list[ItemT], limit: int | None, offset: int | None) -> list[ItemT]:
    """``limit`` and ``offset`` applied to a list, both optional (ruling R-04).

    Used where the filter cannot be pushed into the query, so ``LIMIT``/``SKIP``
    cannot be either: a page sliced before the filter runs comes back short.
    `service/` owns the defaults, so an omitted ``limit`` means "no limit" here
    rather than a number this layer invented.
    """
    start = offset or 0
    return rows[start:] if limit is None else rows[start : start + limit]


def parse_uuid(value: str) -> uuid.UUID | None:
    """A UUID, or ``None`` for anything that is not one.

    The read path's parser. A malformed id is "no such row", never an exception:
    ``get_dataset``, ``get_skeleton`` and ``find_runs`` all take ids that
    arrived as tool inputs, and ``RunQuery.dataset_id``'s own docstring settles
    the question - "an unparseable filter should return no rows, not raise".
    """
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def require_uuid(value: str, field: str) -> uuid.UUID:
    """A UUID, or :class:`StoreError`. The write path's parser.

    The asymmetry with :func:`parse_uuid` is intentional. On a read a malformed
    id is a miss; on a write it is a value that cannot be stored in a ``UUID``
    column, and the choices are to raise or to drop it. Nothing user-caused
    reaches here: ``provenance.supersedes`` is proved to name a real dataset by
    DS-031, and dataset ids come from ``Seeded.uuid()``.
    """
    parsed = parse_uuid(value)
    if parsed is None:
        raise StoreError(f"{field} is not a UUID: {value!r}")
    return parsed


def labels_of(value: Any) -> dict[str, str]:
    """A ``labels`` value, typed. Stored JSON gives back ``Any``; ``Labels`` is
    ``dict[str, str]`` and the rows were validated before they were written."""
    return {str(key): str(item) for key, item in dict(value).items()}


# --------------------------------------------------------------------------
# model to stored document
# --------------------------------------------------------------------------


def stored_document(model: Blueprint | Dataset, **overrides: Any) -> dict[str, Any]:
    """The JSON document to store for ``model``, with ``overrides`` applied.

    ``exclude_unset=True`` is load-bearing, not a micro-optimisation. Ruling
    R-08 defines the round trip as
    ``model_validate(raw).model_dump(mode="json", exclude_unset=True) == raw``,
    and the golden fixtures omit many optional fields - ``tool_name`` on three
    nodes, ``max_iterations`` on eight, ``input`` on every pool entry. A full
    dump would reintroduce those as ``null`` and the bytes a dataset was
    submitted with would not be the bytes ``dataset_export`` emits, which is
    exactly the byte-stability the cross-backend import gate depends on.

    The overrides are the fields the *store* owns rather than the author: a
    dataset's allocated ``version`` and its lineage-level ``archived`` state
    (ruling R-34), and a blueprint's ``status``. Applying them to the document
    as well as to the column is what makes it impossible for a row and the
    document inside it to disagree - which is the same reason ``set_archived``
    rewrites the document rather than only the column.

    Shared rather than per-adapter for the reason the rest of this module is:
    the *bytes* have to be the same on every backend, because
    ``dataset_export`` on one and ``dataset_import`` on another is a gate,
    and two adapters that each dumped their own way could differ by a single
    reintroduced ``null``.
    """
    document = model.model_dump(mode="json", exclude_unset=True)
    document.update(overrides)
    return document


# --------------------------------------------------------------------------
# record to model
# --------------------------------------------------------------------------


def blueprint_summary(fields: Mapping[str, Any]) -> BlueprintSummary:
    """One row of ``list_blueprints``. ``description`` is read from the document,
    which is the only field the summary needs and no backend promotes."""
    document: Mapping[str, Any] = fields["document"]
    return BlueprintSummary(
        agent_id=str(fields["agent_id"]),
        version=str(fields["version"]),
        status=str(fields["status"]),
        description=str(document.get("description", "")),
    )


def dataset_summary(fields: Mapping[str, Any]) -> DatasetSummary:
    """A ``DatasetSummary`` from the promoted fields.

    Which is what they were promoted for - "so find can filter and sort without
    parsing JSON". The document is read for exactly one field: contracts 2.2.1
    puts a 200-character ``narrative`` excerpt in the summary and no backend
    promotes a column for it.
    """
    document: Mapping[str, Any] = fields["document"]
    narrative = str(document.get("narrative", ""))
    return DatasetSummary(
        id=fields["id"],
        version=int(fields["version"]),
        title=str(fields["title"]),
        intent=str(fields["intent"]),
        labels=labels_of(fields["labels"]),
        author=Author(
            name=str(fields["author_name"]),
            handle=str(fields["author_handle"]),
            agent=str(fields["author_agent"]),
        ),
        blueprint=BlueprintRef(agent_id=str(fields["agent_id"]), version=str(fields["bp_version"])),
        narrative_excerpt=narrative[:NARRATIVE_EXCERPT_CHARS],
        archived=bool(fields["archived"]),
        created_at=fields["created_at"],
    )


def skeleton_from(fields: Mapping[str, Any]) -> Skeleton:
    """A ``Skeleton`` from the promoted fields.

    ``parts`` is keyed by section id, and two of ruling R-06's five section ids
    contain a dot. That is a fact every adapter has to survive and only one has
    to work for: see `storage/mongo.py`, which encodes object keys because Mongo
    cannot dot-path-address such a field.
    """
    return Skeleton(
        id=fields["id"],
        agent_id=str(fields["agent_id"]),
        bp_version=str(fields["bp_version"]),
        labels=labels_of(fields["labels"]),
        seed=int(fields["seed"]),
        manifest=list(fields["manifest"]),
        parts=dict(fields["parts"]),
        submitted_as=fields["submitted_as"],
        created_at=fields["created_at"],
    )


def step_from(fields: Mapping[str, Any]) -> StepRecord:
    """A ``StepRecord`` from the promoted fields."""
    return StepRecord(
        node_id=str(fields["node_id"]),
        iteration=int(fields["iteration"]),
        served=dict(fields["served"]),
        actual=fields["actual"],
        recorded_at=fields["recorded_at"],
        seq=int(fields["seq"]),
        fetched_at=fields["fetched_at"],
    )


def run_from(fields: Mapping[str, Any], steps: Sequence[StepRecord]) -> Run:
    """A ``Run`` from the promoted fields plus its steps, in ``seq`` order.

    ``path`` is **reconstructed** from the steps rather than read from a column:
    no backend stores it, because the ordered sequence of calls carrying a run
    id *is* the traversal, so branch selection is observed rather than declared
    and an agent cannot report a path it did not take.
    """
    ordered = list(steps)
    return Run(
        id=str(fields["id"]),
        agent_id=str(fields["agent_id"]),
        pin=RunPin(
            dataset_id=fields["dataset_id"],
            dataset_version=int(fields["dataset_ver"]),
            blueprint_version=str(fields["bp_version"]),
        ),
        declared_blueprint_version=fields["declared_bp_version"],
        model=None if fields["model"] is None else ModelInfo.model_validate(fields["model"]),
        run_class=str(fields["run_class"]),
        path=[
            PathStep(node_id=step.node_id, iteration=step.iteration, at=at)
            for step in ordered
            if (at := step.fetched_at) is not None
        ],
        steps=ordered,
        outcome=fields["outcome"],
        warnings=[Warning.model_validate(item) for item in fields["warnings"]],
        status=str(fields["status"]),
        started_at=fields["started_at"],
        finished_at=fields["finished_at"],
        external_refs=dict(fields["external_refs"]),
    )


def run_summary(fields: Mapping[str, Any]) -> RunSummary:
    """One row of ``find_runs``: ruling R-05's "``runs`` columns minus ``outcome``"."""
    return RunSummary(
        id=str(fields["id"]),
        agent_id=str(fields["agent_id"]),
        dataset_id=fields["dataset_id"],
        dataset_ver=int(fields["dataset_ver"]),
        bp_version=str(fields["bp_version"]),
        run_class=str(fields["run_class"]),
        model=None if fields["model"] is None else ModelInfo.model_validate(fields["model"]),
        status=str(fields["status"]),
        warnings=[Warning.model_validate(item) for item in fields["warnings"]],
        started_at=fields["started_at"],
        finished_at=fields["finished_at"],
        external_refs=dict(fields["external_refs"]),
    )
