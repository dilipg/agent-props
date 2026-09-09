"""Ground rule 1 and PRD 5.6, as a guard: the runtime writes the run and nothing else.

M6's fifth acceptance clause is "**no test can find a write path from
``fetch_step`` to a dataset**", and this file is that clause. It is deliberately
mechanical in three independent ways, because a docstring saying "this module
performs no dataset write" is worth nothing next to a test of the losing side:

**The call graph.** :func:`test_no_runtime_entry_point_reaches_a_world_write`
walks every function reachable from ``run_start``, ``fetch_step``, ``run_get``
and ``run_find`` through `service/`, and asserts that none of them calls a store
method that mutates anything but a run. The forbidden set is **enumerated from
the ``Store`` Protocol** rather than written out: every method whose name starts
with a mutating verb, minus the three run writes. So a method M7 or M9 adds -
``put_expansion``, ``set_dataset_labels``, anything - is forbidden here the day
it appears on the Protocol, with no edit to this file.

**The one dataset write.** :func:`test_put_dataset_has_exactly_one_call_site`
pins what the M6 brief states as fact: ``put_dataset`` is called from exactly
one place in `src/`, in `service/skeletons.py`, and that place is reachable from
``dataset_submit`` and from nothing else. Ground rule 1's "nothing writes to a
dataset except the authoring flow" is that sentence, and this is it as an
assertion.

**Behaviour.** :func:`test_a_whole_runtime_walk_makes_no_world_write` drives a
real walk - ``run_start``, nine ``fetch_step`` calls including a pool loop and
a replay, ``run_get``, ``run_find`` - against a store wrapper that **fails the
test** if any world write is attempted. An AST guard cannot see a write made
through ``getattr`` or through a helper it failed to follow; this can.
:func:`test_the_read_only_wrapper_catches_the_authoring_write` is that
wrapper's own proof: the same wrapper, handed the authoring flow, raises. A
guard that has never been shown to fail is a guard nobody has tested.

``server/`` is deliberately absent from all of this. `test_layering.py` already
asserts that no module in `server/` imports `storage/` at all, so a tool
function cannot reach a store method by any spelling - the two guards compose,
and duplicating one here would suggest they do not.
"""

from __future__ import annotations

import ast
import copy
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import pytest

import agentprops.service
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
from agentprops.service import ServiceContext, blueprints, datasets, runs
from agentprops.service.limits import MAX_STORED_INT
from agentprops.storage import Store
from envelopes import data

SERVICE_DIR: Final[Path] = Path(agentprops.service.__file__).resolve().parent
SERVICE_FILES: Final[list[Path]] = sorted(SERVICE_DIR.glob("*.py"))
SRC_DIR: Final[Path] = SERVICE_DIR.parent

AGENT = "location-onboarding"
RUN_ID = "m6-readonly-run"
PRIYA = "3f8c1a20-0000-4000-8000-000000000001"
POOL_NODE = "request_docs"

#: The verbs a mutating ``Store`` method begins with. ``delete_`` is here even
#: though ground rule 6 forbids one from ever existing - if a ``delete_dataset``
#: is ever added, this guard should be the second test to fail, not a test that
#: silently ignored it.
MUTATING_VERBS: Final[tuple[str, ...]] = ("put_", "set_", "mark_", "upsert_", "delete_")

#: The verbs a **reading** ``Store`` method begins with, and the one reader whose
#: name is a bare verb. Not used to permit anything - every method not matched by
#: :data:`MUTATING_VERBS` is already treated as a read - but to make that
#: treatment a *decision* rather than a default:
#: :func:`test_every_store_method_is_classifiable` fails on a method matching
#: neither list, so a future ``archive_dataset``, ``record_outcome`` or
#: ``bump_version`` cannot be silently classified as harmless. Without it, the
#: durability claim for the guard below holds only for the five prefixes above.
READING_VERBS: Final[tuple[str, ...]] = ("get_", "find_", "list_")
READING_NAMES: Final[frozenset[str]] = frozenset({"health"})

