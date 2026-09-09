"""The runtime read path: ``run_start``, ``fetch_step``, ``run_get``, ``run_find``.

This module is where PRD 5.6 - "the runtime is read-only" - is either true or
false. Everything in it reads a blueprint and a dataset and writes **only** the
run: ``put_run`` for the run row and ``upsert_step`` for the step that was
served. There is no call to ``put_dataset`` or ``set_archived`` anywhere on
these paths, and that is asserted mechanically rather than claimed here -
`tests/unit/test_runtime_is_read_only.py` walks the call graph of every entry
point in this module and fails on any store mutator that is not one of the
three run writes.

The pin, and why every read goes through it
-------------------------------------------

``run_start`` records ``{dataset_id, dataset_version, blueprint_version}`` and
every later ``fetch_step`` in that run resolves against those three values and
nothing else. Dataset edits are copy-on-write, so a run in flight keeps reading
exactly what it started with; an archived dataset stays servable to a run
holding a pin to it (with a ``dataset_archived`` warning) while disappearing
from ``dataset_find``. Two consequences PRD 5.6 draws from that are visible in
this file: no run can corrupt what another run reads, and N runs can share one
dataset with no coordination, because nobody writes to it.

Idempotency, which is the milestone's gate
------------------------------------------

``fetch_step`` is idempotent on ``(run_id, resolved_node_id, iteration)``. The
second call for a key returns the **stored** ``served`` document - not a freshly
drawn one that happens to be equal - and performs no write at all, so no path
entry is appended and no ``seq`` is allocated. :func:`_serve` is the one place
that decides, and both of its returns hand back a *stored* record: the replay
branch reads the step off the run, and the fresh branch returns whatever
``upsert_step`` gives back, which is the existing row if a concurrent caller
served the same key first. A retry an hour or a week later resolves to the same
fixture because the run id is the primary key of a permanently stored record.

Repeat-and-warn, never a gate (ruling R-03)
-------------------------------------------

Iteration N of a pool node draws ``pools[node_id][N]``. For **every** index at
or past the end of the pool the last entry repeats and a ``pool_exhausted``
warning is attached - unconditionally, with no reference to
``max_iterations``, which this module never reads. RT-E05 was deleted by ruling
R-03 because PRD 5.2 states the reasoning: "an agent that loops one extra time
should not get a hard failure for a reason the developer never chose". The
golden ``request_docs`` pool has **two** entries, so iteration 0 draws
``pools[0]``, iteration 1 draws ``pools[1]``, and iteration 2 already repeats
``pools[1]`` and warns (ruling R-52 corrects `worked-example.md` section 7,
which says 3).

The complement is a caller error rather than a draw: a **negative** iteration,
an iteration outside the range a column can hold, and a non-zero iteration
against a ``pool: false`` node are all RT-E02. R-51 settles the last piece of
the draw - ``pool: true`` on a non-loop node draws exactly like a loop node, so
nothing here reads ``kind`` either.

Warnings go on the response *and* on the run
--------------------------------------------

Contracts 3.4 says warnings are "attached to both the response and the stored
run", and PRD 5.2 says a drawing-past-the-pool run "is flagged". So
:func:`_flag` merges anything new onto ``run.warnings``. It writes only when
the merge actually adds something, which is what keeps "a repeated fetch
advances nothing" literally true - a replay recomputes the same
``(code, detail)`` pair, finds it already recorded, and issues no write.
"""

from __future__ import annotations

import re
from typing import Any, Final
from uuid import UUID

from agentprops.models import (
    RT_E02,
    RT_E03,
    RT_E04,
    WARNING_BLUEPRINT_VERSION_MISMATCH,
    WARNING_DATASET_ARCHIVED,
    WARNING_POOL_EXHAUSTED,
    Blueprint,
    Dataset,
    DatasetQuery,
    ModelInfo,
    NodeFixture,
    RuleError,
    Run,
    RunPin,
    RunQuery,
    StepRecord,
    Warning,
)
from agentprops.service.context import ServiceContext
from agentprops.service.documents import parse
from agentprops.service.envelope import (
    AP_ARGUMENT,
    AP_NOT_FOUND,
    Reply,
    boundary,
    failure,
    field_pointer,
    runtime,
    success,
    warning,
)
from agentprops.service.limits import page, storable
from agentprops.service.resolution import resolve

