"""M10's clause 1, against a **real** collector: one trace, one span per step.

> a completed run appears as a trace in a local OTel collector with one span per
> step

A real collector and not a mock, because the whole claim is about something
outside this repository. `docker-compose.yml`'s ``otel`` profile runs
``otel/opentelemetry-collector-contrib`` with an OTLP/HTTP receiver and a file
exporter, and this test reads the spans back out of that file and counts them -
so what is asserted is what the collector *received and wrote*, not what this
service believes it sent.

Start it with::

    docker compose --profile otel up -d otel-collector
    uv run pytest -m integration -k otel_collector

Without it, every test here **skips with the endpoint named** in the reason -
ruling R-60's standing requirement, because a count with no URL beside it is
evidence of very little.

The port is 4418, not 4318 (ruling R-60)
----------------------------------------

4318 is OTLP/HTTP's default, so any other collector on a developer's machine
answers it. A clause-1 test that passed against someone else's collector would
be worth exactly what M7's Mongo run against a foreign database was worth - and
that happened three times before the ports moved. 27017 became 27117, 5432
became 5442, and 4318 becomes 4418 for the same reason. Only
``AGENTPROPS_OTLP_PORT`` or ``AGENTPROPS_TEST_OTLP_URL`` can point this
somewhere else, which is a deliberate act.

What was probed, and what was not
---------------------------------

R-79's corollary: a claim about anything outside the diff is the claim no test
in the diff will catch, so here is what was and was not established.

**Probed.** That collector, at that version, accepts the trace this service
emits over OTLP/HTTP and writes ten spans for a nine-step run - one root and one
per step, sharing a trace id, each step's parent being the root, with every
``agentprops.*`` and ``langfuse.*`` attribute intact after a real
protobuf/JSON round trip. Every assertion below reads the collector's own output
file.

**Not probed.** That *Langfuse's server* ingests those attributes as a dataset
run. That needs a project and an API key, and ground rule 4 keeps key handling
out of `src/` - so the Langfuse half rests on their documented ingestion
contract (quoted in `DECISIONS.md` and in `export/langfuse.py`) plus the
round-trip proved here, and the report says so in those words.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from agentprops.export import langfuse as langfuse_export
from agentprops.export import otel
from agentprops.service import FrozenClock, ServiceContext, sqlite_context
from agentprops.service.evidence import bundle, export
from agentprops.storage import SqlStore
from conftest import FROZEN_NOW
from evidencewalk import STEPS, walk

pytestmark = pytest.mark.integration

#: Where the compose ``otel`` profile publishes the OTLP/HTTP receiver, and what
#: this suite dials with no environment set at all. **4418, not 4318** - see the
#: module docstring.
OTLP_PORT_ENV_VAR: Final = "AGENTPROPS_OTLP_PORT"
OTLP_URL_ENV_VAR: Final = "AGENTPROPS_TEST_OTLP_URL"
HEALTH_PORT_ENV_VAR: Final = "AGENTPROPS_OTLP_HEALTH_PORT"
DEFAULT_OTLP_PORT: Final = "4418"
DEFAULT_HEALTH_PORT: Final = "13233"

#: Where the collector's file exporter writes, from the host's side. The compose
#: file mounts ``./.otel`` into the container as ``/data``.
TRACES_FILE: Final[Path] = Path(__file__).parents[2] / ".otel" / "traces.jsonl"

#: How long to wait for the collector to flush a batch to that file. The file
#: exporter buffers and the collector config sets ``flush_interval: 1s``, so an
#: immediate read can legitimately find nothing.
#:
#: **Do not delete that file while the collector is running.** The exporter holds
#: it open, so a deletion leaves it writing to an inode nothing can read and
#: every test here reports zero spans - which was measured, once, and reads
#: exactly like a service that stopped exporting. Restart the collector if the
#: file needs clearing; the tests do not need it cleared, because each filters by
#: its own derived trace id.
FLUSH_TIMEOUT_SECONDS: Final = 15.0


def traces_url() -> str:
    """The OTLP/HTTP traces endpoint this suite posts to."""
    if override := os.environ.get(OTLP_URL_ENV_VAR):
        return override
    port = os.environ.get(OTLP_PORT_ENV_VAR, DEFAULT_OTLP_PORT)
    return f"http://localhost:{port}/v1/traces"


def health_url() -> str:
    """The collector's health-check extension, which is what "is it up" asks."""
    port = os.environ.get(HEALTH_PORT_ENV_VAR, DEFAULT_HEALTH_PORT)
    return f"http://localhost:{port}/"