#: The writes the runtime is *allowed* to make. PRD 5.6: "``record_step`` writes
#: to the run, never to the world." ``set_step_actual`` was allowlisted at M3,
#: before ``record_step`` existed, because the property being asserted is "only
#: the run is written", not "only the calls that exist today" - and M8 landing
#: ``record_step`` and ``run_finish`` is what that foresight bought:
#: ``mark_run_finished`` is the one name that had to be added.
RUN_WRITES: Final[frozenset[str]] = frozenset(
    {"put_run", "upsert_step", "set_step_actual", "set_run_warnings", "mark_run_finished"}
)

#: Where the runtime starts. Every public function in `service/runs.py`, named
#: as ``(module, function)`` pairs so the closure below is unambiguous about
#: which ``get`` or ``find`` it means. M8's two writes are entry points like the
#: other four: they write a *run*, and the claim being guarded is that nothing
#: reachable from them writes anything else.
RUNTIME_ENTRY_POINTS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("runs.py", "start"),
        ("runs.py", "fetch_step"),
        ("runs.py", "record_step"),
        ("runs.py", "finish"),
        ("runs.py", "get"),
        ("runs.py", "find"),
    }
)

#: The authoring entry points, for the other half of
#: :func:`test_put_dataset_has_exactly_one_call_site`: the dataset write must be
#: reachable from ``submit`` and from neither of the two earlier steps.
AUTHORING_SUBMIT: Final[tuple[str, str]] = ("skeletons.py", "submit")
AUTHORING_EARLIER: Final[frozenset[tuple[str, str]]] = frozenset(
    {("skeletons.py", "skeleton"), ("skeletons.py", "fill_part")}
)


def store_mutators() -> frozenset[str]:
    """Every mutating method on the ``Store`` Protocol, read off the Protocol.

    ``dir()`` on a Protocol class reports its declared methods, so this follows
    `storage/base.py` rather than a list maintained here. That is the property
    ruling R-50 named for the bounded-integer guard and it applies just as well
    to this one: a guard that reads a literal list of today's writes is the
    defect it exists to prevent.
    """
    return frozenset(
        name for name in dir(Store) if not name.startswith("_") and name.startswith(MUTATING_VERBS)
    )


STORE_MUTATORS: Final[frozenset[str]] = store_mutators()

#: Everything a mutator may touch that is **not** a run. ``put_blueprint`` and
#: the two skeleton writes are in here as well as the two dataset writes: the
#: claim being guarded is the stronger and simpler one - the runtime writes the
#: run - and clause 5's "no write path to a dataset" is a subset of it.
WORLD_WRITES: Final[frozenset[str]] = STORE_MUTATORS - RUN_WRITES


# ------------------------------------------------------------ the enumeration


def test_the_protocol_enumeration_finds_the_writes_it_should() -> None:
    """The guard's own guard. Every assertion below is vacuous without this.

    Names the three things that must be true of the enumeration: it is not
    empty, the run writes it allowlists are really on the Protocol (a rename
    would otherwise silently widen the allowlist into nothing), and
    ``put_dataset`` - the one call clause 5 is about - is really in the
    forbidden set.
    """
    assert STORE_MUTATORS, f"no mutating methods found on the Store Protocol: {dir(Store)}"
    assert RUN_WRITES <= STORE_MUTATORS, (
        f"these allowlisted run writes are not on the Protocol any more: "
        f"{sorted(RUN_WRITES - STORE_MUTATORS)}"
    )
    assert "put_dataset" in WORLD_WRITES
    assert "set_archived" in WORLD_WRITES
    assert "upsert_step" not in WORLD_WRITES


