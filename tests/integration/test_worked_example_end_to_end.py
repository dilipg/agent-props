"""M8's first clause: a test agent walks `worked-example.md` section 7, end to end.

Nine steps, against a **live server** - the in-memory MCP ``Client`` connected
straight to the real ``MCPServer`` object, over a real store, with the real
`agentprops_client` package driving it. Nothing here is a mock: every call is an
MCP round trip, every answer comes from `service/`, and the store is whichever
backend ``--store`` names, so the script runs on SQLite by default and on
Postgres and Mongo when their containers are up (testing strategy layer 4: "the
worked example ... in every backend").

The script, and the two steps of it that are **wrong as written**
-----------------------------------------------------------------

``docs/worked-example.md`` section 7 is the authority, with two corrections that
are themselves authoritative:

- **Ruling R-52.** Step 7 says the ``pool_exhausted`` boundary is iteration 3.
  It is **2**: the golden ``request_docs`` pool has two entries, so iteration 0
  draws ``pools[0]``, iteration 1 draws ``pools[1]``, and iteration 2 already
  repeats ``pools[1]`` and warns. :data:`POOL_LENGTH` is read off the fixture
  rather than written as a literal, so padding the pool to match the document
  could not make this file silently agree - and R-44's drift guard would fail
  first anyway.
- **Ruling R-64.** Step 8 says "re-fetch step 2 with the same key". Step 2 was
  fetched by *tool name*, and by step 8 the run's path head has advanced to the
  end of the walk - so a tool-name re-fetch resolves against a different
  position and would fail for a reason that has nothing to do with idempotency.
  "The same key" means the same **step**, so step 8 re-fetches by ``node_id``.
  Idempotency is defined on the resolved key ``(run_id, resolved_node_id,
  iteration)``.

Step 7's two tool-name calls stay tool-name calls, because they are the point of
the whole script: **the same tool name resolving to different nodes depending on
where the run is.**

Every step asserts what the script says
---------------------------------------

Including the warnings. A walk that made the nine calls and checked only that
none of them failed would pass against a server that resolved every tool name to
the same node, never warned on an exhausted pool, and re-drew a fixture on every
fetch. So each step's assertions are written against the *specific* claim the
document makes at that step, and :func:`test_the_worked_example_end_to_end`
prints the walk as it goes, which is the report's step-by-step output.

Twice, and the second time synchronously
----------------------------------------

:func:`test_the_worked_example_end_to_end` drives :class:`AsyncRunClient` and
:func:`test_the_sync_client_walks_the_same_script` drives :class:`RunClient`
over the blocking portal, against the same server. Both are the milestone's
deliverable ("sync and async APIs"), and running the *script* through both is
the only assertion that says the sync facade is a real client rather than a
wrapper that type-checks.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

import pytest

from agentprops.models import WARNING_POOL_EXHAUSTED, Dataset
from agentprops.server import binding, mcp
from agentprops.service import FrozenClock, ServiceContext, blueprints
from agentprops.service.runs import STATUS_FINISHED, WARNING_RUN_ALREADY_FINISHED
from agentprops.storage import Store
from agentprops_client import Envelope, connect_async, grade
from agentprops_client.envelope import ToolError
from agentprops_client.run import AsyncRunClient
from agentprops_client.session import connect
from conftest import BLUEPRINT_FIXTURE, DATASET_FIXTURE, load_document

pytestmark = pytest.mark.integration

AGENT: Final = "location-onboarding"
BLUEPRINT_VERSION: Final = "1.0.0"

#: The tool name ``fetch_store_profile`` and ``recheck_store`` share. Step 7's
#: two tool-name calls are both this, and they must resolve to different nodes.
REPEATED_TOOL: Final = "delightree.stores.get"

POOL_NODE: Final = "request_docs"

#: **Two.** Read off the fixture, per ruling R-52 - exhaustion begins at this
#: index, not at 3.
POOL_LENGTH: Final[int] = len(load_document(DATASET_FIXTURE)["pools"][POOL_NODE])

#: The instant `priya-missing-docs.json` carries in ``validated_at``, so the
#: submitted dataset is comparable with the fixture field for field.
FIXTURE_INSTANT: Final = datetime(2026, 9, 8, 10, 14, 22, tzinfo=UTC)

#: The label selector step 6 uses. One dimension, deliberately: the point is
#: that a *label query* pins a dataset, which is how an eval suite addresses one.
SELECTOR: Final[dict[str, Any]] = {"labels": {"scenario": "missing-documents"}}

#: What the agent "produces" at each step of the walk, keyed by node. Derived
#: from the served fixture in :func:`walk`, except where the script needs a
#: specific value - so ``record_step`` is exercised with real documents rather
#: than with ``{}``.
GOLDEN: Final[dict[str, Any]] = load_document(DATASET_FIXTURE)
EXPECTED_PATH: Final[Sequence[str]] = GOLDEN["expected"]["expected_path"]
EXPECTED_FINAL: Final[dict[str, Any]] = GOLDEN["expected"]["final"]
DECLARED_COMPARISON: Final[str] = GOLDEN["expected"]["comparison"]


class Recorder:
    """Collects the walk, so the test can print it step by step and assert on it."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def say(self, step: str, detail: str) -> None:
        self.lines.append(f"{step:<9} {detail}")

    def report(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def context(store: Store) -> ServiceContext:
    """A service context over the ``--store`` backend, clock frozen at the fixture's instant.

    ``store`` is the conformance suite's own fixture, so this test inherits its
    per-backend parameterisation and its per-test isolation for free - and
    freezing the clock at ``validated_at`` is what lets step 4 compare the
    submitted dataset with the fixture on every field but ``id``.
    """
    return ServiceContext(store=store, clock=FrozenClock(FIXTURE_INSTANT))


@pytest.fixture
def seeded(context: ServiceContext) -> ServiceContext:
    """The published blueprint and the golden dataset, written straight to the store.

    For the tests below the authoring flow, which the async walk asserts in full
    once. The blueprint is published **first** because DS-001 requires a dataset
    to name an existing published one and the SQL DDL has a real foreign key on
    ``(agent_id, bp_version)``.
    """
    published = blueprints.upsert(context, load_document(BLUEPRINT_FIXTURE), publish=True)
    assert published.ok, published
    context.store.put_dataset(Dataset.model_validate(load_document(DATASET_FIXTURE)))
    return context


def content_for(section: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, Any]:
    """One section's ``content``, sliced out of the golden dataset by its pointers.

    Driven off the manifest the **server** returned rather than a table here,
    which is the same choice `test_worked_example_fill.py` makes and for the same
    reason: a caller with a dataset in hand and the manifest in front of it has
    to be able to work out what to send without knowing anything else.
    """
    return {target.lstrip("/"): document[target.lstrip("/")] for target in section["pointers"]}


def payload(envelope: Envelope, key: str) -> dict[str, Any]:
    """One named payload, with the envelope's success asserted on the way through."""
    assert envelope.ok, f"expected a success envelope, got {envelope.rules()}"
    return dict(envelope.payload(key))


async def author_the_dataset(client: AsyncRunClient, log: Recorder) -> str:
    """**Steps 1 to 4.** Publish, skeleton, five fills in order, submit.

    Through the client's own ``call`` escape hatch, because the authoring tools
    are not part of a run and have no typed wrapper - and because step 3's
    negative half needs the *envelope*: "filling entities before provenance must
    be rejected with SK-002" is a rule id, and a client that raised would make
    the assertion about an exception type instead.
    """
    published = await client.call(
        "blueprint_upsert", blueprint=load_document(BLUEPRINT_FIXTURE), publish=True
    )
    assert published.ok, published.rules()
    log.say("step 1", f"published {AGENT} {BLUEPRINT_VERSION}")

    started = payload(
        await client.call(
            "dataset_skeleton",
            agent_id=AGENT,
            version=BLUEPRINT_VERSION,
            labels=GOLDEN["labels"],
            seed=GOLDEN["seed"],
        ),
        "skeleton",
    )
    skeleton_id = str(started["skeleton_id"])
    manifest: list[dict[str, Any]] = list(started["manifest"])
    assert [section["id"] for section in manifest] == [
        "provenance",
        "entities",
        "nodes.core",
        "nodes.branches",
        "expected",
    ], "ruling R-06's five sections, in order"
    log.say("step 2", f"skeleton {skeleton_id} with a five-section manifest")

    out_of_order = await client.call(
        "dataset_fill_part",
        skeleton_id=skeleton_id,
        section="entities",
        content=content_for(manifest[1], GOLDEN),
    )
    assert out_of_order.rules() == ("SK-002",), (
        f"entities before provenance must be SK-002, got {out_of_order.rules()}"
    )
    log.say("step 3a", "entities before provenance rejected with SK-002")

    for section in manifest:
        filled = await client.call(
            "dataset_fill_part",
            skeleton_id=skeleton_id,
            section=section["id"],
            content=content_for(section, GOLDEN),
        )
        assert filled.ok, f"{section['id']}: {filled.rules()}"
        assert payload(filled, "fill")["filled"][-1] == section["id"]
    log.say("step 3b", "five sections filled in manifest order")

    submitted = payload(await client.call("dataset_submit", skeleton_id=skeleton_id), "dataset")
    differing = {field for field in GOLDEN if submitted[field] != GOLDEN[field]}
    assert differing == {"id"}, (
        f"the submitted dataset is the golden fixture but for its id: {sorted(differing)}"
    )
    log.say("step 4", f"submitted dataset {submitted['id']} v{submitted['version']}")
    return str(submitted["id"])


async def walk(client: AsyncRunClient, log: Recorder) -> dict[str, Any]:
    """**Step 7.** The expected path, with both tool-name calls and the pool loop.

    The order is the document's, and each line of it asserts the specific claim
    the document makes there rather than only that the call succeeded.
    """
    first = await client.fetch_step(node_id="receive_request")
    assert first.resolved_node_id == "receive_request"
    log.say("step 7a", "receive_request by node_id")

    early = await client.fetch_step(tool_name=REPEATED_TOOL)
    assert early.resolved_node_id == "fetch_store_profile", (
        "from the entry node the repeated tool name must resolve to fetch_store_profile, "
        "not recheck_store"
    )
    log.say("step 7b", f"{REPEATED_TOOL} -> {early.resolved_node_id} (by tool name)")

    await client.fetch_step(node_id="check_docs")

    inside: list[Mapping[str, Any]] = []
    for iteration in range(POOL_LENGTH):
        drawn = await client.fetch_step(node_id=POOL_NODE, iteration=iteration)
        assert [item.code for item in drawn.warnings] == [], (
            f"iteration {iteration} is inside a {POOL_LENGTH}-entry pool and must not warn"
        )
        inside.append(drawn.fixture)
        log.say("step 7c", f"{POOL_NODE} iteration {iteration} drew pools[{iteration}]")
    assert inside[0] != inside[-1], "the pool's entries differ, so a repeat is detectable"

    late = await client.fetch_step(tool_name=REPEATED_TOOL)
    assert late.resolved_node_id == "recheck_store", (
        "after the loop the same tool name must now resolve to recheck_store"
    )
    assert late.resolved_node_id != early.resolved_node_id, (
        "the two tool-name calls resolved to the same node, which is the whole point of step 7"
    )
    log.say("step 7d", f"{REPEATED_TOOL} -> {late.resolved_node_id} (by tool name, same argument)")

    for iteration in (POOL_LENGTH, POOL_LENGTH + 1):
        exhausted = await client.fetch_step(node_id=POOL_NODE, iteration=iteration)
        assert [item.code for item in exhausted.warnings] == [WARNING_POOL_EXHAUSTED], (
            f"iteration {iteration} draws past a {POOL_LENGTH}-entry pool and must warn "
            f"(ruling R-52: the boundary is {POOL_LENGTH}, not 3)"
        )
        detail = exhausted.warnings[0].detail
        assert detail == {
            "node_id": POOL_NODE,
            "iteration": iteration,
            "pool_length": POOL_LENGTH,
            "served_index": POOL_LENGTH - 1,
        }
        assert exhausted.fixture == inside[-1], "the repeated entry is the last authored one"
        log.say(
            "step 7e",
            f"{POOL_NODE} iteration {iteration} repeated pools[-1] with pool_exhausted",
        )

    for node_id in ("check_docs", "assign_training", "verify_compliance", "complete"):
        served = await client.fetch_step(node_id=node_id)
        assert served.resolved_node_id == node_id
    log.say("step 7f", "continued to complete")

    return dict(early.fixture)


def _first_visits(nodes: Sequence[str]) -> list[str]:
    """``nodes`` with every repeat removed, order preserved. First visit only."""
    seen: set[str] = set()
    ordered: list[str] = []
    for node in nodes:
        if node not in seen:
            seen.add(node)
            ordered.append(node)
    return ordered


def stored_path(run: Mapping[str, Any]) -> list[tuple[str, int]]:
    return [(entry["node_id"], entry["iteration"]) for entry in run["path"]]


async def test_the_worked_example_end_to_end(context: ServiceContext) -> None:
    """`worked-example.md` section 7, all nine steps, printed as it goes.

    The async client, over a real MCP session, against a real store. The
    ``binding`` context manager scopes the store to the block, so this test
    cannot leak into the next one.
    """
    log = Recorder()
    with binding(context):
        async with connect_async(mcp, agent_id=AGENT) as client:
            dataset_id = await author_the_dataset(client, log)

            # Step 5: the client constructed its own run id, before any call.
            assert client.run_id, "the client generated no run id"
            log.say("step 5", f"client constructed with run_id {client.run_id}")

            # Step 6: run_start pins the dataset.
            started = await client.run_start(SELECTOR)
            assert started.dataset_id == dataset_id, "the label query pinned another dataset"
            assert started.dataset_version == 1
            assert started.blueprint_version == BLUEPRINT_VERSION
            assert [item.code for item in started.warnings] == []
            log.say(
                "step 6",
                f"run_start pinned {started.dataset_id} v{started.dataset_version} "
                f"@ blueprint {started.blueprint_version}",
            )

            step_two_fixture = await walk(client, log)

            # Step 8: the idempotency proof. By node_id, per ruling R-64.
            before = payload(await client.call("run_get", run_id=client.run_id), "run")
            replayed = await client.fetch_step(node_id="fetch_store_profile")
            after = payload(await client.call("run_get", run_id=client.run_id), "run")

            assert json.dumps(replayed.fixture) == json.dumps(step_two_fixture), (
                "the re-fetch is not byte-identical to the original"
            )
            assert stored_path(after) == stored_path(before), "the re-fetch appended a path entry"
            assert after["steps"] == before["steps"], "the re-fetch wrote a step"
            log.say(
                "step 8",
                f"re-fetched fetch_store_profile by node_id: byte-identical, "
                f"path still {len(after['path'])} entries",
            )

            # The agent reports what it produced, then closes the run.
            recorded = await client.record_step(
                {"onboarding_status": "complete"}, node_id="complete"
            )
            assert recorded.resolved_node_id == "complete"
            outcome = {**EXPECTED_FINAL, "audit_log_id": "AL-2210"}
            run = await client.run_finish(outcome)
            assert run["status"] == STATUS_FINISHED
            assert run["outcome"] == outcome
            log.say("step 8b", f"record_step + run_finish -> status {run['status']}")

            # Step 9: grade the recorded outcome with the declared helper.
            verdict = grade(DECLARED_COMPARISON, EXPECTED_FINAL, run["outcome"])
            assert verdict.ok, f"step 9 must pass: {verdict}"
            assert verdict.mode == "subset"
            log.say("step 9", f"grade({DECLARED_COMPARISON}) -> {verdict}")

    print("\n" + log.report())
    assert [line.split()[1] for line in log.lines] == [
        "1",
        "2",
        "3a",
        "3b",
        "4",
        "5",
        "6",
        "7a",
        "7b",
        "7c",
        "7c",
        "7d",
        "7e",
        "7e",
        "7f",
        "8",
        "8b",
        "9",
    ], f"the walk did not reach every step of the script:\n{log.report()}"


async def test_the_reconstructed_path_is_the_calls_that_were_made(
    context: ServiceContext,
) -> None:
    """The path is reconstructed from call order, so it must be the walk exactly.

    This is the one thing a step-by-step test can get wrong while every
    individual step passes: each ``fetch_step`` could answer correctly and the
    *run* still record a different traversal. So the stored path is compared
    against the script's own call sequence, key for key.

    **It is deliberately not ``expected.expected_path``, and both reasons are
    worth stating**, because a test that asserted they matched would have to be
    "fixed" by weakening the walk:

    1. the declared path visits ``request_docs`` **once** - one outstanding
       document, one loop iteration - while section 7's script draws four on
       purpose, to reach the ``pool_exhausted`` boundary either side of it;
    2. the declared path visits ``check_docs`` **twice**, and a reconstructed
       path cannot. ``fetch_step`` is idempotent on ``(run_id,
       resolved_node_id, iteration)``, so the loop's second visit to a
       ``pool: false`` node is *the same step* - it replays and appends no path
       entry. A declared path is a **traversal**, which may revisit a node; a
       reconstructed path is the distinct steps that were served, in serve
       order. Only a pool node can appear twice, because only a pool node has a
       second iteration.

    So what is asserted against the author's declaration is the part that
    survives that difference: the order in which each declared node was **first**
    reached.
    """
    with binding(context):
        async with connect_async(mcp, agent_id=AGENT) as client:
            await author_the_dataset(client, Recorder())
            await client.run_start(SELECTOR)
            await walk(client, Recorder())
            run = payload(await client.call("run_get", run_id=client.run_id), "run")

    assert stored_path(run) == [
        ("receive_request", 0),
        ("fetch_store_profile", 0),
        ("check_docs", 0),
        (POOL_NODE, 0),
        (POOL_NODE, 1),
        ("recheck_store", 0),
        (POOL_NODE, POOL_LENGTH),
        (POOL_NODE, POOL_LENGTH + 1),
        ("assign_training", 0),
        ("verify_compliance", 0),
        ("complete", 0),
    ], "the reconstructed path is not the sequence of calls the script made"

    visited = [node for node, _ in stored_path(run)]
    assert set(EXPECTED_PATH) <= set(visited), (
        f"these declared nodes were never visited: {sorted(set(EXPECTED_PATH) - set(visited))}"
    )
    assert _first_visits(visited) == _first_visits(EXPECTED_PATH), (
        "the walk reached the declared nodes in a different order than expected_path declares"
    )
    assert visited.count("check_docs") == 1, (
        "check_docs was served twice, so fetch_step is not idempotent on a re-visited node"
    )


def test_the_sync_client_walks_the_same_script(seeded: ServiceContext) -> None:
    """The sync facade, over the blocking portal, against the same live server.

    A *synchronous* test function, so the portal is doing real work: it runs the
    session on a worker thread and enters and exits it in one portal task, which
    is the cancel-scope constraint `session.py` records. If that were wrong this
    would raise ``RuntimeError: Attempted to exit cancel scope in a different
    task`` on the way out rather than failing an assertion.

    The script's load-bearing steps are all here - both tool-name resolutions,
    the R-52 boundary, the R-64 re-fetch, ``record_step``, ``run_finish`` and the
    grade - so "sync and async APIs" is a claim about two working clients rather
    than one client and a type-checked shell. The authoring half is asserted in
    full once, in the async walk; repeating it here would make this test about
    ``dataset_fill_part``.
    """
    with binding(seeded), connect(mcp, agent_id=AGENT) as client:
        assert client.run_id, "the sync client generated no run id"
        started = client.run_start(SELECTOR)
        assert started.dataset_version == 1
        assert started.blueprint_version == BLUEPRINT_VERSION

        assert client.fetch_step(node_id="receive_request").resolved_node_id == "receive_request"
        early = client.fetch_step(tool_name=REPEATED_TOOL)
        assert early.resolved_node_id == "fetch_store_profile"
        client.fetch_step(node_id="check_docs")

        for iteration in range(POOL_LENGTH):
            assert client.fetch_step(node_id=POOL_NODE, iteration=iteration).warnings == ()
        late = client.fetch_step(tool_name=REPEATED_TOOL)
        assert late.resolved_node_id == "recheck_store", "the sync client resolves by position too"

        exhausted = client.fetch_step(node_id=POOL_NODE, iteration=POOL_LENGTH)
        assert [item.code for item in exhausted.warnings] == [WARNING_POOL_EXHAUSTED]

        replayed = client.fetch_step(node_id="fetch_store_profile")
        assert json.dumps(replayed.fixture) == json.dumps(early.fixture), (
            "the sync re-fetch is not byte-identical"
        )

        recorded = client.record_step(
            {"store": {"status": "pending_docs"}}, node_id="fetch_store_profile"
        )
        assert recorded.actual == {"store": {"status": "pending_docs"}}
        run = client.run_finish(EXPECTED_FINAL)
        assert run["status"] == STATUS_FINISHED
        assert grade(DECLARED_COMPARISON, EXPECTED_FINAL, run["outcome"]).ok


def test_a_closed_run_still_serves_through_the_client(seeded: ServiceContext) -> None:
    """Ruling R-54(c) as a caller experiences it: served, with a warning, never refused.

    The client is where "the service never gates" is either useful or invisible:
    a warning that arrives as an exception is a gate wearing a different hat. So
    this asserts the fixture comes back **and** the warning is on it, and then
    asks for a step the run had *not* served before the close - which a
    ``fetch_step`` that short-circuited to stored steps would refuse.
    """
    with binding(seeded), connect(mcp, agent_id=AGENT) as client:
        client.run_start(SELECTOR)
        before = client.fetch_step(node_id="receive_request")
        client.run_finish(EXPECTED_FINAL)

        after = client.fetch_step(node_id="receive_request")
        assert after.fixture == before.fixture, "a closed run stopped serving"
        assert [item.code for item in after.warnings] == [WARNING_RUN_ALREADY_FINISHED]

        fresh = client.fetch_step(node_id="verify_compliance")
        assert fresh.output["compliant"] is True, "a step unserved before the close was refused"
        assert [item.code for item in fresh.warnings] == [WARNING_RUN_ALREADY_FINISHED]


def test_an_unknown_run_reaches_the_caller_as_a_finding(seeded: ServiceContext) -> None:
    """The failure path through the client, so a caller can act on ``rule``.

    ``fetch_step`` cannot answer with a :class:`~agentprops_client.run.Step` for
    a run that does not exist, so it raises - and the exception carries the
    finding rather than a message. Asserting the *rule id* here is the repo's own
    rule ("assert on rule ids, never on messages") applied to the client.
    """
    with (
        binding(seeded),
        connect(mcp, agent_id=AGENT, run_id="nobodys-run-id") as client,
        pytest.raises(ToolError) as raised,
    ):
        client.fetch_step(node_id="receive_request")
    assert raised.value.rules() == ("RT-E03",)
    assert raised.value.findings[0].pointer == "/run_id"


def test_the_client_records_an_actual_for_every_served_step(seeded: ServiceContext) -> None:
    """``record_step`` over a whole walk, and the evidence that survives it.

    The M8 script records one outcome; a real harness records an actual per step,
    and that is the shape M10's evidence bundle has to have something to
    assemble from. Every step served is recorded, the recorded actual comes back
    off ``run_get``, and the served fixture is untouched by the write - which is
    ruling R-33's division between ``upsert_step`` and ``set_step_actual``, as a
    caller sees it.
    """
    with binding(seeded), connect(mcp, agent_id=AGENT) as client:
        client.run_start(SELECTOR)
        served: dict[str, Any] = {}
        for node_id in ("receive_request", "fetch_store_profile", "check_docs"):
            step = client.fetch_step(node_id=node_id)
            served[node_id] = dict(step.fixture)
            client.record_step(step.output, node_id=node_id)

        run = payload(client.call("run_get", run_id=client.run_id), "run")

    recorded = {step["node_id"]: step for step in run["steps"]}
    assert set(recorded) == set(served)
    for node_id, step in recorded.items():
        assert step["served"] == served[node_id], "recording an actual rewrote the fixture"
        assert step["actual"] == served[node_id]["output"], "the actual was not recorded"
        assert step["recorded_at"] is not None