def collector_reachable() -> bool:
    """Whether a collector is answering. Health endpoint, not a TCP connect.

    A TCP probe succeeds while the receiver is still starting, which is the same
    lesson the Mongo healthcheck records - ``pg_isready`` with no arguments
    answers for the wrong database, and a socket answers before a server does.
    """
    try:
        with urlopen(health_url(), timeout=2) as response:
            return bool(200 <= response.status < 300)
    except (URLError, OSError, ValueError):
        return False


REACHABLE: Final[bool] = collector_reachable()

#: The skip reason **names both URLs**, per ruling R-60: a reader who sees this
#: skip must be able to tell whether the suite was pointed at anything.
SKIP_REASON: Final = (
    f"no OTel collector at {health_url()} (traces would go to {traces_url()}); "
    f"start one with `docker compose --profile otel up -d otel-collector`"
)


@pytest.fixture
def run_id(request: pytest.FixtureRequest) -> str:
    """A run id unique to this test, inside contracts 2.3's ``^[A-Za-z0-9_.:-]+$``.

    Per test, and that is a real consequence of the design rather than hygiene:
    the trace id is derived from the run id, so two tests exporting the same run
    would write to the **same trace** - and the collector's output file appends,
    so the second test reading "the spans for this trace" would find the first
    test's as well. A distinct run id makes the filter exact.
    """
    return f"m10-collector-{request.node.name.replace('_', '-')}"[:128]


@pytest.fixture(autouse=True)
def endpoint(monkeypatch: pytest.MonkeyPatch) -> str:
    """Point ``run_export`` at the collector **under test**, not at the SDK default.

    The service reads the OpenTelemetry SDK's own
    ``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`` and adds no configuration of its own,
    so this is how a caller aims an export - and setting it here rather than
    expecting it in the shell is what stops this suite passing or failing on
    whatever happened to be exported to the SDK's default 4318. Autouse,
    because a test that forgot it would post to 4318 and then look for its
    spans in *this* collector's file, which is a failure that reads as a
    service defect.
    """
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", traces_url())
    return traces_url()


@pytest.fixture
def context(tmp_path: Path, run_id: str) -> Iterator[ServiceContext]:
    """A file-backed SQLite store with a frozen clock, and one completed run."""
    built = sqlite_context(tmp_path / "agentprops.db", clock=FrozenClock(FROZEN_NOW))
    try:
        yield walk(built, run_id)
    finally:
        if isinstance(built.store, SqlStore):
            built.store.dispose()


def spans_written(trace_id: str) -> list[dict[str, Any]]:
    """The **distinct** spans the collector wrote for ``trace_id``, from its own file.

    The file exporter appends OTLP/JSON, one resource-spans envelope per line, so
    the file is read whole and filtered by trace id rather than truncated between
    tests - and it must not be deleted while the collector holds it open (see
    :data:`FLUSH_TIMEOUT_SECONDS`).

    **De-duplicated by span id, and that is a consequence of derived ids rather
    than tidiness.** A span id is ``blake2b(run_id + the step's identity)``, so
    running this suite twice re-exports the *same* trace with the *same* ten span
    ids and the file then holds twenty lines describing ten spans. Counting raw
    lines would make the assertion a fact about how many times pytest has run -
    measured, on the third run, as "30 spans reached the collector". Counting
    distinct span ids makes it a fact about the trace, which is what clause 1 is
    about.
    """
    if not TRACES_FILE.exists():
        return []
    found: dict[str, dict[str, Any]] = {}
    for line in TRACES_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        for resource in json.loads(line).get("resourceSpans", []):
            for scope in resource.get("scopeSpans", []):
                for span in scope.get("spans", []):
                    if span.get("traceId") == trace_id:
                        found[span["spanId"]] = span | {"_resource": resource.get("resource", {})}
    return list(found.values())