def test_every_store_method_is_classifiable() -> None:
    """The guard's durability, closed. Nothing may default to "not a write".

    :func:`store_mutators` is a prefix test, and every public method it does not
    match is treated as a read by mechanisms 1 and 2. That is fine for the
    seventeen methods on the Protocol today and it is *not* self-maintaining: a
    method named ``archive_dataset``, ``record_outcome`` or ``bump_version``
    would write the world and be classified as harmless, with only mechanism 3
    left to notice.

    So an unclassifiable name fails here instead. Adding one is then a visible
    decision - put the verb in :data:`MUTATING_VERBS` or in
    :data:`READING_VERBS` - rather than a silent widening of what the runtime
    is allowed to reach.
    """
    public = {name for name in dir(Store) if not name.startswith("_")}
    assert len(public) >= 17, f"the Protocol enumeration is broken: {sorted(public)}"
    unclassifiable = {
        name
        for name in public
        if not name.startswith(MUTATING_VERBS + READING_VERBS) and name not in READING_NAMES
    }
    assert not unclassifiable, (
        f"these Store methods match neither the mutating nor the reading verbs, so the read-only "
        f"guards would treat them as harmless reads: {sorted(unclassifiable)}. Classify them."
    )


def test_the_protocol_declares_no_delete() -> None:
    """Ground rule 6, restated where a reader of this file will look for it.

    `test_storage_no_delete.py` owns the assertion; this one exists so that
    ``delete_`` appearing in :data:`MUTATING_VERBS` is visibly not an admission
    that such a method might exist.
    """
    assert not [name for name in dir(Store) if name.startswith("delete")]


# -------------------------------------------------------------- the call graph


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def dotted(node: ast.expr) -> str:
    """``context.store.put_dataset`` from an ``Attribute``/``Name`` chain."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def service_functions() -> dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function defined in `service/`, keyed by ``(module, name)``."""
    found: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for path in SERVICE_FILES:
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found[path.name, node.name] = node
    return found


FUNCTIONS: Final[dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef]] = (
    service_functions()
)


def called_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """The last dotted segment of every call in ``function``.

    The last segment is what matters for a store method: ``context.store.put_run``
    and ``self._store.put_run`` are both ``put_run``, and a guard matching the
    whole dotted path would be defeated by a local alias.
    """
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            if name:
                names.add(name.rsplit(".", 1)[-1])
    return names


def targets(module: str, name: str) -> set[tuple[str, str]]:
    """Which indexed function(s) a call to ``name`` from ``module`` could mean.

    Same module first, because that is what Python does for a local helper and
    because `service/` has several same-named functions (``get``, ``find``,
    ``paginate``). Otherwise every module that defines the name, which is
    conservative in the safe direction: an ambiguous name pulls *more* code into
    the closure, so the guard can over-report but not under-report.
    """
    if (module, name) in FUNCTIONS:
        return {(module, name)}
    return {key for key in FUNCTIONS if key[1] == name}


def reachable(entries: frozenset[tuple[str, str]] | set[tuple[str, str]]) -> set[str]:
    """Every call name in the transitive closure of ``entries`` through `service/`.

    A call-graph walk rather than a per-file grep, because the question is not
    "does `runs.py` write a dataset" - it is "can anything ``fetch_step``
    *calls* write one".
    """
    unknown = {entry for entry in entries if entry not in FUNCTIONS}
    assert not unknown, f"these entry points do not exist in service/: {sorted(unknown)}"
    seen: set[tuple[str, str]] = set()
    calls: set[str] = set()
    stack = list(entries)
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        for name in called_names(FUNCTIONS[key]):
            calls.add(name)
            stack.extend(target for target in targets(key[0], name) if target not in seen)
    return calls


