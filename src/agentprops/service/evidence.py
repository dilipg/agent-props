"""``run_evidence`` and ``run_export``: the bundle a grader reads, and pushing it out.

Ruling R-15 lands both here, at M10, for the reason it landed `record_step` at
M8: "the phase-2 tag means 'not required for a working phase-1 runtime', not
'must not exist'", and M10's gate needs them.

Two reads and nothing else
--------------------------

Every function in this module reads ``get_run``, ``get_dataset`` and
``get_blueprint`` and calls **no store mutator at all** - not even the five run
writes `runs.py` is allowed. `tests/unit/test_runtime_is_read_only.py` asserts
that as a separate, stricter clause than the one it applies to the runtime, with
its own negative control: a planted ``put_run`` in a synthetic evidence module
is observed to fail it.

That is worth a guard of its own rather than folding into the existing one.
``run_export`` is the first function in this service that talks to a **third
party**, and the temptation it creates is precisely to record what it did - to
stamp ``external_refs.otel_trace_id`` onto the run. It does not, and the reason
is not squeamishness: the trace id is ``blake2b(run_id)`` (see
`export/otel.py`), so it is already a function of the run and storing it would
store a value the run implies. Exporting twice yields the same
``external_ref``, so nothing is lost by not keeping it.

The alternative was weighed and rejected on rulings R-33 and R-55, which both
say the same thing from the other end: a ``Store`` method added **after** M7
costs three signed-off adapters instead of one conformance test. Adding
``set_run_external_refs`` at the last milestone of the phase would pay exactly
that, to persist a derivable value, and would put a write on the one path whose
whole claim is that it has none. ``Run.external_refs`` therefore stays empty in
phase 1; `DECISIONS.md` records it as the phase-2 item it is.

What the bundle carries, and why that list and not another
----------------------------------------------------------

**Ruling R-82 is the specification, and its general form is the useful part:**

> the bundle must carry **everything the three helpers take as input**.
> ``exact`` and ``subset`` need expected and actual; ``schema`` needs the
> schema. Read the gate as the specification it is, and let the helper
> signatures enumerate the bundle's contents rather than guessing at them.

So the three signatures in `client/python/agentprops_client/compare.py` were
read off, and each one's arguments are a key here:

===============================================  =============================
``exact(expected, actual)``                      ``expected.final``, ``actual``
``subset(expected, actual)``                     the same two
``schema(instance, outcome_schema)``             ``actual``,
                                                 **``outcome_schema``**
``grade(mode, ...)``                             ``comparison``
===============================================  =============================

``outcome_schema`` is the **schema itself, not a reference to it** - R-82's
whole point, because a reference would be the further server call the gate
forbids. The pinned blueprint version is immutable (BP-016), so an embedded
schema cannot go stale relative to the run that pinned it.

The per-node half is the same reading applied one level down: each served step
carries an ``expected`` (the authored fixture's ``output``) beside its
``actual`` (what ``record_step`` recorded), so ``exact(node["expected"],
node["actual"])`` works for a step exactly as it works for the outcome.

`tests/unit/test_evidence_grading.py` is the mechanical form of all of this and
the milestone's second acceptance clause: it imports ``compare`` and
``json``, **no store and no session**, loads a bundle and grades it in all three
modes. A guard in that file asserts its own import set, because a grading test
that could reach a store would not be testing the claim.

The two paths are juxtaposed, never compared
--------------------------------------------

``path`` carries ``expected`` and ``actual`` side by side and **computes no
verdict** - no ``matches``, no first-divergence index, no missing-node set.
Ground rule 2 is not "the service does not grade the outcome", it is "**there is
no comparison logic in the server**", and a path verdict is comparison logic. A
caller who wants one has ``exact(bundle["path"]["expected"],
bundle["path"]["actual"])``, which is a list comparison the client already
implements, positionally, with an index on the difference.

That restraint turned out to be load-bearing rather than pedantic. The golden
`priya-missing-docs` dataset's ``expected_path`` visits ``check_docs`` **twice**,
while ``fetch_step`` is idempotent on ``(run_id, node_id, iteration)`` - so a
second visit to a non-pool node records no second path entry, and the
reconstructed path can never equal that ``expected_path`` however correctly the
agent behaves. A ``matches_expected`` computed here would have reported a
conforming run as divergent, on the service's authority, in the direction that
reads as failure. `DECISIONS.md` carries it as a finding for the owner: whether
``expected_path`` means "per visit" or "per step key" is a data-contract
question, and it is not one this milestone may answer by picking a comparison.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any, Final

from agentprops.export import langfuse, otel
from agentprops.models import Blueprint, Dataset, RuleError, Run, StepRecord
from agentprops.service.context import ServiceContext
from agentprops.service.envelope import (
    AP_ARGUMENT,
    AP_EXPORT_REFUSED,
    Reply,
    boundary,
    failure,
    field_pointer,
    success,
)
from agentprops.service.runs import no_run

__all__ = [
    "EXPORT_TARGETS",
    "TARGET_LANGFUSE",
    "TARGET_OTEL",
    "bundle",
    "evidence",
    "export",
]

#: A vendor-neutral OTLP export: ``agentprops.*`` attributes plus the GenAI
#: convention's model names, and nothing vendor-specific. What a Jaeger, Tempo
#: or Grafana user wants.
TARGET_OTEL: Final = "otel"

#: The same trace with Langfuse's ``langfuse.*`` experiment and item attributes
#: added, so it arrives as a dataset run rather than as an anonymous trace.
#: Ruling R-83: attributes, not an SDK.
TARGET_LANGFUSE: Final = "langfuse"

#: ``run_export``'s closed vocabulary. An unknown value is ``AP-001`` naming
#: these, the way ``run_class`` and ``run_finish``'s ``status`` are - the
#: precedent is `runs.py`'s :data:`~agentprops.service.runs.RUN_CLASSES`.
#:
#: **Braintrust is deliberately absent**, and it is absent as a *finding* rather
#: than an oversight. PRD 5.4 point 6 names it alongside Langfuse and an OTel
#: collector, and a probe of its documented ingestion contract found that it
#: does accept OTLP - but that its project and experiment linkage rides an
#: ``x-bt-parent`` **HTTP header** rather than span attributes, which puts it
#: outside what this transport can express and inside what ground rule 4 keeps
#: out of `src/`. `DECISIONS.md` records the probe and the URL; adding the
#: target is an owner decision, not a line to slip in here.
EXPORT_TARGETS: Final[frozenset[str]] = frozenset({TARGET_OTEL, TARGET_LANGFUSE})


def evidence(context: ServiceContext, run_id: str) -> Reply:
    """The evidence bundle for one run. A read, and the whole of ruling R-82.

    Everything an external grader needs and no further server call: the
    expectation, the recorded actual, per-node expected against actual, the
    traversed path beside ``expected_path``, the declared comparison mode, the
    blueprint's ``outcome_schema`` **itself**, and the run's warnings.
    """
    document, findings = bundle(context, run_id)
    if document is None:
        return failure(findings)
    return success("evidence", document)


def export(context: ServiceContext, run_id: str, target: str) -> Reply:
    """Push one run to an OTLP collector as a trace. ``{external_ref}``.

    The trace is built from the evidence bundle and from nothing else, so what a
    Langfuse or Jaeger reader sees is the document ``run_evidence`` hands a
    grader - see `export/otel.py` on why that is a constraint rather than a
    convenience.

    ``ok`` is the exporter's verdict. An OTLP collector that refuses the batch,
    or is not there at all, is ``AP-008`` with the endpoint in the finding's
    context: the export did not happen, and reporting ``ok: true`` would claim a
    success the transport declined to give, which is ruling R-65's rule one
    layer out. That is **not** gating - ground rule 3 forbids refusing to
    *serve*, and R-56 settled that a refused write is ``ok: false``.

    Where it goes is the OpenTelemetry SDK's own environment convention -
    ``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT``, else ``OTEL_EXPORTER_OTLP_ENDPOINT``,
    else the SDK's ``localhost:4318`` default - so agent-props adds no
    configuration surface of its own and, in particular, **never reads a
    credential**: an API key travels in ``OTEL_EXPORTER_OTLP_HEADERS``, which
    the exporter reads and no code in `src/` mentions (ground rule 4, ruling
    R-83).
    """
    if target not in EXPORT_TARGETS:
        return failure([_unknown_target(target)])
    document, findings = bundle(context, run_id)
    if document is None:
        return failure(findings)
    spec = otel.trace_of(document)
    if target == TARGET_LANGFUSE:
        spec = langfuse.overlay(spec, document)
    endpoint = otel.resolved_endpoint(None, os.environ)
    emission = otel.emit(spec, endpoint)
    if not emission.ok:
        return failure([_export_refused(run_id, target, emission)])
    return success("external_ref", _external_ref(target, emission))


def bundle(context: ServiceContext, run_id: str) -> tuple[dict[str, Any] | None, list[RuleError]]:
    """The bundle, or the findings that stopped it. Shared by both tools.

    One function rather than two, because ``run_export`` sends the same document
    ``run_evidence`` returns and a second assembler would be a second thing to
    keep in step.

    The three reads are the pin's, not the caller's: ``get_dataset`` and
    ``get_blueprint`` are asked for the **pinned** version, so a bundle is as
    reproducible as the run it describes even after the dataset has been edited
    or archived (ground rule 5).
    """
    run = context.store.get_run(run_id)
    if run is None:
        return None, [no_run(run_id)]
    dataset = context.store.get_dataset(str(run.pin.dataset_id), run.pin.dataset_version)
    if dataset is None:  # pragma: no cover - no hard delete, so a pinned version stays
        return None, [_pin_unreadable(run, "dataset")]
    blueprint = context.store.get_blueprint(run.agent_id, run.pin.blueprint_version)
    if blueprint is None:  # pragma: no cover - BP-016 makes a published version immutable
        return None, [_pin_unreadable(run, "blueprint")]
    return _assembled(run, dataset, blueprint), []


# ------------------------------------------------------------------- assembly


def _assembled(run: Run, dataset: Dataset, blueprint: Blueprint) -> dict[str, Any]:
    """The bundle. Every value is copied from the run, the dataset or the
    blueprint; nothing here computes a verdict about any of them."""
    expected = dataset.expected.model_dump(mode="json")
    return {
        "run": _run_block(run),
        "pin": run.pin.model_dump(mode="json"),
        "dataset": _dataset_block(dataset),
        "comparison": expected.get("comparison"),
        "expected": expected,
        "outcome_schema": blueprint.outcome_schema,
        "actual": run.outcome,
        "nodes": [_node_block(step, expected, blueprint) for step in run.steps],
        "path": _path_block(run, expected),
        "warnings": [item.model_dump(mode="json") for item in run.warnings],
    }


def _run_block(run: Run) -> dict[str, Any]:
    """Who ran what, when. The run **without** its steps, path or outcome.

    Those three appear at the top level of the bundle under the names a grader
    reads them by - ``nodes``, ``path``, ``actual`` - so repeating them inside a
    nested run document would put two spellings of one fact in one payload.
    """
    document = run.model_dump(mode="json")
    return {key: value for key, value in document.items() if key not in _RUN_KEYS_HOISTED}


#: The run fields the bundle carries at its top level under a grader's name for
#: them, and therefore does not repeat inside ``run``.
_RUN_KEYS_HOISTED: Final[frozenset[str]] = frozenset(
    {"steps", "path", "outcome", "warnings", "pin"}
)


def _dataset_block(dataset: Dataset) -> dict[str, Any]:
    """Which authored world this was, in the terms a report is written in.

    Beyond what the three helpers take, and deliberately: the gate is "no
    further server calls", so a grader that has to ask which scenario it just
    graded has been sent back to the service for the answer. ``title`` and
    ``labels`` are what a suite is sliced and reported by (PRD 4), ``intent``
    says why the dataset exists in the suite, and ``narrative`` is the world the
    agent was given - which `export/langfuse.py` also needs as the experiment
    item's input.

    No node fixtures: those are in ``nodes``, per served step, which is the only
    place a grader needs them.
    """
    return {
        "id": str(dataset.id),
        "version": dataset.version,
        "title": dataset.provenance.title,
        "intent": dataset.provenance.intent,
        "author": dataset.provenance.author.model_dump(mode="json"),
        "labels": dict(dataset.labels),
        "narrative": dataset.narrative,
        "archived": dataset.archived,
    }


def _node_block(
    step: StepRecord, expected: Mapping[str, Any], blueprint: Blueprint
) -> dict[str, Any]:
    """One served step: what was authored, what was served, what came back.

    ``expected`` is the authored fixture's ``output`` - the world's statement of
    what happens at this node - lifted out of ``served`` so that
    ``exact(node["expected"], node["actual"])`` is the whole call. ``served`` is
    kept in full beside it because the rest of the fixture is evidence too: an
    ``input``, an ``entity_refs`` list and a ``fault`` all bear on why an
    ``actual`` looks the way it does.

    ``recorded`` distinguishes "the agent produced ``null`` here" from "the
    agent never recorded this step", which ``actual: null`` alone cannot -
    contracts 2.2 makes the same point about ``null`` not being absence, and
    `compare.py` treats an expected ``null`` as requiring the key.

    ``expected`` here is the **dumped** ``expected`` block rather than the
    model, because ``node_expectations`` holds ``NodeExpectation`` objects and
    a bundle key must be JSON. One dump, at the top, and every reader of it
    sees the same document.

    The two timestamps come from the **step's own** ``model_dump(mode="json")``
    rather than from ``datetime.isoformat()``, and that is ruling R-24 rather
    than fastidiousness: the model is the canonicaliser, and its canonical form
    is ``...Z``. A hand-rolled ``isoformat()`` here produced ``+00:00`` and put
    two spellings of one instant in one payload - which is exactly the
    round-trip defect R-24 was written about, in a new place.
    """
    node = next((entry for entry in blueprint.nodes if entry.id == step.node_id), None)
    document = step.model_dump(mode="json")
    return {
        "node_id": step.node_id,
        "iteration": step.iteration,
        "seq": step.seq,
        "kind": None if node is None else node.kind,
        "tool_name": None if node is None else node.tool_name,
        "expected": step.served.get("output"),
        "served": step.served,
        "actual": step.actual,
        "recorded": step.actual is not None,
        "fault": step.served.get("fault"),
        "node_expectation": _mapping(expected.get("node_expectations")).get(step.node_id),
        "fetched_at": document["fetched_at"],
        "recorded_at": document["recorded_at"],
    }


def _path_block(run: Run, expected: Mapping[str, Any]) -> dict[str, Any]:
    """The authored path and the traversed one, side by side. **No verdict.**

    See the module docstring: computing one would be comparison logic in the
    server, and for the golden dataset it would have been a *wrong* verdict.
    ``traversed`` keeps the iteration and the timestamp of each visit, which
    ``actual`` flattens away and which a grader looking at a loop wants.
    """
    return {
        "expected": list(_sequence(expected.get("expected_path"))),
        "actual": [entry.node_id for entry in run.path],
        "traversed": [entry.model_dump(mode="json") for entry in run.path],
    }


def _external_ref(target: str, emission: otel.Emission) -> dict[str, Any]:
    """What ``run_export`` hands back under contracts section 4's ``external_ref``.

    contracts names the key and not its type, and the type is an object for
    ruling R-60's standing reason: an export that reports a span count without
    the URL it posted them to is a number nobody can reproduce. ``trace_id`` is
    the value contracts 2.3 calls ``otel_trace_id``.
    """
    return {
        "target": target,
        "trace_id": emission.trace_id_hex,
        "endpoint": emission.endpoint,
        "spans": emission.spans,
    }


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, str | bytes) else ()


def _mapping(value: Any) -> Mapping[str, Any]:
    """A mapping, or an empty one. ``expected.node_expectations`` is optional."""
    return value if isinstance(value, Mapping) else {}


# ------------------------------------------------------------------- findings


def _unknown_target(target: str) -> RuleError:
    """``AP-001`` naming the vocabulary, the way ``run_finish``'s status does."""
    # ``export_target`` rather than ``target`` in the context, because
    # :func:`~agentprops.service.envelope.boundary`'s own second parameter is
    # named ``target`` - it is the pointer - and a kwarg cannot shadow it.
    return boundary(
        AP_ARGUMENT,
        field_pointer("target"),
        f"target must be one of {sorted(EXPORT_TARGETS)}",
        export_target=target,
        allowed=sorted(EXPORT_TARGETS),
    )


def _export_refused(run_id: str, target: str, emission: otel.Emission) -> RuleError:
    """``AP-008``: the collector refused, or was not there. Nothing was exported.

    The endpoint is in the context because it is the only actionable thing about
    this failure - the run is fine, the bundle is fine, and what is wrong is
    where the export was told to go.
    """
    return boundary(
        AP_EXPORT_REFUSED,
        field_pointer("target"),
        f"the OTLP collector at {emission.endpoint} did not accept the trace",
        run_id=run_id,
        export_target=target,
        endpoint=emission.endpoint,
        trace_id=emission.trace_id_hex,
    )


def _pin_unreadable(run: Run, what: str) -> RuleError:
    """The pinned dataset or blueprint version is not in the store.

    Unreachable in practice - there is no hard delete (ground rule 6) and a
    published blueprint version is immutable (BP-016) - and reported as a
    finding rather than an exception because CLAUDE.md's style rule admits no
    third option.
    """
    return boundary(
        AP_ARGUMENT,
        field_pointer("run_id"),
        f"the run's pinned {what} version is not in this store",
        run_id=run.id,
        pin=run.pin.model_dump(mode="json"),
    )