def wait_for(trace_id: str, count: int) -> list[dict[str, Any]]:
    """``count`` spans for ``trace_id``, or whatever arrived before the timeout.

    A single decision site and one exit: poll until the count is reached or the
    budget is spent, then return what there is and let the caller assert. A loop
    that returned early on some other condition would be the smell this build
    keeps finding.
    """
    from time import monotonic, sleep

    deadline = monotonic() + FLUSH_TIMEOUT_SECONDS
    found = spans_written(trace_id)
    while len(found) < count and monotonic() < deadline:
        sleep(0.25)
        found = spans_written(trace_id)
    return found


def attributes(span: dict[str, Any]) -> dict[str, Any]:
    """One span's attributes as a plain mapping. OTLP/JSON boxes every value."""
    return {item["key"]: _unboxed(item["value"]) for item in span.get("attributes", [])}


def _unboxed(value: dict[str, Any]) -> Any:
    """An OTLP ``AnyValue`` as a Python value.

    OTLP/JSON tags every attribute with its type - ``stringValue``,
    ``intValue``, ``boolValue``, ``arrayValue`` - and an ``intValue`` arrives as
    a **string**, which is the protocol's own encoding of a 64-bit integer in
    JSON. Unboxing here rather than in each assertion is what stops a test
    comparing ``"1"`` with ``1`` and reporting it as a service defect.
    """
    if "arrayValue" in value:
        return [_unboxed(item) for item in value["arrayValue"].get("values", [])]
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in value:
            return value[key]
    if "intValue" in value:
        return int(value["intValue"])
    return None


@pytest.mark.skipif(not REACHABLE, reason=SKIP_REASON)
def test_a_completed_run_arrives_as_one_trace_with_one_span_per_step(
    context: ServiceContext, run_id: str
) -> None:
    """**Clause 1.** Ten spans in the collector for a nine-step run.

    The count comes from the collector's output file, not from the exporter's
    return value: ``run_export`` reporting ``spans: 10`` would be this service
    agreeing with itself, and the clause is about what a collector received.
    """
    reply = export(context, run_id, "otel")
    assert reply.ok, reply
    reference = reply.data["external_ref"]  # type: ignore[union-attr]
    assert reference["endpoint"] == traces_url(), (
        f"the export went to {reference['endpoint']}, not to the collector under test"
    )

    spans = wait_for(reference["trace_id"], 1 + len(STEPS))
    assert len(spans) == 1 + len(STEPS) == 10, (
        f"{len(spans)} spans reached {traces_url()} for trace {reference['trace_id']}; "
        f"expected one for the run plus one for each of the {len(STEPS)} steps"
    )

    roots = [span for span in spans if not span.get("parentSpanId")]
    assert [span["name"] for span in roots] == ["run location-onboarding"]
    steps = [span for span in spans if span.get("parentSpanId")]
    assert sorted(span["name"] for span in steps) == sorted(
        f"step {node_id}" for node_id, _ in STEPS
    )
    assert {span["parentSpanId"] for span in steps} == {roots[0]["spanId"]}