@pytest.mark.parametrize("entry", sorted(RUNTIME_ENTRY_POINTS), ids=lambda entry: entry[1])
def test_no_runtime_entry_point_reaches_a_world_write(entry: tuple[str, str]) -> None:
    """Acceptance clause 5, on the call graph. Per entry point, so a failure names one.

    PRD 5.6: "There is no write path from a running agent to a dataset, in any
    phase." The runtime may call ``put_run`` and ``upsert_step``; every other
    store mutator is a finding.
    """
    offences = reachable({entry}) & WORLD_WRITES
    assert not offences, (
        f"service/{entry[0]}::{entry[1]} can reach {sorted(offences)}, which writes something "
        f"other than the run. Ground rule 1: nothing writes to a dataset except the authoring "
        f"flow."
    )


def test_the_runtime_does_write_the_run_so_the_guard_is_not_vacuous() -> None:
    """The other side of the same walk: the permitted writes are actually made.

    Without this, deleting every store call from `runs.py` would make the guard
    above pass - and the milestone's whole mechanism is that ``fetch_step``
    records the served step to the run.
    """
    calls = reachable(RUNTIME_ENTRY_POINTS)
    assert calls >= RUN_WRITES, (
        f"the runtime does not make every run write it is supposed to; missing "
        f"{sorted(RUN_WRITES - calls)}"
    )
    assert {"get_run", "get_dataset", "get_blueprint", "find_runs"} <= calls, (
        "the runtime does not read what it is supposed to read"
    )


def test_the_call_graph_guard_catches_a_planted_dataset_write(tmp_path: Path) -> None:
    """Prove the guard fails, and for the reason it claims to.

    A synthetic two-function module: the entry point calls a helper, and the
    *helper* is the one that writes the dataset. So this also proves the walk is
    transitive rather than a single-function check - which is the way a real
    violation would arrive, since a service function that wrote a dataset
    directly is the version nobody would write.
    """
    planted = tmp_path / "runtime.py"
    planted.write_text(
        "def fetch_step(context, run_id):\n"
        "    return _draw(context)\n"
        "\n"
        "def _draw(context):\n"
        "    return context.store.put_dataset(None)\n",
        encoding="utf-8",
    )
    index = {
        (planted.name, node.name): node
        for node in ast.walk(parse(planted))
        if isinstance(node, ast.FunctionDef)
    }
    calls: set[str] = set()
    stack = [(planted.name, "fetch_step")]
    seen: set[tuple[str, str]] = set()
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        for name in called_names(index[key]):
            calls.add(name)
            stack.extend(k for k in index if k[1] == name and k not in seen)
    assert calls & WORLD_WRITES == {"put_dataset"}
    assert ("runtime.py", "_draw") in seen, "the walk did not follow the helper"


# ---------------------------------------------------------- the one write site


def put_dataset_call_sites() -> set[tuple[str, str]]:
    """``(module, enclosing function)`` for every ``put_dataset`` call in `src/`.

    Recursive over the whole package, not only `service/`: the claim is about
    `src/`, and a ``put_dataset`` call appearing in `expansion/` or `export/`
    would be exactly the kind of thing this is meant to notice.
    """
    sites: set[tuple[str, str]] = set()
    for path in sorted(SRC_DIR.rglob("*.py")):
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if "put_dataset" in called_names(node):
                sites.add((path.name, node.name))
    return sites


#: Every function in `src/` that may call ``put_dataset``, with the tool it
#: serves. An **enumeration**, not a module list, and that is M7's change:
#: until M7 there was exactly one dataset writer, and "one call site" was both
#: the invariant and the test. M7 added two more - ``dataset_expand`` and
#: ``dataset_import``, both of them the authoring flow - so the invariant is now
#: "every call site is a named authoring writer" and the test says which.
#:
#: The two adapter modules are excluded because they *implement* the method
#: rather than call it.
AUTHORING_DATASET_WRITERS: Final[dict[tuple[str, str], str]] = {
    ("skeletons.py", "_store"): "dataset_submit",
    ("expansion.py", "_write_expanded"): "dataset_expand",
    ("promotion.py", "_write_bundle"): "dataset_import",
}

