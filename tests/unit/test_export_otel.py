"""The trace, measured off the **spans the SDK produced** rather than off the spec.

M10's clause 1 is "a completed run appears as a trace in a local OTel collector
with one span per step". The collector half needs a collector and lives in
`tests/integration/test_otel_collector.py`. Everything else about the trace can
be measured with no network at all, and this file does it against
:func:`~agentprops.export.otel.readable_spans` - the realised
``ReadableSpan`` objects, ids, parents, attributes, windows and statuses
included - because that is what an exporter serialises. Asserting on the
``SpanSpec`` tree instead would test this module's intentions against itself.

No store either: the bundle comes from the committed fixture that
`test_service_evidence.py` holds to the live service, so the trace under test is
built from a document the service really emits.

The properties, and which of them are load-bearing
--------------------------------------------------

**One span per step, plus one for the run.** The clause, arithmetic and all
(:func:`test_one_span_per_step_plus_one_for_the_run`), with ``request_docs``
appearing twice because the bundle is keyed by ``(node_id, iteration)`` - so
"per step" is measured, not assumed to mean "per node".

**Ids are a function of identity.** A named step's span id equals
``derived_span_id(run_id, "step:<node>:<iteration>")``, so the assertion does
not depend on emission order; and two ``trace_of`` calls produce byte-identical
traces, resource included. That last one is what would have caught the
``service.instance.id`` the SDK's resource detector fills with a fresh
``uuid4()``.

**No span carries an error status**, asserted against the two runs that most
invite one: an outcome that contradicts the expectation in every field, and a
run the agent abandoned. Ground rule 2 - the service emits evidence and does not
grade - and a red span is a grade a dashboard acts on.

**No attribute was dropped.** The OTel SDK silently discards an attribute whose
value is not a scalar or a homogeneous sequence, with nothing but a log line, so
a nested document handed over as a ``dict`` would vanish from the trace and
every other test here would still pass.
:func:`test_no_attribute_is_silently_dropped` compares the realised attribute
keys with the spec's, and
:func:`test_the_dropped_attribute_guard_catches_an_illegal_value` plants a
``dict`` and observes the loss - which is ruling R-77(e), run rather than
reasoned about.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Final

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import StatusCode

from agentprops.export.otel import (
    ATTR_PREFIX,
    DEFAULT_OTLP_TRACES_ENDPOINT,
    OTLP_ENDPOINT_ENV_VARS,
    SERVICE_NAMESPACE,
    SpanSpec,
    derived_span_id,
    derived_trace_id,
    encoded,
    readable_spans,
    resolved_endpoint,
    root_span_id_hex,
    span_count,
    trace_of,
)
from agentprops.export.otel import _nonzero as nonzero
from conftest import FIXTURES_DIR
from evidencewalk import RUN_ID

FIXTURE: Final[Path] = FIXTURES_DIR / "evidence" / "priya-missing-docs-run.json"

#: Long after the epoch and long before anything this project will run on. Used
#: to assert no span silently starts at Unix zero, which is what an unparseable
#: timestamp would produce now that :func:`_nanos` tolerates one.
YEAR_2020_NS: Final = 1_577_836_800 * 1_000_000_000


@pytest.fixture
def bundle() -> dict[str, Any]:
    """The committed bundle. A deep copy, so a test may perturb it freely."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.fixture
def spans(bundle: dict[str, Any]) -> tuple[ReadableSpan, ...]:
    """The realised spans for the golden run, root first."""
    return readable_spans(trace_of(bundle))


