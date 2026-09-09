"""A recorded run as an OpenTelemetry trace: one span for the run, one per step.

M10's first acceptance clause is "a completed run appears as a trace in a local
OTel collector with one span per step", and PRD design principle 7 is the reason
the milestone exists at all: **interoperate rather than replace - emit formats
other tools consume.** A trace is that format. Nothing here replaces Langfuse,
Braintrust, Jaeger or Tempo; it hands them a run in the shape they already read.

What this module takes, and why it is the evidence bundle
---------------------------------------------------------

:func:`trace_of` takes the **`run_evidence` bundle** - a plain mapping - and
nothing else. Not a :class:`~agentprops.models.Run`, not a store, not a context.
Three consequences, and the second is the one worth the constraint:

1. `export/` imports no other layer of this package at all. It is the only
   package here that reaches *nothing* sideways, which
   `tests/unit/test_layering.py` asserts rather than this docstring claiming it.
2. **The trace cannot disagree with the evidence.** Both are the same document,
   so a field that appears on a span was in the bundle a grader read, and a
   caller who compares a Langfuse trace with a `run_evidence` response is
   comparing one thing to itself. Had this taken a ``Run`` it would have been a
   second projection of the same run, free to drift from the first.
3. It is trivially testable: the pure half of this module needs no store, no
   session and no collector.

Two halves, and only the second does I/O
----------------------------------------

:func:`trace_of` builds a :class:`TraceSpec` - a tree of :class:`SpanSpec`,
every id and timestamp already decided. :func:`emit` hands that tree to the
OpenTelemetry SDK and an OTLP exporter. The split is what lets every property
below be asserted with no network, and it is why a collector is needed for
exactly one test rather than for the suite.

The span hierarchy
------------------

One trace per run. The root span is the run; each recorded step is a **direct
child** of the root, in ``(seq, node_id, iteration)`` order - the order
`run_get` already returns steps in (ruling R-37), so the trace's span order is
the run's total order and is identical on all three backends.

Flat rather than nested, deliberately. A run's steps are a *sequence* an agent
walked, not a call stack: a loop node's third iteration is not inside its
second, and ``check_docs`` visited twice is two peers rather than one
containing the other. Nesting would also have to invent a parent for the pool
iterations, and any invention here is a claim about the agent's control flow
that the service cannot observe - the path is *reconstructed* from call order
(PRD 5.5), and reconstruction gives order, not depth. Langfuse asks for the
same shape from the other direction: "Do not create an enclosing 'experiment'
span."

Ids are derived, not random
---------------------------

The trace id is ``blake2b(run_id)`` and each span id is
``blake2b(run_id + the step's identity)``. So:

- **exporting the same run twice names the same trace**, which makes
  `run_export` idempotent in the only sense that matters to a backend - a retry
  after a timeout lands in the trace it was already writing to, rather than
  creating a second, unlinked one;
- the ``external_ref`` a caller gets back is a function of the run id, so
  nothing has to be stored for it to remain true (see `service/evidence.py` for
  why that decision was taken rather than adding a store write at the last
  milestone);
- a span id is a function of ``(run_id, node_id, iteration)`` - the step's
  identity - and **not** of its position, so a test can assert a named step's
  span id without depending on emission order.

This is a hash, not a random source: ground rule 9 routes *randomness* through
``Seeded``, and there is none here. `tests/unit/test_layering.py` holds this
package to the same no-clock, no-unseeded-random rule as `expansion/`, and the
clock half is load-bearing in its own right - **every timestamp on every span
comes from the recorded run**, so the trace is a replay of what happened and not
of when it was exported.

Never a failure signal
----------------------

**No span carries an error status, ever**, and no attribute says a run passed
or failed. Ground rule 2: the service stores expectations and emits evidence,
and the three comparison helpers live in the client. A span status of ``ERROR``
on a mismatch would be a grade emitted by the service - and worse, one emitted
where a dashboard turns it red without anyone having chosen a comparison mode.

The two paths ride the trace the way they ride the bundle: as
``agentprops.path.expected`` and ``agentprops.path.actual``, side by side, with
no attribute comparing them. A consumer that wants a verdict has both lists.
`tests/unit/test_export_otel.py` asserts the no-error rule against a run whose
outcome contradicts its expectation in every field, and against a run the agent
abandoned.

A fault fixture is the case that tempts the other way, and it is the clearest:
``fault`` means the *authored world* failed on purpose, so a red span would
report a fixture doing its job as an error.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from hashlib import blake2b
from typing import Any, Final

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.id_generator import IdGenerator
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import Span, Tracer, set_span_in_context

__all__ = [
    "ATTR_PREFIX",
    "DEFAULT_OTLP_TRACES_ENDPOINT",
    "INSTRUMENTATION_NAME",
    "OTLP_ENDPOINT_ENV_VARS",
    "SERVICE_NAMESPACE",
    "AttributeValue",
    "Emission",
    "SpanSpec",
    "TraceSpec",
    "derived_span_id",
    "derived_trace_id",
    "emit",
    "encoded",
    "readable_spans",
    "resolved_endpoint",
    "root_span_id_hex",
    "span_count",
    "trace_of",
]

#: Every vendor-neutral attribute this module writes starts here. A consumer
#: that knows nothing about agent-props can still tell which attributes came
#: from it, and a vendor overlay (`langfuse.py`) never has to worry about
#: colliding with one.
ATTR_PREFIX: Final = "agentprops"

#: ``service.namespace`` on the resource. The semantic convention defines it as
#: a namespace that distinguishes a group of services, which is exactly the
#: relationship: ``service.name`` is the **agent** being traced - that is what a
#: tracing UI groups by and what a reader wants to see - and this says which
#: system emitted it.
SERVICE_NAMESPACE: Final = "agent-props"

#: The instrumentation scope name. Identifies the emitter of the spans, as
#: distinct from the system they describe.
INSTRUMENTATION_NAME: Final = "agentprops.export.otel"

#: Where the OTLP/HTTP exporter posts when nothing names an endpoint. The SDK's
#: own default, restated so a caller can be *told* which URL an export used -
#: ruling R-60's standing requirement is to put the URL beside any count, and an
#: export reporting "7 spans" without saying where they went is the same class
#: of evidence as a test count with no URL beside it.
DEFAULT_OTLP_TRACES_ENDPOINT: Final = "http://localhost:4318/v1/traces"

#: The two environment variables the OTLP/HTTP exporter reads for its endpoint,
#: most specific first. Named here so :func:`resolved_endpoint` can report which
#: URL an export used. ``OTEL_EXPORTER_OTLP_HEADERS`` is the SDK's channel for
#: an API key and is deliberately absent from this list and from this package
#: (ground rule 4, and ruling R-83's "no vendor client, no API key handling in
#: `src/`"): a credential reaches the collector through the SDK's own
#: environment convention without passing through any code here.
OTLP_ENDPOINT_ENV_VARS: Final[tuple[str, ...]] = (
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)

#: What an OTel attribute may hold. Not arbitrary JSON: the protocol allows
#: scalars and homogeneous sequences of them, so every nested document on a span
#: - a served fixture, an outcome, a schema - is carried as a JSON **string**.
#: :func:`_encoded` is the one place that happens.
AttributeValue = str | bool | int | float | Sequence[str]

#: How a nested document is spelled on a span, and it is stable: sorted keys and
#: no incidental whitespace, so two exports of one run produce byte-identical
#: attribute values and a diff between two traces is a diff between two runs.
_JSON_SEPARATORS: Final = (",", ":")

#: The salt that derives the root span's id. Steps use their own identity; see
#: :func:`_step_salt`.
_ROOT_SALT: Final = "run"

#: Nanoseconds in a second. The OTel API takes span times in Unix nanoseconds
#: and every timestamp in a run is an ISO-8601 string.
_NANOS_PER_SECOND: Final = 1_000_000_000


@dataclass(frozen=True, slots=True)
class SpanSpec:
    """One span, fully decided: its name, its id, its window and its attributes.

    ``span_id`` is a 64-bit int the way the SDK spells one, derived from the run
    id and the span's identity. ``start_ns`` and ``end_ns`` are Unix nanoseconds
    and both come from the recorded run.
    """

    name: str
    span_id: int
    start_ns: int
    end_ns: int
    attributes: Mapping[str, AttributeValue] = field(default_factory=dict)
    children: tuple[SpanSpec, ...] = ()

    @property
    def span_id_hex(self) -> str:
        """The 16-character lower-case hex form, which is how a backend names a span."""
        return f"{self.span_id:016x}"

    def walk(self) -> Iterator[SpanSpec]:
        """This span then its children, depth first. **Emission order.**"""
        yield self
        for child in self.children:
            yield from child.walk()

    def with_attributes(self, extra: Mapping[str, AttributeValue]) -> SpanSpec:
        """A copy carrying ``extra`` as well. What a vendor overlay is made of."""
        return replace(self, attributes={**self.attributes, **extra})

    def with_children(self, children: tuple[SpanSpec, ...]) -> SpanSpec:
        """A copy whose children are ``children``. The other half of an overlay."""
        return replace(self, children=children)


@dataclass(frozen=True, slots=True)
class TraceSpec:
    """One trace: its id, its resource attributes, and its root span."""

    trace_id: int
    root: SpanSpec
    resource: Mapping[str, AttributeValue] = field(default_factory=dict)

    @property
    def trace_id_hex(self) -> str:
        """The 32-character lower-case hex form: how every backend names a trace,
        and what `run_export` hands back as its ``external_ref``."""
        return f"{self.trace_id:032x}"

    def spans(self) -> tuple[SpanSpec, ...]:
        """Every span, root first, in emission order."""
        return tuple(self.root.walk())

    def with_root(self, root: SpanSpec) -> TraceSpec:
        return replace(self, root=root)

    def with_resource(self, extra: Mapping[str, AttributeValue]) -> TraceSpec:
        return replace(self, resource={**self.resource, **extra})


@dataclass(frozen=True, slots=True)
class Emission:
    """What :func:`emit` did.

    ``ok`` is the **exporter's** answer rather than this module's guess, and
    ``spans`` is what the exporter was handed. ``endpoint`` travels with them
    because a count without its URL is evidence of very little (ruling R-60).
    """

    ok: bool
    trace_id_hex: str
    endpoint: str
    spans: int


def derived_trace_id(run_id: str) -> int:
    """The trace id for ``run_id``: ``blake2b``, 16 bytes, never all-zero."""
    return _nonzero(_digest(run_id, "trace", 16))


def derived_span_id(run_id: str, salt: str) -> int:
    """The span id for one span of one run: ``blake2b``, 8 bytes, never all-zero.

    ``salt`` is the span's *identity* rather than its position - ``"run"`` for
    the root and ``"step:<node_id>:<iteration>"`` for a step - so the id is
    stable across a change in emission order and a test can name one.
    """
    return _nonzero(_digest(run_id, salt, 8))


def root_span_id_hex(run_id: str) -> str:
    """The root span's 16-character hex id, without building a whole trace.

    `langfuse.py` needs it for ``langfuse.experiment.item.root_observation_id``,
    which their documented ingestion contract requires to equal the root span's
    own ``spanId``.
    """
    return f"{derived_span_id(run_id, _ROOT_SALT):016x}"


def trace_of(evidence: Mapping[str, Any]) -> TraceSpec:
    """The `run_evidence` bundle as a trace. Pure: no clock, no network, no store.

    One root span for the run and one child per **step**, which is the
    acceptance clause's "one span per step" read literally: the bundle's
    ``nodes`` list is one entry per served step - per ``(node_id, iteration)``,
    so a loop node visited three times contributes three spans - and this
    produces exactly one span for each. :func:`span_count` states that
    arithmetic once so no caller restates it.
    """
    run = _mapping(evidence.get("run"))
    run_id = _text(run.get("id"))
    started = _nanos(run.get("started_at"))
    steps = tuple(_step_span(run_id, entry, started) for entry in _sequence(evidence.get("nodes")))
    root = SpanSpec(
        name=_root_name(evidence),
        span_id=derived_span_id(run_id, _ROOT_SALT),
        start_ns=started,
        end_ns=_run_end(started, _nanos(run.get("finished_at")), steps),
        attributes=_root_attributes(evidence),
        children=steps,
    )
    return TraceSpec(
        trace_id=derived_trace_id(run_id),
        root=root,
        resource=_resource_attributes(evidence),
    )


def span_count(evidence: Mapping[str, Any]) -> int:
    """One span for the run plus one per step. The clause-1 arithmetic, once."""
    return 1 + len(_sequence(evidence.get("nodes")))


def resolved_endpoint(endpoint: str | None, environment: Mapping[str, str]) -> str:
    """The URL an export will post to, so a caller can be told it.

    ``endpoint`` wins; then the SDK's two documented environment variables, most
    specific first; then the SDK's default. ``environment`` is a parameter
    rather than a read of ``os.environ``, which keeps this a pure function and
    keeps this module free of ambient state - the caller holding an environment
    is `service/evidence.py`, one layer up.

    The bare ``OTEL_EXPORTER_OTLP_ENDPOINT`` form is a **base** URL by the SDK's
    own convention, so ``/v1/traces`` is appended to that one and not to the
    signal-specific one.
    """
    if endpoint:
        return endpoint
    specific, base = OTLP_ENDPOINT_ENV_VARS
    if chosen := environment.get(specific):
        return chosen
    if chosen := environment.get(base):
        return f"{chosen.rstrip('/')}/v1/traces"
    return DEFAULT_OTLP_TRACES_ENDPOINT


def readable_spans(spec: TraceSpec) -> tuple[ReadableSpan, ...]:
    """``spec`` realised as OpenTelemetry spans, in emission order. **No network.**

    The SDK is what turns a span into the thing an exporter serialises, so this
    is where a :class:`SpanSpec` becomes real - and it does it with an in-memory
    exporter, which is why every property of the emitted trace can be asserted
    with no collector: `tests/unit/test_export_otel.py` reads the ids, the
    parents, the attributes and the statuses off *these* objects rather than off
    the spec it built them from.

    Four deliberate choices about the provider, all local to this call:

    - **it is not the global provider.** ``trace.set_tracer_provider`` is
      process-wide and the MCP SDK ships its own instrumentation; installing one
      here would either lose that race or win it. A provider built, used and
      shut down inside one call cannot interfere with anything;
    - **the sampler is** ``ALWAYS_ON``, **explicitly.** The default is read from
      ``OTEL_TRACES_SAMPLER``, so an ambient ``traceidratio`` in the environment
      would silently drop a caller's export. An explicit export is not a
      sampling decision;
    - **the id generator is** :class:`_SpecIds`, which hands out the ids
      :func:`trace_of` already derived. The SDK asks for them in emission order,
      and the return value here is what proves the queue and the tree agreed;
    - **the return order is the spec's**, not the order the spans *ended* in.
      :func:`_record_span` ends a parent after its children, so an in-memory
      exporter collects the root last; re-ordering here means index 0 is the run
      and the rest are its steps, and mapping every spec span onto exactly one
      realised span is itself a check that none went missing.
    """
    collected = InMemorySpanExporter()
    provider = TracerProvider(
        sampler=ALWAYS_ON,
        resource=Resource.create(dict(spec.resource)),
        id_generator=_SpecIds(spec),
        shutdown_on_exit=False,
    )
    provider.add_span_processor(SimpleSpanProcessor(collected))
    try:
        _record_span(provider.get_tracer(INSTRUMENTATION_NAME), spec.root, None)
        provider.force_flush()
    finally:
        provider.shutdown()
    return _in_spec_order(spec, collected.get_finished_spans())


def emit(spec: TraceSpec, endpoint: str, *, timeout: float | None = None) -> Emission:
    """Post ``spec`` to an OTLP/HTTP collector. Returns the exporter's answer.

    The one function in this package that does I/O, and everything it sends was
    decided by :func:`trace_of` - so what a collector receives is exactly the
    tree a test can assert on without one.

    **One batch, one request, one verdict**, and that is not a
    micro-optimisation. The obvious wiring - hang the OTLP exporter off a
    ``SimpleSpanProcessor`` - exports **one span per call**, so a ten-span run
    becomes ten HTTP requests, a collector sees ten payloads instead of one
    trace, and a dead endpoint costs ten retry budgets instead of one. That last
    one was measured rather than reasoned about: twenty seconds against one.
    Building the spans first and exporting them together fixes all three.

    ``ok`` is the exporter's own ``SpanExportResult`` rather than the absence of
    an exception, because the OTLP exporter does not raise on a refusal - it
    retries and returns ``FAILURE``. Reporting that as a success is the shape
    ruling R-65 forbade one layer in: a success the transport declined to give.

    No credential is read, constructed or logged here. ``OTLPSpanExporter``
    takes its headers from the SDK's own ``OTEL_EXPORTER_OTLP_HEADERS``
    convention, which is how a Langfuse or Braintrust key reaches a collector
    without passing through this package.
    """
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    spans = readable_spans(spec)
    exporter = OTLPSpanExporter(endpoint=endpoint, timeout=timeout)
    try:
        result = exporter.export(spans)
    finally:
        # ``OTLPSpanExporter.shutdown`` is unannotated in 1.44, and `mypy
        # --strict` refuses an untyped call. Ignored rather than wrapped: the
        # call is correct, and a shim would be a second thing to maintain over
        # an SDK annotation that will arrive on its own.
        exporter.shutdown()  # type: ignore[no-untyped-call]
    return Emission(
        ok=result is SpanExportResult.SUCCESS,
        trace_id_hex=spec.trace_id_hex,
        endpoint=endpoint,
        spans=len(spans),
    )


# ------------------------------------------------------------------- recording


def _in_spec_order(spec: TraceSpec, realised: Sequence[ReadableSpan]) -> tuple[ReadableSpan, ...]:
    """``realised`` reordered to match ``spec.spans()``, asserting a 1:1 match.

    An ``AssertionError`` rather than a finding: a span the SDK did not produce,
    or produced twice, is a defect in this module and not something a caller
    could have caused.
    """
    by_id = {span.context.span_id: span for span in realised if span.context is not None}
    assert len(by_id) == len(realised) == len(spec.spans()), (
        f"{len(spec.spans())} spans specified, {len(realised)} realised, {len(by_id)} distinct"
    )
    return tuple(by_id[wanted.span_id] for wanted in spec.spans())


def _record_span(tracer: Tracer, spec: SpanSpec, parent: Span | None) -> None:
    """Create one span, then its children, then end it.

    Depth-first with parents before children, which is :meth:`SpanSpec.walk`'s
    order and the order :class:`_SpecIds` hands out ids in. Each span's window
    came from the recorded run, so there is nothing to time and both ends are
    passed explicitly.
    """
    span = tracer.start_span(
        spec.name,
        context=None if parent is None else set_span_in_context(parent),
        attributes=dict(spec.attributes),
        start_time=spec.start_ns,
    )
    for child in spec.children:
        _record_span(tracer, child, span)
    span.end(end_time=spec.end_ns)


class _SpecIds(IdGenerator):
    """Hands the SDK the ids :func:`trace_of` derived, in emission order.

    The SDK calls ``generate_trace_id`` once for a root span and
    ``generate_span_id`` once per span, so a queue drained in
    :meth:`SpanSpec.walk` order gives every span the id its spec names. The ids
    themselves are derived from each span's *identity* (see
    :func:`derived_span_id`), so this class is plumbing rather than policy - and
    the plumbing is checked from the other end, by :func:`readable_spans`
    returning the realised spans keyed on those ids.

    ``is_trace_id_random`` keeps the base class's ``False``. The W3C flag it
    controls asserts that the low 56 bits were *randomly* generated; a digest is
    uniformly distributed but not random, and setting the flag would put a false
    statement in a header for the benefit of a ratio sampler :func:`emit`
    explicitly does not use.
    """

    def __init__(self, spec: TraceSpec) -> None:
        self._trace_id = spec.trace_id
        self._span_ids = iter([span.span_id for span in spec.spans()])

    def generate_trace_id(self) -> int:
        return self._trace_id

    def generate_span_id(self) -> int:
        return next(self._span_ids)


# ---------------------------------------------------------------- the root span


def _root_name(evidence: Mapping[str, Any]) -> str:
    """``run <agent_id>``. Low cardinality: one name per agent, not per run.

    A span name is a *class* of operation - the run id is an attribute, and
    putting it in the name is what makes a tracing UI's aggregate views useless.
    """
    return f"run {_text(_mapping(evidence.get('run')).get('agent_id')) or 'unknown'}"


def _root_attributes(evidence: Mapping[str, Any]) -> dict[str, AttributeValue]:
    """Everything about the run itself, from the bundle and only from the bundle."""
    run = _mapping(evidence.get("run"))
    pin = _mapping(evidence.get("pin"))
    dataset = _mapping(evidence.get("dataset"))
    expected = _mapping(evidence.get("expected"))
    path = _mapping(evidence.get("path"))
    found: dict[str, AttributeValue] = {
        f"{ATTR_PREFIX}.run.id": _text(run.get("id")),
        f"{ATTR_PREFIX}.run.class": _text(run.get("run_class")),
        f"{ATTR_PREFIX}.run.status": _text(run.get("status")),
        f"{ATTR_PREFIX}.agent.id": _text(run.get("agent_id")),
        f"{ATTR_PREFIX}.dataset.id": _text(pin.get("dataset_id")),
        f"{ATTR_PREFIX}.dataset.version": _integer(pin.get("dataset_version")),
        f"{ATTR_PREFIX}.dataset.title": _text(dataset.get("title")),
        f"{ATTR_PREFIX}.blueprint.version": _text(pin.get("blueprint_version")),
        f"{ATTR_PREFIX}.expected.comparison": _text(evidence.get("comparison")),
        f"{ATTR_PREFIX}.expected.final": encoded(expected.get("final")),
        f"{ATTR_PREFIX}.expected.rationale": _text(expected.get("rationale")),
        f"{ATTR_PREFIX}.outcome_schema": encoded(evidence.get("outcome_schema")),
        f"{ATTR_PREFIX}.actual": encoded(evidence.get("actual")),
        f"{ATTR_PREFIX}.step_count": len(_sequence(evidence.get("nodes"))),
        f"{ATTR_PREFIX}.path.expected": _texts(path.get("expected")),
        f"{ATTR_PREFIX}.path.actual": _texts(path.get("actual")),
        f"{ATTR_PREFIX}.warnings": _texts(
            [_text(_mapping(item).get("code")) for item in _sequence(evidence.get("warnings"))]
        ),
    }
    if declared := _text(run.get("declared_blueprint_version")):
        found[f"{ATTR_PREFIX}.blueprint.declared_version"] = declared
    found.update(
        {
            f"{ATTR_PREFIX}.label.{key}": _text(value)
            for key, value in _mapping(dataset.get("labels")).items()
        }
    )
    found.update(_model_attributes(_mapping(run.get("model"))))
    return found


def _model_attributes(model: Mapping[str, Any]) -> dict[str, AttributeValue]:
    """The model under test, in the GenAI convention's names where they exist.

    ``gen_ai.request.model`` and ``gen_ai.provider.name`` are the semantic
    convention's spellings, so a GenAI-aware backend groups these traces by
    model with no agent-props-specific knowledge. The convention has no field
    for a model *snapshot* date, which is what contracts 2.3's
    ``model.version`` holds, so that one keeps the local prefix rather than
    being squeezed into a name that means something else.

    Absent when the run declared no model: ``model`` is optional on a run, and
    an attribute holding ``""`` would claim a provider named empty string.
    """
    if not model:
        return {}
    return {
        "gen_ai.request.model": _text(model.get("name")),
        "gen_ai.provider.name": _text(model.get("provider")),
        f"{ATTR_PREFIX}.model.version": _text(model.get("version")),
    }


def _resource_attributes(evidence: Mapping[str, Any]) -> dict[str, AttributeValue]:
    """``service.name`` is the **agent**, not this service.

    A tracing UI groups by ``service.name``, and the useful grouping is the
    agent under test - which is also what makes two runs of one agent
    comparable, PRD design principle 2's whole claim. ``service.namespace``
    then says which system emitted the trace, and ``service.version`` carries
    the **pinned** blueprint version, because that is the version of the agent's
    shape this run was actually served against.

    ``service.instance.id`` is set to the **run id**, and setting it is what
    matters rather than the value: ``Resource.create`` fills that attribute with
    a fresh ``uuid4()`` from the SDK's own detector when nothing supplies it, so
    leaving it alone would put a random value in every export and make two
    exports of one run differ. Explicit attributes win over detected ones, and
    the run *is* the instance of the agent this trace describes - so the
    determinism and the semantics point the same way. Measured, not assumed:
    `tests/unit/test_export_otel.py` asserts the whole resource is equal across
    two exports, which is the assertion that would have caught the uuid.
    """
    run = _mapping(evidence.get("run"))
    return {
        "service.name": _text(run.get("agent_id")) or SERVICE_NAMESPACE,
        "service.namespace": SERVICE_NAMESPACE,
        "service.version": _text(_mapping(evidence.get("pin")).get("blueprint_version")),
        "service.instance.id": _text(run.get("id")),
    }


# ------------------------------------------------------------------ step spans


def _step_span(run_id: str, entry: Any, fallback_ns: int) -> SpanSpec:
    """One served step as one span. Named ``step <node_id>``, for `_root_name`'s reason."""
    node = _mapping(entry)
    node_id = _text(node.get("node_id"))
    iteration = _integer(node.get("iteration"))
    start = _nanos(node.get("fetched_at")) or fallback_ns
    return SpanSpec(
        name=f"step {node_id}",
        span_id=derived_span_id(run_id, _step_salt(node_id, iteration)),
        start_ns=start,
        end_ns=max(_nanos(node.get("recorded_at")), start),
        attributes=_step_attributes(node, node_id, iteration),
    )