#: The adapters. Each defines ``put_dataset``; neither calls one.
ADAPTER_MODULES: Final[frozenset[str]] = frozenset({"sql.py", "mongo.py"})


def test_every_put_dataset_call_site_is_a_named_authoring_writer() -> None:
    """Ground rule 1, widened from "one site" to "these three sites" at M7.

    ``put_dataset`` is called from exactly the functions in
    :data:`AUTHORING_DATASET_WRITERS`, each of which serves one authoring tool,
    and from nothing the runtime touches -
    :func:`test_no_runtime_entry_point_reaches_a_world_write` is the half that
    asserts the second clause, over the call graph, per entry point.

    Kept as an enumeration rather than relaxed to "anything in `service/`",
    because the thing worth noticing is a **fourth** writer appearing. Adding
    one now means adding a row here and saying which tool it serves, which is a
    visible decision; without this, M8 could add a dataset write and only the
    call-graph guard would have an opinion - and only if the new writer happened
    to be reachable from a runtime entry point.
    """
    sites = {site for site in put_dataset_call_sites() if site[0] not in ADAPTER_MODULES}
    assert sites == set(AUTHORING_DATASET_WRITERS), (
        f"the put_dataset call sites are not the named authoring writers. "
        f"unexpected: {sorted(sites - set(AUTHORING_DATASET_WRITERS))}; "
        f"missing: {sorted(set(AUTHORING_DATASET_WRITERS) - sites)}"
    )
    reachable_from_submit = reachable({AUTHORING_SUBMIT})
    assert "put_dataset" in reachable_from_submit, "dataset_submit no longer writes a dataset"
    for entry in AUTHORING_EARLIER:
        assert "put_dataset" not in reachable({entry}), (
            f"skeletons.py::{entry[1]} can write a dataset; only submit may"
        )


# -------------------------------------------------------- R-03, mechanically


@pytest.mark.parametrize("module", ["runs.py", "resolution.py"], ids=lambda name: name)
def test_the_read_path_never_reads_max_iterations(module: str) -> None:
    """Ruling R-03 as a property of the source: there is no cap to trip.

    RT-E05 was deleted, so ``max_iterations`` has no role at read time at all -
    a pool draw past the end repeats and warns whatever the blueprint says.
    Checked on the parsed AST rather than by grepping, so the two docstrings
    that *discuss* ``max_iterations`` do not trip it.
    """
    path = SERVICE_DIR / module
    offences = [
        f"line {node.lineno}"
        for node in ast.walk(parse(path))
        if isinstance(node, ast.Attribute) and node.attr == "max_iterations"
    ]
    offences += [
        f"line {node.lineno}"
        for node in ast.walk(parse(path))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value == "max_iterations"
    ]
    assert not offences, (
        f"service/{module} reads max_iterations at {offences}; ruling R-03 deletes RT-E05, so "
        f"the read path has no iteration cap"
    )


# ----------------------------------------------------------------- behaviour


class WorldWriteError(AssertionError):
    """A store mutator other than the three run writes was called."""