__all__ = [
    "DEFAULT_RUN_CLASS",
    "RUN_CLASSES",
    "RUN_ID_MAX_LENGTH",
    "RUN_ID_MIN_LENGTH",
    "RUN_ID_PATTERN",
    "STATUS_RUNNING",
    "WARNING_DATASET_SELECTION_AMBIGUOUS",
    "fetch_step",
    "find",
    "get",
    "paginate",
    "start",
]

#: contracts 2.3: "a client-supplied opaque string, 8 to 128 characters,
#: ``^[A-Za-z0-9_.:-]+$``". Checked here because the service *stores* the id and
#: nothing downstream can repair a run nobody can address; the model carries no
#: pattern, per ruling R-04.
RUN_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9_.:-]+$")
RUN_ID_MIN_LENGTH: Final = 8
RUN_ID_MAX_LENGTH: Final = 128

#: contracts 2.3's ``run_class`` vocabulary. Closed, and no rule id owns it, so
#: an unknown value is an ``AP-001`` at the argument.
RUN_CLASSES: Final[frozenset[str]] = frozenset({"dev", "eval", "load"})

#: What ``run_class`` defaults to. The default lives here rather than in each
#: storage adapter (``Run.run_class``'s own docstring asks for that).
DEFAULT_RUN_CLASS: Final = "dev"

#: A run's status the moment ``run_start`` returns. ``run_finish`` moves it at
#: M8; nothing in phase 1 reads it, because the service never gates.
STATUS_RUNNING: Final = "running"

#: Attached when a ``{labels}`` selector matched more than one dataset, naming
#: how many and which one was assigned. ``Warning.code`` is an open string
#: (ruling R-22) and M4 set the precedent with ``blueprint_version_missing``.
#: Selection is *assignment*, not reservation (PRD 5.6 point 2), so matching
#: several datasets is not an error - but a caller whose label query was
#: broader than they thought has no other way to find out.
WARNING_DATASET_SELECTION_AMBIGUOUS: Final = "dataset_selection_ambiguous"


def start(
    context: ServiceContext,
    run_id: str,
    agent_id: str,
    selector: dict[str, Any],
    declared_blueprint_version: str | None = None,
    *,
    model: dict[str, Any] | None = None,
    run_class: str | None = None,
) -> Reply:
    """Pin a dataset to a run and record it. ``{run, pin}`` plus warnings.

    Four steps, and the second one is the reason the run id is an idempotency
    key rather than just a name:

    1. **check the arguments** - the run id's shape, the ``run_class``
       vocabulary, the selector's one-of-two shape, and the ``model`` object,
       all reported together the way ``ArgReader`` reports argument findings;
    2. **replay an existing run.** A run id that already exists is returned
       **unchanged**, with no write: the pin is immutable for the run's whole
       life (PRD 5.6), so re-resolving the selector could hand a run that has
       already been served from version 1 a pin to version 2. A client retrying
       ``run_start`` after a network timeout gets the same answer, and a client
       that changed its mind about the selector can see in the response's
       ``pin`` which dataset it actually has;
    3. **select the dataset** - by explicit id, or by label query. See
       :func:`_select`;
    4. **compare versions and store.** A ``declared_blueprint_version`` other
       than the pinned one is a ``blueprint_version_mismatch`` warning on the
       response *and* on the stored run, and the dataset is served anyway. The
       service never gates: "the developer is told loudly and decides for
       themselves" (PRD 5.4).
    """
    findings = _argument_findings(run_id, run_class, selector)
    info, model_findings = (None, []) if model is None else parse(ModelInfo, model)
    findings += model_findings
    if findings:
        return failure(findings)

    existing = context.store.get_run(run_id)
    if existing is not None:
        return _started(existing)

    pin, unresolved, warnings = _select(context, agent_id, selector)
    if pin is None:
        return failure(unresolved)
    warnings += _mismatch(pin, declared_blueprint_version)

    run = Run(
        id=run_id,
        agent_id=agent_id,
        pin=pin,
        declared_blueprint_version=declared_blueprint_version,
        model=info,
        run_class=run_class or DEFAULT_RUN_CLASS,
        status=STATUS_RUNNING,
        started_at=context.clock.now(),
        warnings=warnings,
    )
    return _started(context.store.put_run(run), warnings)