def attributes(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


# ------------------------------------------------------------------- clause one


def test_one_span_per_step_plus_one_for_the_run(
    bundle: dict[str, Any], spans: tuple[ReadableSpan, ...]
) -> None:
    """The acceptance clause, as arithmetic and as names.

    Nine served steps and one run, so ten spans - and ``request_docs`` twice,
    because the two pool iterations are two steps. A trace with one span per
    *node* would have nine, which is the answer that looks right and is not.
    """
    assert len(spans) == span_count(bundle) == 10
    assert spans[0].name == "run location-onboarding"
    assert [span.name for span in spans[1:]] == [
        f"step {node['node_id']}" for node in bundle["nodes"]
    ]
    assert [span.name for span in spans].count("step request_docs") == 2


def test_every_span_is_in_one_trace_and_every_step_is_a_child_of_the_run(
    spans: tuple[ReadableSpan, ...],
) -> None:
    """One trace, one root, and a **flat** tree - no step is inside another step.

    Flat is the design decision (`export/otel.py`'s docstring), and the
    assertion that makes it real is that no span's parent is another *step*: a
    nesting would be a claim about the agent's control flow that the service
    cannot observe.
    """
    root, steps = spans[0], spans[1:]
    assert root.context is not None and root.parent is None
    trace_ids = {span.context.trace_id for span in spans if span.context is not None}
    assert trace_ids == {root.context.trace_id}
    parents = {span.parent.span_id for span in steps if span.parent is not None}
    assert parents == {root.context.span_id}
    assert len(parents) == 1, "a step is nested inside another step"


def test_the_span_order_is_the_runs_total_order(
    bundle: dict[str, Any], spans: tuple[ReadableSpan, ...]
) -> None:
    """``(seq, node_id, iteration)``, which ruling R-37 made total.

    The bundle's ``nodes`` is in the store's order and the trace preserves it, so
    two exports of one run - on any of the three backends - list the spans the
    same way.
    """
    sequence = [node["seq"] for node in bundle["nodes"]]
    assert sequence == sorted(sequence), "the bundle's steps are not in seq order"
    assert sequence == list(range(1, len(sequence) + 1)), sequence
    on_the_wire = [attributes(span)[f"{ATTR_PREFIX}.step.seq"] for span in spans[1:]]
    assert on_the_wire == sequence


# ------------------------------------------------------------------ derived ids


def test_the_trace_id_is_derived_from_the_run_id(spans: tuple[ReadableSpan, ...]) -> None:
    """So the ``external_ref`` is reproducible and a re-export lands in one trace."""
    assert spans[0].context is not None
    assert spans[0].context.trace_id == derived_trace_id(RUN_ID)
    assert f"{derived_trace_id(RUN_ID):032x}" != f"{derived_trace_id(RUN_ID + 'x'):032x}"


def test_a_named_steps_span_id_is_a_function_of_its_identity(
    spans: tuple[ReadableSpan, ...],
) -> None:
    """``(run_id, node_id, iteration)``, not position.

    So this assertion survives a change in emission order - and so does a
    caller who wants to link to one step of one run without asking us.
    """
    by_name = {
        (span.name, attributes(span)[f"{ATTR_PREFIX}.node.iteration"]): span for span in spans[1:]
    }
    first = by_name[("step request_docs", 0)]
    second = by_name[("step request_docs", 1)]
    assert first.context is not None and second.context is not None
    assert first.context.span_id == derived_span_id(RUN_ID, "step:request_docs:0")
    assert second.context.span_id == derived_span_id(RUN_ID, "step:request_docs:1")
    assert first.context.span_id != second.context.span_id


def test_the_root_span_id_helper_agrees_with_the_root_span(
    spans: tuple[ReadableSpan, ...],
) -> None:
    """`langfuse.py` needs the root span id before the trace exists; it must match.

    Their contract requires ``langfuse.experiment.item.root_observation_id`` to
    equal the root span's own ``spanId``, and the only reason that can be
    satisfied without inspecting the trace is that both sides derive it.
    """
    assert spans[0].context is not None
    assert f"{spans[0].context.span_id:016x}" == root_span_id_hex(RUN_ID)


def test_two_traces_of_one_bundle_are_identical(bundle: dict[str, Any]) -> None:
    """Byte-identical, resource included. PRD design principle 2, one layer out.

    The resource is the half that matters: ``Resource.create`` fills
    ``service.instance.id`` with a fresh ``uuid4()`` when nothing supplies it, so
    without this assertion two exports of one run would differ in a field nobody
    was looking at. `export/otel.py` sets it to the run id for that reason.
    """
    first, second = readable_spans(trace_of(bundle)), readable_spans(trace_of(bundle))
    assert [span.to_json() for span in first] == [span.to_json() for span in second]
    assert dict(first[0].resource.attributes) == dict(second[0].resource.attributes)


def test_the_reserved_all_zero_id_is_never_handed_out() -> None:
    """A digest of exactly zero would be the OTel API's "no such span".

    Unreachable in practice, which is precisely why the substitution is asserted
    directly rather than left to a test that cannot trigger it. Both ends: zero
    becomes one, and anything else is returned untouched.
    """
    assert nonzero(0) == 1
    assert nonzero(7) == 7
    assert derived_trace_id("") != 0
    assert derived_span_id("", "") != 0


# ------------------------------------------------------------------- the window


def test_every_span_window_comes_from_the_recorded_run(
    bundle: dict[str, Any], spans: tuple[ReadableSpan, ...]
) -> None:
    """A replay of what happened, not of when it was exported.

    The root starts at the run's ``started_at``; every span starts after 2020,
    which is what rules out the Unix-epoch span an unparseable timestamp would
    now silently produce; and no span ends before it started.
    """
    assert bundle["run"]["started_at"] == "2026-09-08T12:00:00Z"
    for span in spans:
        assert span.start_time is not None and span.start_time > YEAR_2020_NS, span.name
        assert span.end_time is not None and span.end_time >= span.start_time, span.name


def test_the_root_span_contains_every_step_span(spans: tuple[ReadableSpan, ...]) -> None:
    """A parent that ends before its child is what a trace viewer draws as nonsense.

    Not a theoretical concern: the run's stamps come from the injected clock and
    a step's ``recorded_at`` from a database column default, and under a frozen
    test clock they invert. `_run_end` takes the maximum of all three for exactly
    this assertion.
    """
    root, steps = spans[0], spans[1:]
    for span in steps:
        assert root.start_time is not None and span.start_time is not None
        assert root.end_time is not None and span.end_time is not None
        assert root.start_time <= span.start_time, span.name
        assert span.end_time <= root.end_time, span.name


def test_a_run_still_in_flight_ends_at_its_last_step(bundle: dict[str, Any]) -> None:
    """No ``finished_at`` is not an error - it is a run someone is still watching."""
    bundle["run"]["finished_at"] = None
    bundle["run"]["status"] = "running"
    spans = readable_spans(trace_of(bundle))
    assert spans[0].end_time == max(span.end_time or 0 for span in spans[1:])


def test_a_run_with_no_steps_still_yields_one_span(bundle: dict[str, Any]) -> None:
    """The empty end of the range, which the M8 boundary lesson asks for by name."""
    bundle["nodes"] = []
    bundle["run"]["finished_at"] = None
    spans = readable_spans(trace_of(bundle))
    assert len(spans) == 1
    assert spans[0].end_time == spans[0].start_time


# --------------------------------------------------------------- never a verdict


@pytest.mark.parametrize(
    "perturb",
    [
        pytest.param(lambda bundle: bundle, id="as-emitted"),
        pytest.param(
            lambda bundle: (
                bundle | {"actual": {"onboarding_status": "escalated", "outstanding_tasks": 99}}
            ),
            id="outcome-contradicts-the-expectation",
        ),
        pytest.param(
            lambda bundle: bundle | {"run": bundle["run"] | {"status": "abandoned"}},
            id="run-abandoned",
        ),
        pytest.param(
            lambda bundle: (
                bundle
                | {
                    "nodes": [
                        node | {"fault": {"kind": "error", "code": "RATE_LIMITED"}}
                        for node in bundle["nodes"]
                    ]
                }
            ),
            id="every-fixture-faulted",
        ),
        pytest.param(
            lambda bundle: bundle | {"path": bundle["path"] | {"actual": []}},
            id="agent-went-nowhere",
        ),
    ],
)
def test_no_span_ever_carries_an_error_status(bundle: dict[str, Any], perturb: Any) -> None:
    """Ground rule 2, on the wire. Five runs, and four of them look like failures.

    The service stores expectations and emits evidence; the three comparison
    helpers live in the client. An ``ERROR`` status is a verdict a dashboard
    acts on without anyone having chosen a comparison mode - and the faulted
    case is the clearest of the five, because a ``fault`` fixture is the authored
    world failing *on purpose*.
    """
    for span in readable_spans(trace_of(perturb(copy.deepcopy(bundle)))):
        assert span.status.status_code is StatusCode.UNSET, f"{span.name}: {span.status}"


def test_no_attribute_names_a_verdict(spans: tuple[ReadableSpan, ...]) -> None:
    """Both paths are on the root span; nothing compares them.

    A ``matches_expected`` here would be comparison logic in the server, and for
    this very dataset it would have been a *wrong* verdict - ``expected_path``
    revisits ``check_docs`` while ``fetch_step`` is idempotent per step key, so
    the reconstructed path cannot equal it however correctly an agent behaved.
    """
    found = attributes(spans[0])
    assert found[f"{ATTR_PREFIX}.path.expected"]
    assert found[f"{ATTR_PREFIX}.path.actual"]
    verdicts = [
        key
        for key in found
        if any(word in key for word in ("match", "pass", "fail", "ok", "grade", "verdict"))
    ]
    assert verdicts == [], verdicts


# ---------------------------------------------------------------- the attributes


def test_the_root_span_carries_the_expectation_the_actual_and_the_schema(
    bundle: dict[str, Any], spans: tuple[ReadableSpan, ...]
) -> None:
    """A consumer reading only the trace has what a grader reading the bundle has.

    Including the ``outcome_schema`` itself, which is ruling R-82 on a span: a
    trace carrying a *reference* would send a Langfuse reader back to the
    service.
    """
    found = attributes(spans[0])
    assert found[f"{ATTR_PREFIX}.expected.final"] == encoded(bundle["expected"]["final"])
    assert found[f"{ATTR_PREFIX}.actual"] == encoded(bundle["actual"])
    assert found[f"{ATTR_PREFIX}.outcome_schema"] == encoded(bundle["outcome_schema"])
    assert found[f"{ATTR_PREFIX}.expected.comparison"] == "subset"


def test_the_root_span_carries_the_pin_the_labels_and_the_warnings(
    bundle: dict[str, Any], spans: tuple[ReadableSpan, ...]
) -> None:
    """What a reader filters and groups a trace list by."""
    found = attributes(spans[0])
    assert found[f"{ATTR_PREFIX}.run.id"] == RUN_ID
    assert found[f"{ATTR_PREFIX}.dataset.version"] == 1
    assert found[f"{ATTR_PREFIX}.blueprint.version"] == "1.0.0"
    assert found[f"{ATTR_PREFIX}.blueprint.declared_version"] == "1.1.0"
    assert found[f"{ATTR_PREFIX}.warnings"] == ("blueprint_version_mismatch",)
    assert found[f"{ATTR_PREFIX}.label.persona"] == bundle["dataset"]["labels"]["persona"]
    assert found[f"{ATTR_PREFIX}.step_count"] == len(bundle["nodes"])


def test_the_model_travels_under_the_genai_convention(spans: tuple[ReadableSpan, ...]) -> None:
    """``gen_ai.request.model`` and ``gen_ai.provider.name``, so a GenAI-aware
    backend groups these traces by model with no agent-props knowledge."""
    found = attributes(spans[0])
    assert found["gen_ai.request.model"] == "claude-opus-5"
    assert found["gen_ai.provider.name"] == "anthropic"
    assert found[f"{ATTR_PREFIX}.model.version"] == "20260401"


def test_a_run_with_no_model_carries_no_model_attributes(bundle: dict[str, Any]) -> None:
    """The other end of the boundary. ``model`` is optional on a run.

    An attribute holding ``""`` would claim a provider named empty string, which
    a backend would then group by.
    """
    bundle["run"]["model"] = None
    found = dict(readable_spans(trace_of(bundle))[0].attributes or {})
    assert [key for key in found if key.startswith("gen_ai.")] == []
    assert f"{ATTR_PREFIX}.model.version" not in found


def test_each_step_span_carries_what_was_authored_served_and_recorded(
    bundle: dict[str, Any], spans: tuple[ReadableSpan, ...]
) -> None:
    """The per-node half of the evidence, on the span for that step."""
    node = bundle["nodes"][0]
    found = attributes(spans[1])
    assert found[f"{ATTR_PREFIX}.node.id"] == node["node_id"]
    assert found[f"{ATTR_PREFIX}.node.kind"] == "tool_call"
    assert found[f"{ATTR_PREFIX}.node.tool_name"] == node["tool_name"]
    assert found[f"{ATTR_PREFIX}.step.served"] == encoded(node["served"])
    assert found[f"{ATTR_PREFIX}.step.expected"] == encoded(node["expected"])
    assert found[f"{ATTR_PREFIX}.step.actual"] == encoded(node["actual"])
    assert found[f"{ATTR_PREFIX}.step.recorded"] is True


def test_the_unrecorded_step_says_so(spans: tuple[ReadableSpan, ...]) -> None:
    """``recorded: false`` with an empty ``actual``, which is not the same claim.

    An OTel attribute cannot hold ``null``, so an unrecorded actual is the empty
    string - and the boolean beside it is what stops a reader concluding the
    agent returned nothing.
    """
    decision = next(span for span in spans if span.name == "step check_docs")
    found = attributes(decision)
    assert found[f"{ATTR_PREFIX}.step.recorded"] is False
    assert found[f"{ATTR_PREFIX}.step.actual"] == ""
    assert found[f"{ATTR_PREFIX}.step.expected"] != ""


def test_a_node_expectation_reaches_the_step_it_is_about(bundle: dict[str, Any]) -> None:
    """``node_expectations`` is per node, so the span for that node carries it."""
    for node in bundle["nodes"]:
        if node["node_id"] == "request_docs":
            node["node_expectation"] = {"called": True}
    spans = readable_spans(trace_of(bundle))
    loops = [span for span in spans if span.name == "step request_docs"]
    assert loops and all(
        attributes(span)[f"{ATTR_PREFIX}.node.expectation"] == encoded({"called": True})
        for span in loops
    )


def test_the_resource_names_the_agent_and_this_system(spans: tuple[ReadableSpan, ...]) -> None:
    """``service.name`` is the agent; ``service.namespace`` says who emitted it."""
    resource = dict(spans[0].resource.attributes)
    assert resource["service.name"] == "location-onboarding"
    assert resource["service.namespace"] == SERVICE_NAMESPACE
    assert resource["service.version"] == "1.0.0"
    assert resource["service.instance.id"] == RUN_ID


# -------------------------------------------------- the dropped-attribute guard


def test_no_attribute_is_silently_dropped(bundle: dict[str, Any]) -> None:
    """Every attribute the spec named is on the span the SDK produced.

    The OTel SDK discards an attribute whose value is not a scalar or a
    homogeneous sequence and says so only in a log line, so a nested document
    passed as a ``dict`` would vanish from the trace while every other assertion
    in this file still passed. This is the comparison that notices.
    """
    spec = trace_of(bundle)
    realised = readable_spans(spec)
    for wanted, span in zip(spec.spans(), realised, strict=True):
        lost = set(wanted.attributes) - set(attributes(span))
        assert not lost, f"{wanted.name} lost {sorted(lost)} - an illegal attribute type"


def test_the_dropped_attribute_guard_catches_an_illegal_value(bundle: dict[str, Any]) -> None:
    """The guard above, run against the thing it forbids. Ruling R-77(e).

    A ``dict`` attribute is planted on the root span, and the loss is observed -
    so the comparison is known to be doing something rather than passing because
    every attribute happens to be legal today.
    """
    spec = trace_of(bundle)
    planted = spec.with_root(spec.root.with_attributes({f"{ATTR_PREFIX}.planted": {"a": 1}}))  # type: ignore[dict-item]
    root = readable_spans(planted)[0]
    lost = set(planted.root.attributes) - set(attributes(root))
    assert lost == {f"{ATTR_PREFIX}.planted"}, lost


def test_every_attribute_value_is_a_legal_otel_type(bundle: dict[str, Any]) -> None:
    """The same property from the other side, on the spec rather than the span.

    Two directions on one fact, and they fail differently: the comparison above
    notices a *loss*, and this names the offending value's type - which is what
    a reader of the failure needs.
    """
    for span in trace_of(bundle).spans():
        for key, value in span.attributes.items():
            legal = isinstance(value, str | bool | int | float) or (
                isinstance(value, tuple) and all(isinstance(item, str) for item in value)
            )
            assert legal, f"{span.name}: {key} is {type(value).__name__}"


# ------------------------------------------------------------------- the helpers


def test_encoded_is_stable_and_sorted() -> None:
    """A JSON attribute must be a function of the document, not of key order.

    Otherwise two exports of one run differ in a string, and a diff of two
    traces stops being a diff of two runs.
    """
    assert encoded({"b": 1, "a": 2}) == encoded({"a": 2, "b": 1}) == '{"a":2,"b":1}'
    assert encoded(None) == ""
    assert encoded([]) == "[]"


def test_resolved_endpoint_prefers_the_signal_specific_variable() -> None:
    """The SDK's own precedence, restated so a caller can be *told* the URL.

    The bare variable is a **base** URL by the SDK's convention, so ``/v1/traces``
    is appended to that one and to no other - getting this backwards would report
    an endpoint the export did not use, which is worse than reporting none.
    """
    specific, base = OTLP_ENDPOINT_ENV_VARS
    assert resolved_endpoint("http://explicit/v1/traces", {}) == "http://explicit/v1/traces"
    assert resolved_endpoint(None, {specific: "http://a/v1/traces"}) == "http://a/v1/traces"
    assert resolved_endpoint(None, {base: "http://b"}) == "http://b/v1/traces"
    assert resolved_endpoint(None, {base: "http://b/"}) == "http://b/v1/traces"
    assert (
        resolved_endpoint(None, {specific: "http://a/v1/traces", base: "http://b"})
        == "http://a/v1/traces"
    )
    assert resolved_endpoint(None, {}) == DEFAULT_OTLP_TRACES_ENDPOINT


def test_no_credential_variable_is_named_anywhere_in_this_package() -> None:
    """Ground rule 4 and ruling R-83, as a scan rather than a promise.

    A credential reaches a collector through the OTel SDK's own
    ``OTEL_EXPORTER_OTLP_HEADERS``, which the exporter reads. No module in
    `export/` may name it, or any other header or key variable - naming one
    would be the first line of the API-key handling this service must not have.
    """
    package = Path(trace_of.__module__.replace(".", "/")).parent
    sources = sorted((Path.cwd() / "src" / package).glob("*.py"))
    assert len(sources) == 3, sources
    forbidden = ("OTEL_EXPORTER_OTLP_HEADERS", "OTEL_EXPORTER_OTLP_TRACES_HEADERS", "api_key")
    for source in sources:
        text = source.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith(("#:", "#"))
        )
        for name in forbidden:
            assert name not in code.replace("``" + name + "``", ""), f"{source.name} names {name}"


def test_a_span_spec_child_list_is_replaceable_without_mutation() -> None:
    """The overlay mechanism `langfuse.py` is built on. Frozen, so it copies."""
    leaf = SpanSpec(name="leaf", span_id=1, start_ns=1, end_ns=2)
    parent = SpanSpec(name="parent", span_id=2, start_ns=1, end_ns=2, children=(leaf,))
    extended = parent.with_attributes({"a": "b"}).with_children(())
    assert parent.children == (leaf,) and parent.attributes == {}
    assert extended.children == () and extended.attributes == {"a": "b"}
