"""The runtime: ``run_start``, ``fetch_step``, ``record_step``, ``run_finish``, and the two reads.

This module is where PRD 5.6 - "the runtime is read-only" - is either true or
false. Everything in it reads a blueprint and a dataset and writes **only** the
run: ``put_run`` for the run row, ``upsert_step`` for the step that was served,
``set_step_actual`` for what the agent did there, ``set_run_warnings`` for the
warning merge and ``mark_run_finished`` for the lifecycle. There is no call to
``put_dataset`` or ``set_archived`` anywhere on these paths, and that is
asserted mechanically rather than claimed here -
`tests/unit/test_runtime_is_read_only.py` walks the call graph of every entry
point in this module and fails on any store mutator that is not one of those
five run writes.

"Read-only" is about the *world*, and M8's two writes do not weaken it.
``record_step`` writes an ``actual`` onto a step of a run and ``run_finish``
closes a run; neither can reach a dataset, which is PRD 5.6's own division -
"``record_step`` writes to the run, never to the world".

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
:func:`_flag` merges anything new onto ``run.warnings``, keyed by
``(code, node_id, iteration)`` per ruling R-54(b), and writes only when the
merge actually adds something - which is what keeps "a repeated fetch advances
nothing" literally true.

**The write is ``set_run_warnings``, not ``put_run``, and that is not a
micro-optimisation.** ``put_run`` writes every column from the model it is
handed, so flagging a warning through an edited copy of the run this call read
at the top would revert ``status``, ``outcome`` and ``finished_at`` to whatever
they were at the snapshot - un-finishing a run that ``run_finish`` completed in
between. `storage/base.py::set_run_warnings` carries the full reasoning; the
short version is that the column bound is the guarantee, and a transaction
around a read and a write would not have been one (ruling R-37).

**M8 built the other half of that pair.** ``run_finish`` writes through
``mark_run_finished``, which sets ``status``, ``outcome`` and ``finished_at``
and touches no other column - so it cannot revert the ``warnings`` a concurrent
``fetch_step`` just merged in either. The two narrow writes are each other's
counterpart, and until M8 the bug was unreachable only because one of them did
not exist.

Closing a run, and the two repeated writes (ruling R-65)
--------------------------------------------------------

``run_finish`` and ``record_step`` are writes, so both meet a condition
``fetch_step`` never does: the value is **already there**. Ruling R-65 splits
that case in two, by whether the value *differs*, because the two halves have
different truths to report:

- **the same value** - an identical re-record, an identical re-finish - is a
  **silent no-op success**. Nothing changed and the caller's intent is already
  satisfied, so there is nothing to report: a caller repeating a write is a
  client retrying after a timeout, which is the case R-53 kept ``run_start``
  idempotent for, and success is the *expected* outcome. A warning on an
  expected outcome is noise, and a vocabulary that fires on expected outcomes
  trains callers to ignore it. This matches R-29's byte-identical re-publish,
  which is also silent. (M8's fix round briefly attached a warning here, reading
  R-65's first draft literally; the ruling was amended and the code removed.);
- **a differing value** is a **refused write**: ``ok: false`` with ``AP-007``.
  The write did not happen, so ``ok: true`` would misreport what the store now
  holds - the same class of defect as M3's silent success carrying the winner's
  value. R-33 already makes ``set_step_actual`` *raise* on a differing actual, so
  a tool answering ``ok: true`` would be reporting a success the storage layer
  explicitly declined to give.

**This is not gating**, and R-65 says why in terms: ground rule 3's "the service
never gates" is about refusing to **serve** - the read path must hand over
fixtures even when something looks wrong - and R-56 already settled that a
refused *write* is ``ok: false`` and is not gating. M8 first shipped both as
warnings, arguing R-53; R-65 corrected that, and the correction is one branch
per tool (:func:`_conflicted` and :func:`_finish_divergence`) rather than a
structural change.

R-54(c) is the third case and it goes the *other* way, because it is about
serving: a **finished** run still serves, since nothing reads ``Run.status`` at
read time and the read path is pin-scoped. So ``fetch_step`` against a closed run
attaches ``run_already_finished`` and answers normally - the "may warn" half of
that ruling, never the refusal it forbids.

That code is on the **read path only**. ``record_step`` against a closed run
succeeds silently: a write is not serving, so the sentence the code stands for -
"a finished run served you a fixture anyway" - would not be true of it.
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
    AP_STORE_REFUSED,
    AP_WRITE_ONCE_CONFLICT,
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
from agentprops.storage import RecordNotFoundError, StoreError

__all__ = [
    "DEFAULT_RUN_CLASS",
    "FINISH_STATUSES",
    "RUN_CLASSES",
    "RUN_ID_MAX_LENGTH",
    "RUN_ID_MIN_LENGTH",
    "RUN_ID_PATTERN",
    "STATUS_ABANDONED",
    "STATUS_FINISHED",
    "STATUS_RUNNING",
    "WARNING_DATASET_SELECTION_AMBIGUOUS",
    "WARNING_RUN_ALREADY_FINISHED",
    "WARNING_RUN_START_MISMATCH",
    "fetch_step",
    "find",
    "finish",
    "get",
    "paginate",
    "record_step",
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

#: A run's status the moment ``run_start`` returns. ``run_finish`` moves it to
#: one of :data:`FINISH_STATUSES`; nothing on the *read* path reads it, because
#: the service never gates (ruling R-54(c)).
STATUS_RUNNING: Final = "running"

#: The two statuses ``run_finish`` may move a run to. contracts 2.3's
#: vocabulary is ``running | finished | abandoned``, and ``running`` is
#: deliberately not accepted here: ``run_finish`` closes a run, and a tool that
#: accepted ``running`` would advertise an un-finish that
#: ``mark_run_finished``'s compare-and-set cannot perform anyway. An unknown
#: value is ``AP-001`` naming the vocabulary, the way ``run_class`` is.
STATUS_FINISHED: Final = "finished"
STATUS_ABANDONED: Final = "abandoned"
FINISH_STATUSES: Final[frozenset[str]] = frozenset({STATUS_FINISHED, STATUS_ABANDONED})

#: Attached when a ``{labels}`` selector matched more than one dataset, naming
#: how many and which one was assigned. ``Warning.code`` is an open string
#: (ruling R-22) and M4 set the precedent with ``blueprint_version_missing``.
#: Selection is *assignment*, not reservation (PRD 5.6 point 2), so matching
#: several datasets is not an error - but a caller whose label query was
#: broader than they thought has no other way to find out.
WARNING_DATASET_SELECTION_AMBIGUOUS: Final = "dataset_selection_ambiguous"

#: Attached when ``run_start`` names an existing run id with arguments that do
#: not match the run that exists - a different ``agent_id``, or a selector that
#: resolves to a different pin or to nothing at all. Ruling R-53: keep returning
#: the first run, because a retried request must not create a second run and
#: must not fail, and **say so**, because ground rule 3 is "mismatches produce
#: warnings attached to the response and to the stored run". It is the same
#: shape as ``blueprint_version_mismatch`` - the caller declared one thing, the
#: run is pinned to another, so serve the pin and report the difference.
WARNING_RUN_START_MISMATCH: Final = "run_start_mismatch"

#: **A repeated write attaches no warning at all**, and that is a ruling rather
#: than an omission - twice over, so neither half comes back by accident.
#:
#: M8 shipped ``step_actual_conflict`` and ``run_finish_mismatch`` as warnings on
#: a *successful* response. **Ruling R-65** replaced them with ``AP-007`` on an
#: ``ok: false`` envelope, because for a **differing** value the write did not
#: happen and ``ok: true`` would claim a success the store declined to give. See
#: :data:`~agentprops.service.envelope.AP_WRITE_ONCE_CONFLICT`,
#: :func:`_step_conflict` and :func:`_finish_conflict`.
#:
#: M8's fix round then added ``step_actual_already_recorded`` for the
#: **identical** half, reading R-65's "no-op success with a warning" literally.
#: **R-65 was amended and that code is gone**: its two cited precedents both
#: pointed the other way - R-29's byte-identical re-publish is silent and
#: R-47's compare-and-set loser *errors* - and on merits a caller re-recording an
#: identical actual is a client retrying after a timeout, so success is the
#: expected outcome. A warning on an expected outcome is noise, and a vocabulary
#: that fires on expected outcomes trains callers to ignore it. So an identical
#: re-write is a **silent** no-op success, and ``run_already_finished`` below
#: stays on the read path where R-54(c) put it.

#: Attached when ``fetch_step`` or ``record_step`` addresses a run whose
#: lifecycle is closed. Ruling R-54(c): "a finished run still serves, and that
#: is correct ... M8 may add a warning if it proves useful; it must not add a
#: refusal." This is the warning half. An agent still fetching steps after its
#: harness closed the run is a real defect in the harness and nothing else in
#: the response says so - but the fixtures are pin-scoped and immutable, so
#: serving them is not the defect and refusing would be a gate.
WARNING_RUN_ALREADY_FINISHED: Final = "run_already_finished"


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
    2. **replay an existing run**, and warn if the request diverges from it. A
       run id that already exists keeps its pin: it is immutable for the run's
       whole life (PRD 5.6), so re-pinning could hand a run already served from
       version 1 a pin to version 2. A client retrying after a network timeout
       therefore gets the same answer and never a second run. Ruling R-53 adds
       the other half - arguments that do not match the run that exists are a
       *mismatch*, so they warn on the response and on the stored run rather
       than being silently ignored. See :func:`_replayed`;
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
        return _replayed(context, existing, agent_id, selector)

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
        return failure([_no_run(run_id)])

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
    warnings += _lifecycle(run)
    _flag(context, run, warnings)
    return success("step", {"fixture": served, "resolved_node_id": resolved}, warnings)


def record_step(
    context: ServiceContext,
    run_id: str,
    actual: dict[str, Any],
    *,
    node_id: str | None = None,
    tool_name: str | None = None,
    iteration: int | None = None,
) -> Reply:
    """What the agent produced at one step. ``{step, resolved_node_id}``.

    The write half of ``fetch_step``, and deliberately the *same* addressing:
    ``node_id`` or ``tool_name``, resolved through `resolution.py` against the
    run's reconstructed position, with the same ``iteration``. The key is the
    **resolved** one - ``(run_id, resolved_node_id, iteration)`` - so a step
    fetched by tool name and recorded by node id is one step, which is what
    ruling R-64 means by "the same key means the same step".

    Four things this deliberately does not do:

    - **It does not grade.** Ground rule 2: the service "stores expectations and
      emits evidence" and holds no comparison logic. ``actual`` is stored
      verbatim, unvalidated against ``expected.final`` and unvalidated against
      the blueprint's ``output_schema`` - a run whose agent produced nonsense is
      a run whose evidence records nonsense, and the client's three comparison
      helpers are what decide whether that passes.
    - **It does not serve a step it has not served.** ``set_step_actual`` fails
      when no step record exists (ruling R-33), because an actual cannot be
      reported for a step that was never fetched. That is ``AP-004``: the step
      key names no record.
    - **It does not overwrite.** ``set_step_actual`` is write-once per key, and
      ruling R-65 splits a repeat by whether the value differs: an **identical**
      re-record is a **silent** no-op success, which is what makes a retry after
      a network blip safe, and a **differing** one is refused with ``AP-007``
      because the write did not happen. See :func:`_conflicted`.
    - **It does not refuse a closed run.** Ruling R-54(c) again: the warning,
      not the gate.
    """
    index, bad = _iteration(iteration)
    if bad:
        return failure(bad)

    run = context.store.get_run(run_id)
    if run is None:
        return failure([_no_run(run_id)])

    blueprint = context.store.get_blueprint(run.agent_id, run.pin.blueprint_version)
    if blueprint is None:  # pragma: no cover - DS-001 plus BP-016 make this unreachable
        return failure([_no_blueprint(run)])

    resolved, findings = resolve(blueprint, run, node_id=node_id, tool_name=tool_name)
    if resolved is None:
        return failure(findings)

    try:
        step = context.store.set_step_actual(run_id, resolved, index, actual)
    except RecordNotFoundError:
        return failure([_step_not_served(run_id, resolved, index, node_id, tool_name)])
    except StoreError:
        return _conflicted(context, run, resolved, index, actual)
    return _recorded(resolved, step)


def finish(context: ServiceContext, run_id: str, outcome: dict[str, Any], status: str) -> Reply:
    """Close a run: ``status``, ``outcome``, ``finished_at``. Returns the stored run.

    Ruling R-15 lands this at M8, the milestone whose gate needs a recorded
    outcome to grade. Three properties, and the middle one is the reason the
    store method is a compare-and-set:

    **The write is narrow.** ``mark_run_finished`` sets three columns and
    touches no other, which is ``set_run_warnings``' guarantee mirrored: a
    ``fetch_step`` running concurrently owns ``warnings``, and neither write can
    revert the other's column. ``put_run`` with an edited copy of the run read
    here would revert exactly the warning a concurrent exhausted draw had just
    merged in - the M6 finding, in the opposite direction.

    **The first finish wins**, and ruling R-65 splits what happens to the
    second by whether it agrees. The compare-and-set decides *once*, in the
    database, and the boolean it returns is the whole of this function's branch.
    An unconditional write would hand *both* callers a success envelope naming
    their own outcome while the row held one of them, which is the silent shape
    ruling R-47 rejected for ``mark_skeleton_submitted``.

    So a repeated **identical** finish is a **silent** no-op success - a retry
    is meant to be indistinguishable from the call it retries - and a
    **differing** one is ``AP-007`` and writes nothing. ``run_already_finished``
    is deliberately *not* attached here: that code belongs to the read path,
    where it means "a finished run served you a fixture anyway" (R-54(c),
    ratified by R-67(a)).

    **It does not grade, and it does not validate the outcome.** Ground rule 2:
    ``outcome`` is stored as given, even when it contradicts the blueprint's
    ``outcome_schema``. Validating it here would put the comparison the client
    owns onto the write path and would turn a *finding about the agent* into a
    refusal to record what the agent did.
    """
    findings = _finish_argument_findings(status)
    if findings:
        return failure(findings)
    try:
        claimed = context.store.mark_run_finished(run_id, status, outcome, context.clock.now())
    except RecordNotFoundError:
        return failure([_no_run(run_id)])
    run = context.store.get_run(run_id)
    if run is None:  # pragma: no cover - no hard delete, so the run cannot vanish
        return failure([_no_run(run_id)])
    diverged = [] if claimed else _finish_divergence(run, status, outcome)
    if diverged:
        return failure([_finish_conflict(run, status, diverged)])
    return _finished(run)


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
        return failure([_no_run(run_id)])
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


def _finish_argument_findings(status: str) -> list[RuleError]:
    """``run_finish``'s one argument check: the status vocabulary.

    ``AP-001`` naming the two accepted values, for the reason ``run_class`` is
    an ``AP-001``: the vocabulary is closed and no catalogue rule owns it.
    ``running`` is rejected along with everything else - see
    :data:`FINISH_STATUSES`.

    ``outcome`` is **not** checked here beyond the object-ness the boundary
    already enforced. Ground rule 2: its content is the agent's, and the service
    does not have an opinion about it.
    """
    if status in FINISH_STATUSES:
        return []
    return [
        boundary(
            AP_ARGUMENT,
            field_pointer("status"),
            f"status must be one of {sorted(FINISH_STATUSES)}.",
            argument="status",
            given=status,
            allowed=sorted(FINISH_STATUSES),
        )
    ]


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


def _replayed(
    context: ServiceContext, existing: Run, agent_id: str, selector: dict[str, Any]
) -> Reply:
    """A second ``run_start`` for a live run id: the same run, plus R-53's warning.

    Nothing about the run changes - not the pin, not ``started_at``, not
    ``declared_blueprint_version``. What the ruling adds is that a request which
    does **not** match the run cannot pass silently, so the selector is resolved
    again purely to be compared, and a divergence is merged onto the stored run
    as well as returned on the response.

    The response carries the run's warning list as it now stands rather than
    only this call's, which is what makes a mismatch recorded at the first start
    still visible on the fourth retry.
    """
    diverged = _divergence(context, existing, agent_id, selector)
    return _started(existing, _flag(context, existing, diverged))


def _divergence(
    context: ServiceContext, existing: Run, agent_id: str, selector: dict[str, Any]
) -> list[Warning]:
    """Ruling R-53's comparison: does this request match the run that exists?

    Two fields diverge independently and the ruling covers both. The selector is
    R-53's own case; ``agent_id`` was found alongside it and the ruling extended
    to cover it, because ``run_start(same_id, other_agent, ...)`` silently
    handing back another agent's run is the same shape of surprise.

    A selector that no longer resolves at all counts as divergence, and it is
    the case the ruling actually names - "the one that surprises a caller who
    mistyped a ``dataset_id``". A mistyped id does not resolve to a *different*
    pin, it resolves to nothing, so treating an unresolvable selector as "cannot
    compare, say nothing" would miss the motivating example. The findings from
    that resolution are deliberately dropped: a retry must not become an error
    because the world changed, so an RT-E04 informs the warning rather than
    replacing the reply.

    :func:`_select`'s own warnings are dropped for a different reason - they
    describe a pinning decision, and this call is not making one.
    """
    diverged: list[str] = []
    if agent_id != existing.agent_id:
        diverged.append("agent_id")
    pin, _unresolved, _selection = _select(context, agent_id, selector)
    if pin is None or pin != existing.pin:
        diverged.append("selector")
    if not diverged:
        return []
    return [
        warning(
            WARNING_RUN_START_MISMATCH,
            diverged=diverged,
            run_id=existing.id,
            pinned={
                "agent_id": existing.agent_id,
                "pin": existing.pin.model_dump(mode="json"),
            },
            requested={
                "agent_id": agent_id,
                "pin": None if pin is None else pin.model_dump(mode="json"),
            },
        )
    ]


def _started(run: Run, warnings: list[Warning]) -> Reply:
    """``{run, pin}`` under one named key, with the warnings on the envelope.

    contracts section 4 documents the payload as ``{run, pin, warnings}``;
    ``warnings`` is the envelope's own list (M4's convention) and ``pin`` is
    carried alongside the run as well as inside it, because that is the field a
    caller reads to learn what it was given.

    ``warnings`` is required rather than defaulted from the run, because the two
    callers want different lists and a default hid that: a first start reports
    what *this* call found, and a replay reports what the run now carries.
    """
    return success(
        "start",
        {"run": run.model_dump(mode="json"), "pin": run.pin.model_dump(mode="json")},
        warnings,
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
    node = next((candidate for candidate in blueprint.nodes if candidate.id == node_id), None)
    if node is None:  # pragma: no cover - resolve() has already proved the node exists
        return None, [], [_no_fixture(dataset, node_id, iteration)]
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


def _warning_key(item: Warning) -> tuple[str, Any, Any]:
    """Ruling R-54(b)'s merge key: ``(code, node_id, iteration)``.

    Derived from the detail rather than compared as a whole warning, and the
    ruling is explicit about why: it makes losing a duplicate concurrent write
    *provably* a no-op. Whole-``detail`` equality happened to be equivalent for
    ``pool_exhausted`` - ``pool_length`` and ``served_index`` are functions of
    the pinned pool and the iteration, so two calls for one key cannot differ -
    but that equivalence was written nowhere and any added detail field would
    have quietly broken it.

    A code with neither field in its detail - ``dataset_archived``,
    ``blueprint_version_mismatch``, ``run_start_mismatch`` - keys on
    ``(code, None, None)`` and is therefore recorded once per run. That is
    deliberate rather than a side effect: a client retrying a diverging
    ``run_start`` in a loop must not grow the run's warning list without bound,
    and each response carries its own current detail regardless of what the run
    already records.
    """
    return item.code, item.detail.get("node_id"), item.detail.get("iteration")


def _flag(context: ServiceContext, run: Run, warnings: list[Warning]) -> list[Warning]:
    """Merge new warnings onto the stored run; return its list as it now stands.

    Contracts 3.4 attaches a warning "to both the response and the stored run"
    and PRD 5.2 says an exhausted pool "flags" the run, so the run has to carry
    them. Two properties, both load-bearing:

    **The write is narrow.** ``set_run_warnings`` touches one column, so this
    cannot revert ``status``, ``outcome`` or ``finished_at`` - which ``put_run``
    with an edited copy of a snapshot would, and which nothing today would catch
    because ``run_finish`` arrives at M8. See the module docstring and
    `storage/base.py::set_run_warnings`.

    **The merge is keyed and writes only on a change**, so a replayed fetch
    recomputes an identical warning, adds nothing, and issues no write at all -
    which is what "a repeated fetch advances nothing" has to mean for the one
    part of a response that is recomputed rather than read back.
    """
    merged = list(run.warnings)
    seen = {_warning_key(item) for item in merged}
    for item in warnings:
        key = _warning_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    if len(merged) == len(run.warnings):
        return merged
    return context.store.set_run_warnings(run.id, merged)


# ------------------------------------------------- record_step and run_finish


def _lifecycle(run: Run) -> list[Warning]:
    """``run_already_finished``, if the run is closed. Ruling R-54(c)'s warn half.

    Called by ``fetch_step`` and by **nothing else**. The code means "a finished
    run served you a fixture anyway", which is a statement about *serving*, so
    ``record_step`` and ``run_finish`` do not attach it - R-54(c) put it on the
    read path and R-67(a) ratified it there. M8's fix round briefly attached it
    to ``run_finish``'s identical repeat as well and R-65's amendment removed
    that.

    Keyed off ``finished_at`` rather than ``status``, for the reason
    ``mark_run_finished``'s compare-and-set is: the timestamp is set by both
    terminal statuses, so one predicate covers ``finished`` and ``abandoned``
    without this module holding an opinion about the vocabulary in two places.

    The detail carries neither ``node_id`` nor ``iteration``, which makes
    :func:`_warning_key` record it **once per run** rather than once per step -
    deliberate, and the same choice ``run_start_mismatch`` makes: an agent
    looping against a closed run must not grow the run's warning list without
    bound, and every response carries its own copy regardless.
    """
    if run.finished_at is None:
        return []
    return [
        warning(
            WARNING_RUN_ALREADY_FINISHED,
            run_id=run.id,
            status=run.status,
            finished_at=run.finished_at.isoformat(),
        )
    ]


def _recorded(resolved: str, step: StepRecord) -> Reply:
    """``{step, resolved_node_id}`` for ``record_step``. **No warnings, ever.**

    contracts section 4 documents the return as ``{ok}``, which the envelope
    itself carries. The named key holds the **stored** step and the resolved
    node id, for ``fetch_step``'s reason: a caller who addressed the step by
    tool name has no other way to learn which node the key was built from.

    Nothing here attaches a warning, and the two that were tried are worth
    naming so neither comes back by accident:

    - ``step_actual_already_recorded`` on an identical repeat. **R-65 as
      amended makes that a silent success**, because a caller re-recording an
      identical actual is a client retrying after a timeout - the case R-53 kept
      ``run_start`` idempotent for - and success is what it expects. A warning on
      the *expected* outcome of a retry is noise, and a vocabulary that fires on
      expected outcomes trains callers to ignore it;
    - ``run_already_finished`` when the run is closed. That code belongs to the
      **read** path only, where R-54(c) put it and R-67(a) ratified it: it means
      "a finished run served you a fixture anyway". A write is not serving, so
      attaching it here would say something else under the same name.

    So this takes no ``ServiceContext`` and touches the store not at all: with
    nothing to attach there is nothing to merge onto the run.
    """
    return success(
        "record",
        {"step": step.model_dump(mode="json"), "resolved_node_id": resolved},
    )


def _conflicted(
    context: ServiceContext, run: Run, resolved: str, iteration: int, actual: dict[str, Any]
) -> Reply:
    """``set_step_actual`` refused. Which refusal was it?

    The store raises :class:`StoreError` for two different conditions and they
    need different answers, so this **re-reads the step and classifies** rather
    than assuming the common one:

    - a *different* actual is recorded: the write-once conflict, and the write
      **did not happen**. That is ``AP-007`` (ruling R-65). Returning
      ``ok: true`` here would report a success the storage layer explicitly
      declined to give, which is M3's silent-success defect said louder - and
      ground rule 3's "never gates" governs refusing to *serve*, while R-56
      already settled that a refused **write** is ``ok: false``;
    - no actual is recorded at all: the store exhausted its retry budget under
      contention. That is ``AP-005`` - "the store refused a write through one of
      its programming-error guards" - and it is emphatically not a conflict,
      because nothing is stored to conflict with. Reporting it as one would tell
      a caller their evidence lost to a value that does not exist.

    This is not a second decision site for the *write*: the store decided that,
    once, and this classifies the refusal it returned. The comparison is
    ``==`` on the two decoded documents rather than the adapter's canonical
    form, which is enough to tell the two conditions apart.

    **The third branch is unreachable by construction**, and it is written out
    rather than asserted away. An *identical* re-record never arrives here at
    all - ``set_step_actual`` returns the existing record for it and never
    raises (R-33's replay tolerance), which is R-65's no-op-success half and is
    handled a frame up. Reaching this branch would mean a value equal to the
    caller's appeared between the raise and the re-read, and write-once makes
    that impossible; if it ever happened, the caller's intent *is* satisfied and
    a success is the honest answer, so that is what it returns rather than
    raising a second exception inside an error path.
    """
    stored = _step_of(context, run.id, resolved, iteration)
    if stored is None or stored.actual is None:
        return failure([_store_refused(run.id, resolved, iteration)])
    if stored.actual != actual:
        return failure([_step_conflict(run.id, resolved, iteration, stored)])
    return _recorded(resolved, stored)  # pragma: no cover - see above


def _step_of(
    context: ServiceContext, run_id: str, node_id: str, iteration: int
) -> StepRecord | None:
    """The stored step for one key, read back through ``get_run``.

    A re-read rather than the snapshot ``record_step`` took at the top, because
    the snapshot is exactly what a concurrent writer has invalidated - it is the
    reason there is a conflict to report at all.
    """
    reread = context.store.get_run(run_id)
    if reread is None:  # pragma: no cover - no hard delete, so the run cannot vanish
        return None
    return next(
        (step for step in reread.steps if step.node_id == node_id and step.iteration == iteration),
        None,
    )


def _finished(run: Run) -> Reply:
    """``{run}`` for ``run_finish``: the run as stored, and nothing to add to it.

    Reached on both success paths R-65 recognises, and they answer identically:
    the call that closed the run, and an **identical** repeat that found it
    already closed. R-65 as amended makes the repeat a **silent** no-op, so
    there is no warning list to take and nothing to merge - a request that
    *would* have changed something never gets here at all, because
    :func:`finish` refuses it with ``AP-007``.

    ``run.warnings`` is the stored list, so a caller reading
    ``data.run.warnings`` sees what the run accumulated from ``fetch_step``,
    which is the field contracts section 4 means by "the stored run". This
    touches the store not at all.
    """
    return success("run", run.model_dump(mode="json"))


def _finish_divergence(run: Run, status: str, outcome: dict[str, Any]) -> list[str]:
    """Which of ``status`` and ``outcome`` this request disagrees with. **The branch.**

    Called only when the compare-and-set reported that this call did **not**
    close the run, so the stored values are another caller's (or this caller's
    own retry). An empty list means the request matches what is stored: a
    repeated identical finish, which is R-65's no-op success and is silent -
    that is what makes a retry after a network blip safe. A non-empty list means
    the write did not happen and ``AP-007`` says so.

    The two fields are tested **independently** rather than as a pair, so the
    finding names the one that actually diverged. A pair comparison would report
    both every time and still satisfy a test that only looked for a refusal.

    The two documents are compared as they now stand: ``run.outcome`` is the
    stored one, because :func:`finish` re-reads the run after the write.
    """
    return [
        name
        for name, differs in (("status", run.status != status), ("outcome", run.outcome != outcome))
        if differs
    ]


# ------------------------------------------------------------------- findings


def _no_run(run_id: str) -> RuleError:
    """RT-E03. The one finding four entry points share, so they cannot drift."""
    return runtime(RT_E03, field_pointer("run_id"), f"no run {run_id!r}.", run_id=run_id)


def _step_not_served(
    run_id: str, resolved: str, iteration: int, node_id: str | None, tool_name: str | None
) -> RuleError:
    """``AP-004`` for a ``record_step`` on a step that was never fetched.

    The step key names no record, which is what ``AP-004`` is: "no record with
    that id". Not an ``RT-*`` code - resolution *succeeded*, the node exists in
    the blueprint and the run exists in the store; what is missing is the served
    step, and ruling R-33 is explicit that an actual cannot be reported for one.

    The pointer addresses **whichever argument the caller used** to address the
    step, so a ``tool_name`` caller is not pointed at a ``node_id`` they never
    sent. ``resolved_node_id`` rides in the context either way, because a
    tool-name caller needs to know which node the key was built from.
    """
    return boundary(
        AP_NOT_FOUND,
        field_pointer("node_id" if node_id is not None else "tool_name"),
        f"run {run_id!r} has no served step {resolved}/{iteration}: an actual cannot be "
        f"recorded for a step that was never fetched.",
        run_id=run_id,
        resolved_node_id=resolved,
        iteration=iteration,
        node_id=node_id,
        tool_name=tool_name,
    )


def _step_conflict(run_id: str, resolved: str, iteration: int, stored: StepRecord) -> RuleError:
    """``AP-007`` for a ``record_step`` whose step already records a different actual.

    Ruling R-65: the write **did not happen**, so this is ``ok: false``. The
    pointer addresses ``/actual`` because that is the argument that could not be
    accepted - the address resolved fine and the run exists, and the only thing
    wrong with the request is the document it carries.

    The context names the step key and *when* the stored actual was recorded,
    and deliberately **not** the stored document itself. Two reasons, and the
    owner ruled on it at M8's first fix round rather than leaving it to taste:
    an ``actual`` is arbitrary agent output with **no size bound**, so putting
    one inside an error context is a real hazard - no other finding in this
    product carries a document - and ``run_get`` is the right place to look for
    it. What a caller needs from the finding is that a value is there, which key
    it is under, and that it is not theirs.
    """
    return boundary(
        AP_WRITE_ONCE_CONFLICT,
        field_pointer("actual"),
        f"step {resolved}/{iteration} of run {run_id!r} already records a different actual; "
        f"an actual is write-once per step, so nothing was written.",
        run_id=run_id,
        resolved_node_id=resolved,
        iteration=iteration,
        recorded_at=None if stored.recorded_at is None else stored.recorded_at.isoformat(),
    )


def _finish_conflict(run: Run, status: str, diverged: list[str]) -> RuleError:
    """``AP-007`` for a ``run_finish`` on a run already closed with other values.

    Ruling R-65 again, and the same reasoning: the compare-and-set reported that
    this call did not close the run, and the values differ, so nothing was
    written.

    The pointer addresses the **first** diverged field in a fixed order, so a
    status-only divergence points at ``/status`` and an outcome-only one at
    ``/outcome``, deterministically. ``diverged`` in the context names every
    field, which is what a caller reads when both did - a pointer can only
    address one, and picking by a rule beats picking by whichever comparison
    happened to run first.

    ``recorded`` and ``requested`` carry the *statuses* and the recorded
    ``finished_at``, and not the outcome documents, for :func:`_step_conflict`'s
    reason and under the same ruling: an ``outcome`` is unbounded agent output,
    an error context is the wrong place for one, and ``run_get`` is where it
    lives.
    """
    return boundary(
        AP_WRITE_ONCE_CONFLICT,
        field_pointer(diverged[0]),
        f"run {run.id!r} is already {run.status}; a run is closed once, so nothing was written.",
        run_id=run.id,
        diverged=diverged,
        recorded={
            "status": run.status,
            "finished_at": None if run.finished_at is None else run.finished_at.isoformat(),
        },
        requested={"status": status},
    )


def _store_refused(run_id: str, resolved: str, iteration: int) -> RuleError:
    """``AP-005`` for a ``set_step_actual`` that refused with nothing recorded.

    The store exhausted its retry budget without any actual being stored, which
    is contention rather than a conflict. ``AP-005`` is the code for "the store
    refused a write through one of its programming-error guards", and its
    catalogue entry says the right thing about this: reaching it means something
    upstream did not hold.
    """
    return boundary(
        AP_STORE_REFUSED,
        field_pointer("actual"),
        f"the store refused to record an actual for {run_id}/{resolved}/{iteration}.",
        run_id=run_id,
        resolved_node_id=resolved,
        iteration=iteration,
    )


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