def fetch_step(
    context: ServiceContext,
    run_id: str,
    *,
    node_id: str | None = None,
    tool_name: str | None = None,
    iteration: int | None = None,
) -> Reply:
    """The fixture for one step of one run. ``{fixture, resolved_node_id}``.

    Reads the pinned blueprint and the pinned dataset, resolves the step
    identity through `resolution.py`, draws the fixture, and records **to the
    run** that it was served. Nothing on this path writes a dataset.

    Idempotent on ``(run_id, resolved_node_id, iteration)``: see the module
    docstring and :func:`_serve`.
    """
    index, bad = _iteration(iteration)
    if bad:
        return failure(bad)

    run = context.store.get_run(run_id)
    if run is None:
        return failure(
            [runtime(RT_E03, field_pointer("run_id"), f"no run {run_id!r}.", run_id=run_id)]
        )

    blueprint = context.store.get_blueprint(run.agent_id, run.pin.blueprint_version)
    if blueprint is None:  # pragma: no cover - DS-001 plus BP-016 make this unreachable
        return failure([_no_blueprint(run)])

    resolved, findings = resolve(blueprint, run, node_id=node_id, tool_name=tool_name)
    if resolved is None:
        return failure(findings)

    dataset = context.store.get_dataset(str(run.pin.dataset_id), run.pin.dataset_version)
    if dataset is None:  # pragma: no cover - no hard delete, so a pin cannot dangle
        return failure([_no_dataset(run)])

    fixture, warnings, missing = _draw(blueprint, dataset, resolved, index)
    if fixture is None:
        return failure(missing)
    if dataset.archived:
        warnings = [
            warning(WARNING_DATASET_ARCHIVED, dataset_id=str(dataset.id), version=dataset.version),
            *warnings,
        ]

    served = _serve(context, run, resolved, index, fixture)
    _flag(context, run, warnings)
    return success("step", {"fixture": served, "resolved_node_id": resolved}, warnings)


def get(context: ServiceContext, run_id: str) -> Reply:
    """One run in full, with its steps and its reconstructed path.

    The order of ``steps`` and ``path`` is the store's ``(seq, node_id,
    iteration)`` - total, per ruling R-37 - and is **not** re-sorted here. A
    Python ``sorted()`` on this list would undo the one ordering that step
    resolution depends on, and it would do so identically on every backend
    until it did not.
    """
    run = context.store.get_run(run_id)
    if run is None:
        return failure(
            [runtime(RT_E03, field_pointer("run_id"), f"no run {run_id!r}.", run_id=run_id)]
        )
    return success("run", run.model_dump(mode="json"))


def find(context: ServiceContext, query: RunQuery) -> Reply:
    """Run summaries, newest first. ``(started_at DESC, id)``, per ruling R-35.

    Ordered by the store and not re-sorted here, for the reason
    ``dataset_find`` is not either: the tie-break on ``id`` is what makes the
    order total, and a re-sort in Python would quietly drop it.
    """
    rows = context.store.find_runs(paginate(query))
    return success("runs", [row.model_dump(mode="json") for row in rows])


def paginate(query: RunQuery) -> RunQuery:
    """``query`` with ``limit`` and ``offset`` defaulted and clamped.

    The same two decisions ``dataset_find`` makes, from the same place -
    :func:`~agentprops.service.limits.page` - rather than a second copy of
    them. ``limit`` and ``offset`` reach an integer column, so ruling R-50
    applies and both ends of the range are clamped.
    """
    limit, offset = page(query.limit, query.offset)
    return query.model_copy(update={"limit": limit, "offset": offset})


# ------------------------------------------------------------------ arguments


def _argument_findings(
    run_id: str, run_class: str | None, selector: dict[str, Any]
) -> list[RuleError]:
    """Everything wrong with ``run_start``'s arguments, in one pass.

    Collected rather than short-circuited, the same way ``ArgReader`` collects,
    so a caller with two bad arguments learns about both.
    """
    findings: list[RuleError] = []
    if not _run_id_ok(run_id):
        findings.append(
            boundary(
                AP_ARGUMENT,
                field_pointer("run_id"),
                f"run_id must be {RUN_ID_MIN_LENGTH} to {RUN_ID_MAX_LENGTH} characters matching "
                f"{RUN_ID_PATTERN.pattern}.",
                argument="run_id",
                run_id=run_id,
            )
        )
    if run_class is not None and run_class not in RUN_CLASSES:
        findings.append(
            boundary(
                AP_ARGUMENT,
                field_pointer("run_class"),
                f"run_class must be one of {sorted(RUN_CLASSES)}.",
                argument="run_class",
                run_class=run_class,
            )
        )
    return findings + _selector_findings(selector)