def _step_salt(node_id: str, iteration: int) -> str:
    """A step's identity, which is its span id's whole input besides the run id."""
    return f"step:{node_id}:{iteration}"


def _step_attributes(
    node: Mapping[str, Any], node_id: str, iteration: int
) -> dict[str, AttributeValue]:
    """What happened at one step: what was authored, what was served, what came back."""
    found: dict[str, AttributeValue] = {
        f"{ATTR_PREFIX}.node.id": node_id,
        f"{ATTR_PREFIX}.node.iteration": iteration,
        f"{ATTR_PREFIX}.node.kind": _text(node.get("kind")),
        f"{ATTR_PREFIX}.step.seq": _integer(node.get("seq")),
        f"{ATTR_PREFIX}.step.served": encoded(node.get("served")),
        f"{ATTR_PREFIX}.step.expected": encoded(node.get("expected")),
        f"{ATTR_PREFIX}.step.actual": encoded(node.get("actual")),
        f"{ATTR_PREFIX}.step.recorded": bool(node.get("recorded")),
    }
    if tool_name := _text(node.get("tool_name")):
        found[f"{ATTR_PREFIX}.node.tool_name"] = tool_name
    if fault := _mapping(node.get("fault")):
        found[f"{ATTR_PREFIX}.node.fault.kind"] = _text(fault.get("kind"))
    if expectation := _mapping(node.get("node_expectation")):
        found[f"{ATTR_PREFIX}.node.expectation"] = encoded(expectation)
    return found