@pytest.mark.skipif(not REACHABLE, reason=SKIP_REASON)
def test_the_evidence_survives_the_round_trip(context: ServiceContext, run_id: str) -> None:
    """What the collector holds is what the bundle said. Attributes, not just counts.

    A trace with the right number of spans and empty attributes would satisfy
    clause 1's arithmetic and be useless, so this asserts the payload: the
    expectation, the recorded outcome and ruling R-82's ``outcome_schema``, all
    compared against the bundle `run_evidence` would hand a grader.
    """
    document, unresolved = bundle(context, run_id)
    assert document is not None and unresolved == []

    reply = export(context, run_id, "otel")
    assert reply.ok, reply
    trace_id = reply.data["external_ref"]["trace_id"]  # type: ignore[union-attr]
    spans = wait_for(trace_id, 1 + len(STEPS))
    root = next(span for span in spans if not span.get("parentSpanId"))
    found = attributes(root)

    prefix = otel.ATTR_PREFIX
    assert found[f"{prefix}.run.id"] == run_id
    assert found[f"{prefix}.expected.comparison"] == document["comparison"]
    assert json.loads(found[f"{prefix}.expected.final"]) == document["expected"]["final"]
    assert json.loads(found[f"{prefix}.actual"]) == document["actual"]
    assert json.loads(found[f"{prefix}.outcome_schema"]) == document["outcome_schema"]
    assert found[f"{prefix}.step_count"] == len(STEPS)
    assert found[f"{prefix}.path.expected"] == document["path"]["expected"]
    assert found[f"{prefix}.path.actual"] == document["path"]["actual"]

    served = {
        (attributes(span)[f"{prefix}.node.id"], attributes(span)[f"{prefix}.node.iteration"])
        for span in spans
        if span.get("parentSpanId")
    }
    assert served == set(STEPS)


@pytest.mark.skipif(not REACHABLE, reason=SKIP_REASON)
def test_the_langfuse_attributes_survive_the_round_trip(
    context: ServiceContext, run_id: str
) -> None:
    """Ruling R-83's linkage, as bytes a collector actually parsed.

    This is the half of the Langfuse claim that *is* testable without an API
    key: their required experiment attributes reach an OTLP collector intact,
    on every span, with ``root_observation_id`` equal to the root span's own
    id - which is the equality their contract states. Whether Langfuse's own
    server then files it as a dataset run is not established here, and the
    module docstring says so.
    """
    reply = export(context, run_id, "langfuse")
    assert reply.ok, reply
    trace_id = reply.data["external_ref"]["trace_id"]  # type: ignore[union-attr]
    spans = wait_for(trace_id, 1 + len(STEPS))
    root = next(span for span in spans if not span.get("parentSpanId"))

    for span in spans:
        found = attributes(span)
        for name in sorted(langfuse_export.LANGFUSE_REQUIRED_EXPERIMENT_ATTRS):
            assert found.get(name), f"{span['name']} lost {name} in transit"
        assert found[langfuse_export.LANGFUSE_ITEM_ROOT_OBSERVATION_ID] == root["spanId"]

    root_found = attributes(root)
    for name in sorted(langfuse_export.LANGFUSE_REQUIRED_ITEM_ROOT_ATTRS):
        assert root_found.get(name), f"the root span lost {name} in transit"
    assert root_found[f"{otel.ATTR_PREFIX}.run.id"] == run_id, (
        "the overlay dropped the vendor-neutral half"
    )


@pytest.mark.skipif(not REACHABLE, reason=SKIP_REASON)
def test_exporting_twice_lands_in_the_same_trace(context: ServiceContext, run_id: str) -> None:
    """The trace id is a function of the run id, so a retry is not a second trace.

    Which is also why nothing is stored: an ``external_ref`` that can be
    recomputed does not need a column (see `service/evidence.py`).
    """
    first = export(context, run_id, "otel")
    second = export(context, run_id, "otel")
    assert first.ok and second.ok
    assert (
        first.data["external_ref"]["trace_id"]  # type: ignore[union-attr]
        == second.data["external_ref"]["trace_id"]  # type: ignore[union-attr]
        == f"{otel.derived_trace_id(run_id):032x}"
    )


@pytest.mark.skipif(not REACHABLE, reason=SKIP_REASON)
def test_the_collector_is_the_one_this_suite_thinks_it_is() -> None:
    """The guard against the failure ruling R-60 exists for.

    A foreign collector on 4318 would satisfy every assertion above, so the
    reachability check and the endpoint the export actually used are asserted to
    be the *same* place - and the port is asserted not to be OTLP's default,
    because that is the value a well-meaning edit would put back.
    """
    assert traces_url().endswith("/v1/traces")
    assert ":4318/" not in traces_url(), (
        "4318 is OTLP's default and any collector on this machine answers it (R-60)"
    )
    assert DEFAULT_OTLP_PORT == "4418"
    assert REACHABLE, SKIP_REASON