def _run_id_ok(run_id: str) -> bool:
    """contracts 2.3's shape, and nothing more. Opaque otherwise."""
    return (
        RUN_ID_MIN_LENGTH <= len(run_id) <= RUN_ID_MAX_LENGTH
        and RUN_ID_PATTERN.match(run_id) is not None
    )


def _selector_findings(selector: dict[str, Any]) -> list[RuleError]:
    """``selector`` is ``{dataset_id}`` **or** ``{labels}`` - exactly one.

    An unknown key is refused rather than ignored: ``{"label": {...}}`` would
    otherwise be indistinguishable from an empty selector, and the caller would
    be told "give one of two keys" while looking at a selector that has one.
    """
    keys = set(selector)
    known = {"dataset_id", "labels"}
    findings: list[RuleError] = [
        boundary(
            AP_ARGUMENT,
            field_pointer("selector", key),
            "selector takes dataset_id or labels, and nothing else.",
            argument="selector",
            unknown_key=key,
        )
        for key in sorted(keys - known)
    ]
    chosen = keys & known
    if len(chosen) != 1:
        findings.append(
            boundary(
                AP_ARGUMENT,
                field_pointer("selector"),
                "selector must carry exactly one of dataset_id or labels.",
                argument="selector",
                given=sorted(keys),
            )
        )
        return findings
    if "dataset_id" in chosen and not isinstance(selector["dataset_id"], str):
        findings.append(
            boundary(
                AP_ARGUMENT,
                field_pointer("selector", "dataset_id"),
                "selector.dataset_id must be a string.",
                argument="selector",
                given_type=type(selector["dataset_id"]).__name__,
            )
        )
    if "labels" in chosen:
        findings += _label_findings(selector["labels"])
    return findings


def _label_findings(labels: object) -> list[RuleError]:
    """A ``{dimension: value}`` object of strings, pointed at the bad dimension.

    The same shape ``ArgReader.labels`` reports, and for the same reason: the
    one thing the caller has to change is which dimension, not the whole
    selector.
    """
    if not isinstance(labels, dict):
        return [
            boundary(
                AP_ARGUMENT,
                field_pointer("selector", "labels"),
                "selector.labels must be an object of string label values.",
                argument="selector",
                given_type=type(labels).__name__,
            )
        ]
    return [
        boundary(
            AP_ARGUMENT,
            field_pointer("selector", "labels", str(dimension)),
            "a label value must be a string.",
            argument="selector",
            given_type=type(value).__name__,
        )
        for dimension, value in labels.items()
        if not (isinstance(dimension, str) and isinstance(value, str))
    ]


def _iteration(iteration: int | None) -> tuple[int, list[RuleError]]:
    """The iteration index to draw, or the finding that says it addresses nothing.

    Omitted means 0. A **negative** value is RT-E02 by ruling R-03, and a value
    outside the range a column can hold is the same answer for the same reason:
    ``iteration`` is an *identifier* of a step within a run, not a quantity, so
    it is refused rather than clamped (the M4 fix-round entry names ``version``
    and "M6's ``iteration``" as the two identifiers this applies to). Without
    the range check the value reaches ``upsert_step``, pysqlite raises
    ``OverflowError``, and the caller gets a protocol error instead of an
    envelope - which is ruling R-50's whole subject, and
    `tests/unit/test_bounded_integers.py` is what enforces it.

    This is **not** a cap on iteration count and R-03 is untouched: every
    storable index at or past the pool's end serves ``pools[-1]`` and warns.
    ``MAX_STORED_INT`` is served; only a value that no column could hold is
    refused, and such a value cannot be a loop that happened.
    """
    index = 0 if iteration is None else iteration
    if index < 0 or not storable(index):
        return 0, [
            runtime(
                RT_E02,
                field_pointer("iteration"),
                f"iteration {index} addresses no step: it must be zero or a positive integer no "
                f"wider than 64 bits.",
                iteration=index,
            )
        ]
    return index, []


# ------------------------------------------------------------------ selection