class RunOnlyStore:
    """A ``Store`` that delegates every read and refuses every non-run write.

    The behavioural half of clause 5, and it catches what an AST walk cannot: a
    write reached through ``getattr``, through a call the closure failed to
    follow, or through a store method a future refactor renames. It is an
    explicit delegate rather than a ``__getattr__`` proxy so that ``mypy
    --strict`` checks it against the Protocol - if the Protocol grows a method,
    this class stops satisfying ``Store`` and the type checker says so.
    """

    def __init__(self, inner: Store) -> None:
        self.inner = inner
        self.refused: list[str] = []

    def _refuse(self, method: str) -> Any:
        self.refused.append(method)
        raise WorldWriteError(f"the runtime called {method}, which writes the world")

    # reads, all delegated

    def get_blueprint(self, agent_id: str, version: str | None) -> Blueprint | None:
        return self.inner.get_blueprint(agent_id, version)

    def list_blueprints(self, status: str | None) -> list[BlueprintSummary]:
        return self.inner.list_blueprints(status)

    def get_dataset(self, dataset_id: str, version: int | None) -> Dataset | None:
        return self.inner.get_dataset(dataset_id, version)

    def find_datasets(self, q: DatasetQuery) -> list[DatasetSummary]:
        return self.inner.find_datasets(q)

    def get_skeleton(self, skeleton_id: str) -> Skeleton | None:
        return self.inner.get_skeleton(skeleton_id)

    def get_run(self, run_id: str) -> Run | None:
        return self.inner.get_run(run_id)

    def find_runs(self, q: RunQuery) -> list[RunSummary]:
        return self.inner.find_runs(q)

    def health(self) -> StoreHealth:
        return self.inner.health()

    # the three run writes, delegated: PRD 5.6 permits exactly these

    def put_run(self, run: Run) -> Run:
        return self.inner.put_run(run)

    def upsert_step(self, run_id: str, step: StepRecord) -> StepRecord:
        return self.inner.upsert_step(run_id, step)

    def set_run_warnings(self, run_id: str, warnings: Sequence[Warning]) -> list[Warning]:
        return self.inner.set_run_warnings(run_id, warnings)

    def set_step_actual(
        self, run_id: str, node_id: str, iteration: int, actual: Mapping[str, Any]
    ) -> StepRecord:
        return self.inner.set_step_actual(run_id, node_id, iteration, actual)

    def mark_run_finished(
        self, run_id: str, status: str, outcome: Mapping[str, Any], finished_at: datetime
    ) -> bool:
        return self.inner.mark_run_finished(run_id, status, outcome, finished_at)

    # everything else: refused

    def put_blueprint(self, bp: Blueprint, publish: bool) -> Blueprint:
        return self._refuse("put_blueprint")  # type: ignore[no-any-return]

    def put_dataset(self, ds: Dataset) -> Dataset:
        return self._refuse("put_dataset")  # type: ignore[no-any-return]

    def set_archived(self, dataset_id: str, archived: bool) -> DatasetSummary:
        return self._refuse("set_archived")  # type: ignore[no-any-return]

    def put_skeleton(self, sk: Skeleton) -> Skeleton:
        return self._refuse("put_skeleton")  # type: ignore[no-any-return]

    def mark_skeleton_submitted(self, skeleton_id: str, dataset_id: str) -> bool:
        return self._refuse("mark_skeleton_submitted")  # type: ignore[no-any-return]


@pytest.fixture
def guarded(
    context: ServiceContext, blueprint_document: dict[str, Any], dataset_document: dict[str, Any]
) -> ServiceContext:
    """The seeded store, wrapped so that any world write fails the test.

    Seeded *before* wrapping, because seeding is the authoring flow's job and
    the wrapper exists to refuse exactly that.
    """
    blueprints.upsert(context, blueprint_document, publish=True)
    context.store.put_dataset(Dataset.model_validate(dataset_document))
    return ServiceContext(store=RunOnlyStore(context.store), clock=context.clock)


def test_the_run_only_store_satisfies_the_protocol(guarded: ServiceContext) -> None:
    """``runtime_checkable`` sees the method names; ``mypy --strict`` sees the rest.

    If the Protocol grows a method, this fails at runtime and the type checker
    fails on the assignment in :func:`guarded` - so the wrapper cannot silently
    stop covering the whole surface.
    """
    assert isinstance(guarded.store, Store)


