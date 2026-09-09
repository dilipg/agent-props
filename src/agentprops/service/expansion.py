"""``dataset_expand``: grow a pool deterministically, validate, then store.

Ruling R-56 is the whole shape of this module, in one sentence:
``dataset_expand`` is a **write path**, so R-23 applies unchanged - build the
expanded document, validate it, and store only if it passes. An expansion that
would push a loop node's pool past ``max_iterations`` therefore returns
**DS-023** and stores nothing, with ``max_iterations`` and the resulting length
in the finding's ``context`` so the caller can retry with a workable ``count``
rather than guessing.

Two alternatives R-56 rejected, and they are worth restating because both look
like kindnesses. **Silently clamping to ``max_iterations``** breaks the
determinism contract the tool exists to provide: a caller asking for ``count=10``
and receiving 3 without being told has no way to know its output is not what it
requested. **Storing the over-long pool** violates ground rule 7 - a dataset in
the store is trusted by the read path, and writing one that fails its own
catalogue is the one thing the validator exists to prevent.

What an expanded pool entry actually is
---------------------------------------

**A deterministically chosen copy of an authored entry**, with
``latency_hint_ms`` jittered when the template carries one. Not invented
content, and that is a constraint rather than a shortcut: a new fixture has to
satisfy the node's ``output_schema`` (DS-019), the schema is user-supplied JSON
Schema, and generating a conforming instance of an arbitrary schema is the job
of a model - which ground rule 4 forbids anywhere in this service. So the honest
thing expansion can do is what PRD section 4 says it is for: "deterministic
expansion from ``seed`` fills volume, repeated rows and pool entries (cheap,
reproducible). A 10,000-row load-test dataset must not cost 10,000 LLM calls."
Repetition is the feature.

``latency_hint_ms`` is the one field varied, because it is the one field on a
``NodeFixture`` that carries no schema and no semantics the timeline depends on.

What it seeds from, and why that answer
---------------------------------------

Each new entry is derived from ``Seeded(dataset.seed, "expand:<node_id>:<index>")``
where ``index`` is the entry's **final position in the pool** - so an entry is a
pure function of ``(seed, node_id, position)`` and of nothing else. Three
properties follow, and the third is the acceptance criterion:

- **Stable across versions.** Expanding version 1 and then version 2 keeps the
  entries version 1 produced, byte for byte, because their positions did not
  move. That is the property a reviewer reading a lineage depends on, and it is
  what a stream shared across the batch would have destroyed.
- **Reproducible per request.** Repeating a request against the same starting
  dataset reproduces its result exactly.
- **Byte-identical across processes**, because ``Seeded`` is
  ``hashlib``-derived rather than ``hash()``-derived.
  `tests/integration/test_expansion_determinism.py` proves that with three real
  subprocesses under differing ``PYTHONHASHSEED`` values, which is the only way
  to prove it at all.

**Not** idempotent under splitting, and that is a correction rather than an
omission: the first version of this docstring claimed expanding by 5 equalled
expanding by 2 then 3, and the test written to assert it found otherwise. A new
entry is chosen from the pool it is being added to, and after the first call
that pool is longer - so the second call is expanding a *different dataset*, and
a draw over five entries is not a draw over two. The prefix is identical and the
continuation is not. Making them agree would need the store to remember which
entries were *authored* as against generated, and nothing does; the residue is
recorded in `DECISIONS.md` and asserted in
`tests/unit/test_service_expansion.py`, so it is a stated trade rather than a
surprise.

The dataset's own ``seed`` is the source rather than a new argument, because
design principle 3 is "same seed plus same blueprint version yields the same
dataset" - a per-call seed would make one dataset's pool depend on which call
grew it, and the dataset already carries the value that everything else about
it was derived from (DS-020 owns it).

Copy-on-write, like every other dataset edit
--------------------------------------------

The expanded document keeps the dataset's ``id`` and gets a new ``version``
from the store, so the pre-expansion version stays readable and a run pinned to
it keeps reading it. ``archived`` is inherited from the lineage (ruling R-34)
and ``created_at`` stays the authored one (ruling R-09), so an expansion does
not move a dataset in ``dataset_find``'s ordering.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any, Final

from agentprops.expansion.seeded import Seeded
from agentprops.models import (
    SEVERITY_ERROR,
    WARNING_DATASET_ARCHIVED,
    Dataset,
    RuleError,
    Warning,
)
from agentprops.service.context import ServiceContext
from agentprops.service.documents import parse
from agentprops.service.envelope import (
    AP_ARGUMENT,
    AP_NOT_FOUND,
    AP_STORE_REFUSED,
    Reply,
    blocking,
    boundary,
    failure,
    field_pointer,
    not_found,
    success,
    warning,
    warnings_from,
)
from agentprops.service.limits import clamp
from agentprops.storage import RecordNotFoundError, StoreError
from agentprops.validation import validate_dataset

__all__ = [
    "MAX_EXPAND_COUNT",
    "WARNING_EXPANSION_ADDED_NOTHING",
    "expand",
    "expansion_salt",
    "jittered_range",
]

#: The most entries one ``dataset_expand`` call may add.
#:
#: A **stated** bound, never a silent one, which is the distinction ruling R-56
#: cares about: it is reported as ``AP-001`` naming the maximum, so a caller is
#: told rather than quietly served something smaller. It exists because DS-023
#: cannot be the only bound - DS-023 caps a *loop* node's pool at
#: ``max_iterations``, and contracts section 2.1 lets a ``pool: true`` node not
#: be a loop node at all (ruling R-51), so nothing in the catalogue stops
#: ``count = 2**62`` from being *attempted*. R-23 requires the document be built
#: before it is validated, and building 2**62 fixtures is not a validation
#: failure, it is an out-of-memory.
#:
#: Ten thousand because PRD section 4 names "a 10,000-row load-test dataset" as
#: the volume expansion exists for; a caller who wants more makes a second call,
#: and gets the same entries either way because an entry is addressed by its
#: position rather than drawn from a batch.
MAX_EXPAND_COUNT: Final = 10_000

#: Attached when a well-formed request adds no entries - ``count`` of zero, or
#: a negative ``count`` clamped to zero.
#:
#: A warning rather than an ``AP-001``, because the argument is well formed and
#: the service does not refuse well-formed requests: the caller gets the
#: unchanged dataset, is told that nothing was added, and no version is burned
#: on a byte-identical copy. ``Warning.code`` is an open string precisely so a
#: tool can add one (ruling R-22); documented in contracts section 3.4 and
#: asserted by `tests/unit/test_validation_drift.py`.
WARNING_EXPANSION_ADDED_NOTHING: Final = "expansion_added_nothing"


def expansion_salt(node_id: str, index: int) -> str:
    """The ``Seeded`` salt for the pool entry at ``index`` of ``node_id``.

    Public and named, because it is the whole determinism contract in one line
    and both the property test and the cross-process test assert against it.
    Change it and every previously expanded entry stops being re-derivable from
    its dataset's seed - the same class of consequence ``Seeded.uuid``'s pinned
    derivation carries, for the same reason.
    """
    return f"expand:{node_id}:{index}"


def expand(context: ServiceContext, dataset_id: str, node_id: str, count: int) -> Reply:
    """Add ``count`` deterministic entries to ``node_id``'s pool. See the module docstring.

    The order is R-23's and R-56's: resolve, build, **validate the raw
    document**, parse, stamp, store. Nothing is written unless the whole
    document passes every ``DS-*`` rule, which is what makes DS-023 the answer
    to an over-long pool rather than a corrupt row.
    """
    if count > MAX_EXPAND_COUNT:
        return failure([_too_many(count)])
    wanted = clamp(count, high=MAX_EXPAND_COUNT)
    dataset = context.store.get_dataset(dataset_id, None)
    if dataset is None:
        return not_found("dataset", field="dataset_id", dataset_id=dataset_id)

    document: dict[str, Any] = dataset.model_dump(mode="json", exclude_unset=True)
    notices = _archive_notices(dataset)
    if wanted == 0:
        return success(
            "dataset",
            document,
            [
                *notices,
                warning(
                    WARNING_EXPANSION_ADDED_NOTHING,
                    dataset_id=str(dataset.id),
                    node_id=node_id,
                    requested=count,
                ),
            ],
        )

    pools: dict[str, list[dict[str, Any]]] = dict(document.get("pools", {}))
    templates = pools.get(node_id) or []
    if not templates:
        return failure(_unexpandable(context, document, node_id))

    pools[node_id] = [*templates, *_grown(dataset.seed, node_id, templates, wanted)]
    document["pools"] = pools
    return _validate_then_store(context, document, notices)


def _validate_then_store(
    context: ServiceContext, document: dict[str, Any], notices: Sequence[Warning]
) -> Reply:
    """Ruling R-56's three steps, in the one order that satisfies R-23 and R-7.

    Validate the **raw** document, then parse it, then write. DS-023 is reported
    from here like any other finding, carrying the ``max_iterations`` and
    ``pool_length`` its rule already puts in ``context`` - which is what R-56
    asks for so a caller can retry with a workable ``count``.
    """
    findings = validate_dataset(document, context.resolver).errors
    if blocking(findings):
        return failure(findings)
    model, shape_findings = parse(Dataset, document)
    if model is None:
        return failure(findings + shape_findings)
    stamped = model.model_copy(update={"validated_at": context.clock.now()})
    return _write_expanded(context, stamped, findings, notices)


def _write_expanded(
    context: ServiceContext,
    model: Dataset,
    findings: list[RuleError],
    notices: Sequence[Warning],
) -> Reply:
    """The one store write on this path, with the adapters' guards translated.

    Copy-on-write: ``put_dataset`` allocates the next version of the same
    lineage, so the pre-expansion version stays readable and a pinned run is
    untouched. A ``StoreError`` here means a guard fired that a rule should have
    caught first, which is what ``AP-005`` says.
    """
    try:
        stored = context.store.put_dataset(model)
    except (RecordNotFoundError, StoreError) as exc:
        return failure([boundary(AP_STORE_REFUSED, field_pointer("dataset_id"), str(exc))])
    document: dict[str, Any] = stored.model_dump(mode="json", exclude_unset=True)
    return success("dataset", document, [*notices, *warnings_from(findings)])


def _grown(
    seed: int, node_id: str, templates: Sequence[Mapping[str, Any]], count: int
) -> list[dict[str, Any]]:
    """``count`` new entries, each addressed by its final position in the pool.

    One ``Seeded`` per position rather than one stream for the batch, which is
    the decision the module docstring argues for: an entry is then a pure
    function of ``(seed, node_id, position)`` **and of the templates it was
    drawn from**, so re-expanding a later version keeps the entries an earlier
    one produced - their positions did not move.

    It does **not** make expanding by 5 equal to expanding by 2 then 3, and the
    module docstring records why: ``templates`` is the pool as it stands at the
    start of the call, so the second call draws from a longer list. The prefix
    is preserved; the continuation is not. An earlier version of this docstring
    claimed otherwise and
    ``test_a_split_expansion_keeps_the_prefix_and_may_diverge_after_it`` is what
    disproved it.
    """
    start = len(templates)
    return [
        _replicated(Seeded(seed, expansion_salt(node_id, index)), templates)
        for index in range(start, start + count)
    ]


def _replicated(source: Seeded, templates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One authored entry, chosen deterministically, with its latency jittered.

    A deep copy, because the entries share nothing: a shallow one would put the
    same ``output`` object in several pool slots, and the next edit to one of
    them would silently change the others.

    ``latency_hint_ms`` is the only field varied. It is the one field on a
    ``NodeFixture`` that no schema constrains (DS-019 validates ``output``
    against ``output_schema`` and ``input`` against ``input_schema``) and that
    the entity timeline does not depend on, so varying it cannot make a
    conforming fixture non-conforming. Everything else is copied verbatim, for
    the reason the module docstring gives: inventing schema-conforming content
    needs a model, and ground rule 4 forbids one here.

    The range is :func:`jittered_range`, which is a named function rather than
    an expression here because its two clamps are the whole of it and both have
    a boundary worth testing exactly.
    """
    entry = copy.deepcopy(dict(source.choice(templates)))
    hint = entry.get("latency_hint_ms")
    if isinstance(hint, int) and not isinstance(hint, bool):
        entry["latency_hint_ms"] = source.int(*jittered_range(hint))
    return entry