def _select(
    context: ServiceContext, agent_id: str, selector: dict[str, Any]
) -> tuple[RunPin | None, list[RuleError], list[Warning]]:
    """The pin this run will hold for its whole life.

    ``{dataset_id}`` and ``{labels}`` are deliberately asymmetric about
    archives, and the asymmetry is the same one ``dataset_get`` and
    ``dataset_find`` already have:

    - an **explicit id** serves an archived dataset, with a
      ``dataset_archived`` warning. The caller named it, the service never
      gates, and PRD 5.6 keeps an archived dataset "servable to any run holding
      a pin to it";
    - a **label query** goes through ``find_datasets``, which excludes
      archives in its ``WHERE`` clause. Archiving is how an author retires a
      dataset from the suite, so discovery must not hand one out - that would
      make archive meaningless for the case it exists for.
    """
    if "dataset_id" in selector:
        return _by_id(context, agent_id, str(selector["dataset_id"]))
    return _by_labels(context, agent_id, dict(selector["labels"]))


def _by_id(
    context: ServiceContext, agent_id: str, dataset_id: str
) -> tuple[RunPin | None, list[RuleError], list[Warning]]:
    """The latest version of an explicitly named dataset.

    A dataset authored against a **different** agent is RT-E04 rather than a
    served run: its node ids come from another graph, so every ``fetch_step``
    would report RT-E02 and the run would be unplayable. Reporting that at
    ``run_start`` is resolution, not policy - there is no dataset for *this*
    agent with that id.
    """
    dataset = context.store.get_dataset(dataset_id, None)
    if dataset is None:
        return None, [_dataset_missing(dataset_id, agent_id)], []
    if dataset.blueprint.agent_id != agent_id:
        return None, [_wrong_agent(dataset, agent_id)], []
    warnings = (
        [warning(WARNING_DATASET_ARCHIVED, dataset_id=str(dataset.id), version=dataset.version)]
        if dataset.archived
        else []
    )
    return _pin(dataset.id, dataset.version, dataset.blueprint.version), [], warnings


def _by_labels(
    context: ServiceContext, agent_id: str, labels: dict[str, str]
) -> tuple[RunPin | None, list[RuleError], list[Warning]]:
    """The **first** row of a label query, which is the oldest match.

    Selection is *assignment*, not reservation (PRD 5.6 point 2): nobody writes
    to a dataset, so N runs may share one and there is nothing to lock. That
    leaves only the question of which match to assign, and phase 1 answers it
    deterministically - ``find_datasets`` orders by ``(created_at, id)``, which
    ruling R-35 makes total, so "the first row" is the same dataset on every
    backend and on every repeat of the same query. A round-robin or random
    strategy is the phase-2 work PRD 5.6 describes, and both would make an
    identical request answer differently.

    Matching several datasets is not an error, so the run starts - with
    :data:`WARNING_DATASET_SELECTION_AMBIGUOUS` naming how many matched and
    which one was assigned.
    """
    rows = context.store.find_datasets(DatasetQuery(agent_id=agent_id, labels=labels))
    if not rows:
        return None, [_no_match(agent_id, labels)], []
    chosen = rows[0]
    warnings = (
        [
            warning(
                WARNING_DATASET_SELECTION_AMBIGUOUS,
                matched=len(rows),
                assigned=str(chosen.id),
                labels=labels,
                strategy="first by (created_at, id)",
            )
        ]
        if len(rows) > 1
        else []
    )
    return _pin(chosen.id, chosen.version, chosen.blueprint.version), [], warnings


def _pin(dataset_id: UUID, version: int, blueprint_version: str) -> RunPin:
    """The three values a run holds for its whole life."""
    return RunPin(
        dataset_id=dataset_id, dataset_version=version, blueprint_version=blueprint_version
    )


def _mismatch(pin: RunPin, declared: str | None) -> list[Warning]:
    """``blueprint_version_mismatch``, and nothing else, when the agent disagrees.

    "If the agent declares a different version, the service emits a
    ``blueprint_version_mismatch`` warning on the response and on the stored
    run, and then serves the dataset anyway" (PRD 5.4). Both halves are here:
    the warning goes onto the ``Run`` model, so ``put_run`` stores it, and the
    same list rides back on the response.
    """
    if declared is None or declared == pin.blueprint_version:
        return []
    return [
        warning(
            WARNING_BLUEPRINT_VERSION_MISMATCH,
            declared=declared,
            pinned=pin.blueprint_version,
            dataset_id=str(pin.dataset_id),
        )
    ]