def test_the_read_only_wrapper_catches_the_authoring_write(
    guarded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    """Prove the wrapper works, on both dataset writes, *through the service*.

    Reaching into ``guarded.store`` directly would only prove the wrapper
    raises. These two go through ``service/`` functions, which is what proves
    the wrapper is actually the store those functions use - so the walk below
    is running against a store that would notice.
    """
    with pytest.raises(WorldWriteError):
        datasets.archive(guarded, PRIYA)
    with pytest.raises(WorldWriteError):
        guarded.store.put_dataset(Dataset.model_validate(dataset_document))


def test_a_whole_runtime_walk_makes_no_world_write(guarded: ServiceContext) -> None:
    """The M8 script's shape, against a store that refuses to write the world.

    Every kind of call M6 ships: a pin by label query, addressing by node id and
    by tool name in both directions, an in-pool draw, an exhausted draw, a
    replay, an unresolvable tool name, a refused iteration, and both run reads.
    **Plus M8's two writes**, which is the half that matters now that the module
    has write entry points: a ``record_step`` against a served step, a
    re-``record_step`` of the same actual, a ``run_finish``, a diverging
    re-``run_finish``, and a ``fetch_step`` *after* the run is closed - so the
    R-54(c) path that keeps serving a finished run is inside the wrapper too.
    None of them may write anything but the run.
    """
    assert runs.start(guarded, RUN_ID, AGENT, {"labels": {"scenario": "missing-documents"}}).ok, (
        "the walk never started"
    )

    def step(**arguments: Any) -> Any:
        return runs.fetch_step(guarded, RUN_ID, **arguments)

    step(node_id="receive_request")
    step(tool_name="delightree.stores.get")
    step(node_id="check_docs")
    step(tool_name="delightree.stores.get")
    step(node_id=POOL_NODE, iteration=0)
    step(node_id=POOL_NODE, iteration=1)
    step(tool_name="delightree.stores.get")
    step(node_id=POOL_NODE, iteration=2)
    step(node_id=POOL_NODE, iteration=0)
    step(node_id=POOL_NODE, iteration=MAX_STORED_INT + 1)
    step(node_id="no_such_node")

    produced = {"requested": ["fssai"], "received": []}
    assert runs.record_step(guarded, RUN_ID, produced, node_id=POOL_NODE, iteration=0).ok
    assert runs.record_step(guarded, RUN_ID, produced, node_id=POOL_NODE, iteration=0).ok
    assert runs.record_step(guarded, RUN_ID, produced, node_id="escalate").ok is False
    assert runs.finish(guarded, RUN_ID, {"onboarding_status": "complete"}, "finished").ok
    assert runs.finish(guarded, RUN_ID, {"onboarding_status": "escalated"}, "abandoned").ok
    step(node_id="receive_request")

    runs.get(guarded, RUN_ID)
    runs.find(guarded, RunQuery())

    store = guarded.store
    assert isinstance(store, RunOnlyStore)
    assert store.refused == [], f"the runtime attempted {store.refused}"


def test_the_walk_leaves_the_dataset_byte_identical(
    context: ServiceContext, blueprint_document: dict[str, Any], dataset_document: dict[str, Any]
) -> None:
    """The same claim from the outside: read the dataset before and after.

    Belt and braces on top of the wrapper, and the one assertion a reader who
    distrusts every mechanism above can still check: the bytes the authoring
    flow stored are the bytes that are there afterwards, and no new version
    appeared.
    """
    blueprints.upsert(context, blueprint_document, publish=True)
    context.store.put_dataset(Dataset.model_validate(dataset_document))
    before = copy.deepcopy(data(datasets.get(context, PRIYA, None))["dataset"])
    counted = context.store.health().counts.datasets

    runs.start(context, RUN_ID, AGENT, {"dataset_id": PRIYA})
    for iteration in range(4):
        runs.fetch_step(context, RUN_ID, node_id=POOL_NODE, iteration=iteration)
    runs.fetch_step(context, RUN_ID, node_id="receive_request")

    after = data(datasets.get(context, PRIYA, None))["dataset"]
    assert json.dumps(after, sort_keys=True) == json.dumps(before, sort_keys=True)
    assert context.store.get_dataset(PRIYA, 2) is None, "the walk created a dataset version"
    assert context.store.health().counts.datasets == counted