def jittered_range(hint: int) -> tuple[int, int]:
    """``(lo, hi)`` for a ``latency_hint_ms`` of ``hint``: half to double, clamped.

    Public and named because the two clamps are the only arithmetic in this
    module and each prevents a different failure that a behavioural test
    catches only by luck:

    - **the top** keeps the range inside what a column can hold. A hint at
      ``MAX_STORED_INT`` doubles out of it, and the doubled value is the draw's
      upper bound - so without the clamp the expansion can write a
      ``latency_hint_ms`` no backend can store, which is ruling R-50's failure
      one layer in from the boundary. Whether it *does* depends on where the
      draw lands, which is exactly why the range is tested rather than the
      value.
    - **the bottom** keeps the range from inverting. A negative hint - nonsense,
      but nothing forbids it, because ruling R-04 keeps value constraints out of
      the models and no ``DS-*`` rule has an opinion about this field - would
      give ``lo > hi`` and raise ``ValueError`` out of ``Seeded.int``: an
      exception where an envelope belongs.

    ``lo <= hi`` for every ``int``, which is the property
    `tests/unit/test_service_expansion.py` asserts over generated hints rather
    than over the four an author thought of.
    """
    return clamp(hint // 2), clamp(hint * 2)


def _unexpandable(
    context: ServiceContext, document: Mapping[str, Any], node_id: str
) -> list[RuleError]:
    """Why ``node_id`` has no pool to expand, **answered by the catalogue**.

    Expansion replicates authored entries, so a node with none cannot be
    expanded - and the reason is always a rule the catalogue already owns:
    DS-018 for a node that is not ``pool: true`` (or is not a node at all), and
    DS-019 for a pool with no fixtures. So the document is validated with an
    empty pool present for that node and those findings are returned, rather
    than this module inventing a message for a condition a rule id already
    names.

    The ``AP-004`` fallback is defence in depth for a state neither rule
    reports, and it is deliberately last: an envelope with no findings says
    nothing at all, which is the one answer worse than the wrong rule id.
    """
    probe = {**document, "pools": {**document.get("pools", {}), node_id: []}}
    findings = [
        finding
        for finding in validate_dataset(probe, context.resolver).errors
        if finding.severity == SEVERITY_ERROR
    ]
    if findings:
        return findings
    return [  # pragma: no cover - DS-018 or DS-019 always reports on this probe
        boundary(
            AP_NOT_FOUND,
            field_pointer("node_id"),
            f"{node_id!r} has no authored pool to expand.",
            node_id=node_id,
        )
    ]


def _archive_notices(dataset: Dataset) -> list[Warning]:
    """A ``dataset_archived`` warning when the lineage is archived, and no refusal.

    The service never gates (ground rule 3), and ``dataset_get`` sets the
    precedent: an archived dataset is served with a warning. Expanding one is a
    reasonable thing to do to a dataset that has been retired from discovery but
    is still pinned by old runs, and refusing would be a policy judgement this
    layer does not get to make.
    """
    if not dataset.archived:
        return []
    return [warning(WARNING_DATASET_ARCHIVED, dataset_id=str(dataset.id), version=dataset.version)]


def _too_many(count: int) -> RuleError:
    """``AP-001`` for a ``count`` above :data:`MAX_EXPAND_COUNT`.

    Named as a bound rather than reported as a clamp, which is R-56's
    requirement read one level out: the ruling forbids silently serving a
    smaller expansion than the caller asked for, and a clamp here would do
    exactly that.
    """
    return boundary(
        AP_ARGUMENT,
        field_pointer("count"),
        f"count must be at most {MAX_EXPAND_COUNT} in one call (got {count}).",
        argument="count",
        requested=count,
        maximum=MAX_EXPAND_COUNT,
    )