def _started(run: Run, warnings: list[Warning] | None = None) -> Reply:
    """``{run, pin}`` under one named key, with the warnings on the envelope.

    contracts section 4 documents the payload as ``{run, pin, warnings}``;
    ``warnings`` is the envelope's own list (M4's convention) and ``pin`` is
    carried alongside the run as well as inside it, because that is the field a
    caller reads to learn what it was given.

    A replay - step 2 of :func:`start` - passes no ``warnings``, so the stored
    run's own list is what comes back. That keeps a mismatch recorded at first
    start visible on every retry rather than only on the first response.
    """
    return success(
        "start",
        {"run": run.model_dump(mode="json"), "pin": run.pin.model_dump(mode="json")},
        warnings if warnings is not None else run.warnings,
    )


# ----------------------------------------------------------------- the fixture


def _draw(
    blueprint: Blueprint, dataset: Dataset, node_id: str, iteration: int
) -> tuple[dict[str, Any] | None, list[Warning], list[RuleError]]:
    """The fixture for ``(node_id, iteration)``, plus any ``pool_exhausted``.

    Which branch is taken depends on the blueprint node's ``pool`` flag and on
    **nothing else**. Ruling R-51: the pool mechanism is orthogonal to ``kind``,
    so a ``pool: true`` node that is not a loop draws exactly like one, and this
    function never reads ``kind`` or ``max_iterations``.
    """
    node = next(candidate for candidate in blueprint.nodes if candidate.id == node_id)
    if node.pool:
        pool = dataset.pools.get(node_id, [])
        if not pool:  # pragma: no cover - DS-018/DS-019 make this unstorable
            return None, [], [_no_fixture(dataset, node_id, iteration)]
        index = min(iteration, len(pool) - 1)
        exhausted = (
            [
                warning(
                    WARNING_POOL_EXHAUSTED,
                    node_id=node_id,
                    iteration=iteration,
                    pool_length=len(pool),
                    served_index=index,
                )
            ]
            if iteration >= len(pool)
            else []
        )
        return _fixture(pool[index]), exhausted, []

    if iteration != 0:
        return None, [], [_not_a_pool(node_id, iteration)]
    fixture = dataset.nodes.get(node_id)
    if fixture is None:  # pragma: no cover - DS-002 makes this unstorable
        return None, [], [_no_fixture(dataset, node_id, iteration)]
    return _fixture(fixture), [], []


def _fixture(fixture: NodeFixture) -> dict[str, Any]:
    """The authored fixture document, verbatim.

    ``exclude_unset`` per ruling R-08, which is what makes the served bytes the
    *authored* bytes: the golden datasets omit ``input`` on every pool entry and
    ``latency_hint_ms`` on several nodes, and a full dump would hand the agent
    those keys as ``null``. PRD design principle 2 - hold the environment
    byte-identical - is the basis of the product's drift claim, so the read path
    hands over what the author wrote and computes nothing.

    In particular ``entity_refs`` is served **unresolved**. Resolving
    ``store@after_docs`` into the entity's state here would make the served
    document a thing the service computed rather than a thing the author wrote,
    and the dataset already encodes that timeline explicitly (PRD 5.6 point 4).
    """
    document: dict[str, Any] = fixture.model_dump(mode="json", exclude_unset=True)
    return document


def _serve(
    context: ServiceContext, run: Run, node_id: str, iteration: int, fixture: dict[str, Any]
) -> dict[str, Any]:
    """Record that ``fixture`` was served, and return what is **stored**.

    The idempotency gate, and the only place it is decided. Both branches
    return a stored document rather than the one just drawn:

    - the step key is already on the run: return its ``served`` and write
      **nothing**. No row, no ``seq``, no path entry - "fetching the same step
      key twice returns byte-identical fixtures and advances nothing";
    - the step key is new: ``upsert_step`` allocates ``seq`` and returns the
      row it wrote *or the row that was already there*, because it is
      idempotent on ``(run_id, node_id, iteration)`` at the storage layer. So
      even the loser of a concurrent first fetch answers with the served
      document that was actually recorded, rather than with an equal-looking
      one of its own.

    ``fetched_at`` comes from the injected ``Clock`` (ruling R-09) and is what
    ``get_run`` reconstructs ``Run.path`` from.
    """
    for step in run.steps:
        if step.node_id == node_id and step.iteration == iteration:
            return step.served
    record = StepRecord(
        node_id=node_id, iteration=iteration, served=fixture, fetched_at=context.clock.now()
    )
    return context.store.upsert_step(run.id, record).served