# ------------------------------------------------------------------- internals


def _digest(run_id: str, salt: str, length: int) -> int:
    """``blake2b`` over ``run_id`` and ``salt``, as an unsigned int of ``length`` bytes.

    The two inputs are joined with a NUL, which cannot occur in a run id
    (contracts 2.3: ``^[A-Za-z0-9_.:-]+$``) and cannot occur in a node id
    (``^[a-z][a-z0-9_]{0,62}$``), so no pair of ``(run_id, salt)`` can collide
    with another by concatenation.
    """
    return int.from_bytes(blake2b(f"{run_id}\x00{salt}".encode(), digest_size=length).digest())


def _nonzero(value: int) -> int:
    """``value``, or ``1`` if it is zero.

    The OTel API reserves the all-zero trace id and the all-zero span id for
    "no such span", so neither may be handed out. One decision site rather than
    a re-derivation loop: the substitute is as valid an id as the digest was,
    the case is unreachable in practice, and a loop here would be a second exit
    to reason about for no gain. Asserted directly in
    `tests/unit/test_export_otel.py`, because a property nothing can trigger is
    a property nothing would notice breaking.
    """
    return value or 1


def _nanos(value: Any) -> int:
    """An ISO-8601 timestamp as Unix nanoseconds; ``0`` for an absent one.

    The run's own timestamps, parsed. **No clock is read** - the models
    canonicalise every stamp to UTC (ruling R-24), so a bundle carries a
    ``...Z`` stamp and this is a parse rather than an interpretation.

    An unparseable value is ``0`` rather than a raised ``ValueError``, for the
    reason every other coercion in this module is total: :func:`trace_of` takes
    a plain mapping, and CLAUDE.md's style rule admits no third answer between
    a value and a structured finding - an exception out of a read path is
    neither. The condition is unreachable from a real bundle, since
    `service/evidence.py` builds every stamp through the model, so the risk this
    tolerance creates is a span silently starting at the Unix epoch. That is
    what `tests/unit/test_export_otel.py` asserts against directly: every span
    of a real trace has a start after 2020 and an end at or after its start.
    """
    if not isinstance(value, str) or not value:
        return 0
    try:
        return int(datetime.fromisoformat(value).timestamp() * _NANOS_PER_SECOND)
    except ValueError:
        return 0


