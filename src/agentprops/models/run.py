"""The run aggregate: one execution of an agent against one pinned dataset.

`docs/contracts.md` section 2.3, which nests a ``pin`` where `docs/prd.md`
section 5.4 is flat. Ruling R-08 takes contracts' shape: PRD 5.6 says
``run_start`` pins ``{dataset_id, dataset_version, blueprint_version}``, which
is exactly that nesting.

The run is the only thing in the system anything writes to at runtime.
``record_step`` writes to the run, never to the world; there is no write path
from a running agent to a dataset in any phase.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, StrictInt

from agentprops.models.base import StrictModel
from agentprops.models.errors import Warning

__all__ = ["ModelInfo", "PathStep", "Run", "RunPin", "RunQuery", "RunSummary", "StepRecord"]


class ModelInfo(StrictModel):
    """Which model drove the run. Recorded, never interpreted."""

    provider: str
    name: str
    version: str


class RunPin(StrictModel):
    """What ``run_start`` pins, and what every later ``fetch_step`` resolves
    against.

    Dataset edits are copy-on-write, so a run in flight keeps reading exactly
    what it started with, and a run finished two years ago is still readable
    against the version it used. This is also what makes soft archive coherent:
    an archived dataset disappears from ``dataset_find`` but stays servable to
    any run holding a pin to it, with a ``dataset_archived`` warning.
    """

    dataset_id: UUID
    dataset_version: StrictInt
    blueprint_version: str


class PathStep(StrictModel):
    """One entry in the reconstructed traversal.

    The path is reconstructed, not declared: the ordered sequence of calls
    carrying a run id *is* the traversal, so branch selection is learned
    implicitly and the agent cannot lie about where it went.
    """

    node_id: str
    iteration: StrictInt
    at: datetime


class StepRecord(StrictModel):
    """The fixture served for one step, and what the agent produced there.

    Idempotent on ``(run_id, node_id, iteration)``, which is the ``run_steps``
    primary key - so ``fetch_step`` is idempotent at the storage layer rather
    than in application code, and a retry an hour later still resolves to the
    same fixture.

    ``seq`` and ``fetched_at`` are ``run_steps`` columns that contracts 2.3's
    inline ``steps`` example elides. They are carried here, optional, so that
    M3 can write a row from a ``StepRecord`` without reshaping this model; a
    run document that omits them still parses.
    """

    node_id: str
    iteration: StrictInt

    served: dict[str, Any]
    """The fixture actually handed over. Needed for evidence; the dataset
    itself is referenced by the pin, never duplicated into the run."""

    actual: dict[str, Any] | None = None
    """What the agent produced. Written by ``record_step``, absent until
    then."""

    recorded_at: datetime | None = None
    seq: StrictInt | None = None
    """Traversal order, which reconstructs the path."""

    fetched_at: datetime | None = None


class Run(StrictModel):
    """One execution. Stored permanently, keyed by a client-generated id."""

    id: str
    """Client-generated at library init, before the first call: an opaque
    string of 8 to 128 characters matching ``^[A-Za-z0-9_.:-]+$``. The service
    does not generate it and does not parse meaning from it, so ``uuid4()`` in
    the *client* is correct and is the one place ground rule 9 permits it."""

    agent_id: str
    pin: RunPin

    declared_blueprint_version: str | None = None
    """What the agent said it was running. A value other than
    ``pin.blueprint_version`` produces a ``blueprint_version_mismatch``
    warning, and the dataset is served anyway - the service never gates."""

    model: ModelInfo | None = None
    run_class: str
    """``dev``, ``eval`` or ``load``. Required here and defaulted to ``dev`` by
    the service, so the default lives in one place rather than in each storage
    adapter."""

    path: list[PathStep] = Field(default_factory=list)
    steps: list[StepRecord] = Field(default_factory=list)

    outcome: dict[str, Any] | None = None
    """What the agent actually produced. **Not graded here.** The service
    stores expectations and emits evidence; comparison lives in the client."""

    warnings: list[Warning] = Field(default_factory=list)
    status: str
    """``running``, ``finished`` or ``abandoned``."""

    started_at: datetime
    finished_at: datetime | None = None

    external_refs: dict[str, str | None] = Field(default_factory=dict)
    """``otel_trace_id``, ``langfuse_run_id``, ``braintrust_experiment_id``.
    Free-form so M10 can add a target without a model change."""


class RunQuery(StrictModel):
    """The filter ``Store.find_runs`` takes.

    Shape derived from ``run_find``'s parameters, per ruling R-05. ``dataset_id``
    is a ``str`` rather than a ``UUID`` because these fields mirror the tool
    inputs, which are strings: an unparseable filter should return no rows, not
    raise.
    """

    agent_id: str | None = None
    dataset_id: str | None = None
    run_class: str | None = None

    model: str | None = None
    """Matches ``model.name``. The column is JSONB and ``run_find`` documents
    the parameter only as ``model?``; the model *name* is the only part of
    :class:`ModelInfo` worth filtering a run list on."""

    limit: StrictInt | None = None
    offset: StrictInt | None = None


class RunSummary(StrictModel):
    """One row of ``run_find``.

    Ruling R-05 derives it from the ``runs`` DDL columns minus ``outcome`` -
    the one column large enough to be worth withholding from a list. Column
    names are the DDL's, verbatim (``dataset_ver``, ``bp_version``).
    """

    id: str
    agent_id: str
    dataset_id: UUID
    dataset_ver: StrictInt
    bp_version: str
    run_class: str
    model: ModelInfo | None = None
    status: str
    warnings: list[Warning] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime | None = None
    external_refs: dict[str, str | None] = Field(default_factory=dict)