def _flag(context: ServiceContext, run: Run, warnings: list[Warning]) -> None:
    """Merge new warnings onto the stored run. No write when nothing is new.

    Contracts 3.4 attaches a warning "to both the response and the stored run"
    and PRD 5.2 says an exhausted pool "flags" the run, so the run has to carry
    them. De-duplication is on the whole ``(code, detail)`` pair, which is what
    makes this idempotent: a replayed fetch recomputes an identical warning and
    adds nothing, while a *different* iteration drawing past the pool records
    its own entry - so the run says which iterations were served short, not
    merely that one was.

    Writing only on a change is what keeps the replay path free of any store
    write at all.
    """
    added = [item for item in warnings if item not in run.warnings]
    if not added:
        return
    context.store.put_run(run.model_copy(update={"warnings": [*run.warnings, *added]}))


# ------------------------------------------------------------------- findings


def _dataset_missing(dataset_id: str, agent_id: str) -> RuleError:
    return runtime(
        RT_E04,
        field_pointer("selector", "dataset_id"),
        f"no dataset {dataset_id!r}.",
        dataset_id=dataset_id,
        agent_id=agent_id,
    )


def _wrong_agent(dataset: Dataset, agent_id: str) -> RuleError:
    return runtime(
        RT_E04,
        field_pointer("selector", "dataset_id"),
        f"dataset {dataset.id} was authored against agent "
        f"{dataset.blueprint.agent_id!r}, not {agent_id!r}.",
        dataset_id=str(dataset.id),
        agent_id=agent_id,
        dataset_agent_id=dataset.blueprint.agent_id,
    )


def _no_match(agent_id: str, labels: dict[str, str]) -> RuleError:
    return runtime(
        RT_E04,
        field_pointer("selector", "labels"),
        f"no dataset for agent {agent_id!r} matches those labels.",
        agent_id=agent_id,
        labels=labels,
    )


def _no_blueprint(run: Run) -> RuleError:  # pragma: no cover - unreachable, see fetch_step
    return boundary(
        AP_NOT_FOUND,
        field_pointer("run_id"),
        f"run {run.id} pins blueprint {run.agent_id} {run.pin.blueprint_version}, which the store "
        f"does not hold.",
        run_id=run.id,
        agent_id=run.agent_id,
        blueprint_version=run.pin.blueprint_version,
    )


def _no_dataset(run: Run) -> RuleError:  # pragma: no cover - unreachable, see fetch_step
    return runtime(
        RT_E04,
        field_pointer("run_id"),
        f"run {run.id} pins dataset {run.pin.dataset_id} version "
        f"{run.pin.dataset_version}, which the store does not hold.",
        run_id=run.id,
        dataset_id=str(run.pin.dataset_id),
        dataset_version=run.pin.dataset_version,
    )


def _no_fixture(
    dataset: Dataset, node_id: str, iteration: int
) -> RuleError:  # pragma: no cover - unstorable, see _draw
    return boundary(
        AP_NOT_FOUND,
        field_pointer("node_id"),
        f"dataset {dataset.id} version {dataset.version} carries no fixture for node "
        f"{node_id!r} at iteration {iteration}.",
        dataset_id=str(dataset.id),
        dataset_version=dataset.version,
        node_id=node_id,
        iteration=iteration,
    )


def _not_a_pool(node_id: str, iteration: int) -> RuleError:
    """Ruling R-03's complement: a non-zero iteration on a ``pool: false`` node.

    RT-E02 rather than a revived RT-E05. The node has exactly one fixture, so
    iteration 1 addresses nothing - which is a caller error about *what was
    asked for*, not a verdict about how many times the agent looped.
    """
    return runtime(
        RT_E02,
        field_pointer("iteration"),
        f"node {node_id!r} declares pool: false, so it has one fixture and only iteration 0 "
        f"addresses it.",
        node_id=node_id,
        iteration=iteration,
    )