def _run_end(started_ns: int, finished_ns: int, steps: Sequence[SpanSpec]) -> int:
    """When the root span ends: the latest of its ``finished_at``, its start and
    its last step.

    The obvious version - "``finished_at`` if it has one, else the last step" -
    is wrong in one direction and it is not hypothetical. A root span that ends
    *before* a child it contains is the one thing a trace viewer draws as
    nonsense, and every timestamp here comes from a different source: the run's
    two stamps from the injected clock (ruling R-09), a step's ``recorded_at``
    from a database column default. Nothing guarantees an ordering between
    those, and under a frozen test clock they invert - ``finished_at`` at
    12:00:00 with a step recorded at 12:00:01.

    So the maximum of all three, always. A run still in flight ends at its last
    step; a run with neither ends where it started.
    """
    return max([started_ns, finished_ns, *(step.end_ns for step in steps)])


def encoded(document: Any) -> str:
    """A nested document as a stable JSON string. ``""`` for an absent one.

    An OTel attribute holds a scalar or a homogeneous sequence, never an object,
    so every fixture, outcome and schema on a span travels as text. Sorted keys
    and no whitespace, so the string is a function of the document rather than
    of the encoder's mood - which is what makes two exports of one run
    byte-identical.
    """
    if document is None:
        return ""
    return json.dumps(document, sort_keys=True, separators=_JSON_SEPARATORS)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _texts(value: Any) -> tuple[str, ...]:
    """A homogeneous string sequence, which is the only list shape an attribute takes."""
    return tuple(_text(item) for item in _sequence(value))


def _integer(value: Any) -> int:
    """An int, and ``bool`` is not one - the trap `compare.py` records at length."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, str | bytes) else ()
