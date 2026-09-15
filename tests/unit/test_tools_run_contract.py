"""The six run tools over a real in-memory MCP session.

The same division of labour `test_tools_contract.py` records: **envelopes,
warnings and determinism** here, behaviour in `test_service_runs.py` and
`test_service_resolution.py`. Duplicating the service suite through the client
would test the SDK's serialisation another fifty times.

What genuinely needs a client, and is therefore here rather than there:

- the six tools answer the section 1 envelope over a round trip at all, which
  is what registers them for `test_tool_surface.py`'s coverage guard;
- a malformed argument becomes ``AP-001`` rather than an MCP protocol error -
  the property `server/args.py` exists for, and the one that would break
  silently if a parameter were ever annotated with its real type;
- ``fetch_step``'s idempotency holds **on the wire**: two calls, byte-identical
  ``structured_content``. The service suite proves the stored document is what
  comes back; this proves nothing in the serialisation layer reintroduces a
  difference, which is the form the M6 gate is actually written in.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agentprops.models import Dataset
from agentprops.service import ServiceContext, blueprints
from conftest import load_document
from toolclient import connected, invoke

AGENT = "location-onboarding"
RUN_ID = "m6-wire-run"
PRIYA = "3f8c1a20-0000-4000-8000-000000000001"
POOL_NODE = "request_docs"
REPEATED_TOOL = "acme.stores.get"


@pytest.fixture
def seeded(
    context: ServiceContext, blueprint_document: dict[str, Any], dataset_document: dict[str, Any]
) -> ServiceContext:
    """The published blueprint and `priya`, written through the store.

    The same choice `test_tools_contract.py` records: the golden fixture carries
    its own ``id`` and ``validated_at``, which the authoring flow mints, so a
    submitted dataset is not byte-identical to the fixture these assertions
    compare against.
    """
    blueprints.upsert(context, blueprint_document, publish=True)
    context.store.put_dataset(Dataset.model_validate(dataset_document))
    return context


def rules(envelope: dict[str, Any]) -> list[str]:
    return [finding["rule"] for finding in envelope["errors"]]


def codes(envelope: dict[str, Any]) -> list[str]:
    return [item["code"] for item in envelope["warnings"]]


async def test_run_start_returns_the_run_and_the_pin(seeded: ServiceContext) -> None:
    """contracts section 4's payload, under the one named key M4's convention uses."""
    envelope = await invoke(
        seeded, "run_start", run_id=RUN_ID, agent_id=AGENT, selector={"dataset_id": PRIYA}
    )
    assert envelope["ok"] is True
    assert set(envelope["data"]) == {"start"}
    payload = envelope["data"]["start"]
    assert set(payload) == {"run", "pin"}
    assert payload["pin"]["dataset_version"] == 1
    assert payload["run"]["id"] == RUN_ID


async def test_run_start_warns_on_a_declared_version_mismatch(seeded: ServiceContext) -> None:
    envelope = await invoke(
        seeded,
        "run_start",
        run_id=RUN_ID,
        agent_id=AGENT,
        selector={"labels": {"scenario": "missing-documents"}},
        declared_blueprint_version="2.0.0",
        model={"provider": "anthropic", "name": "claude-opus-5", "version": "20260401"},
        run_class="eval",
    )
    assert envelope["ok"] is True
    assert codes(envelope) == ["blueprint_version_mismatch"]
    assert envelope["data"]["start"]["run"]["run_class"] == "eval"


async def test_a_diverging_replay_warns_over_the_wire(seeded: ServiceContext) -> None:
    """Ruling R-53 through the transport: same run, warning attached, still ``ok``.

    The wire is where this matters most, because the caller that hits it is a
    client retrying after a network blip - and the two things it must never see
    are a second run and a failure.
    """
    async with connected(seeded) as client:
        first = await invoke(
            client, "run_start", run_id=RUN_ID, agent_id=AGENT, selector={"dataset_id": PRIYA}
        )
        assert codes(first) == []
        again = await invoke(
            client,
            "run_start",
            run_id=RUN_ID,
            agent_id=AGENT,
            selector={"labels": {"scenario": "no-such-scenario"}},
        )
        assert again["ok"] is True
        assert codes(again) == ["run_start_mismatch"]
        assert again["data"]["start"]["pin"] == first["data"]["start"]["pin"]

        listed = await invoke(client, "run_find")
        assert len(listed["data"]["runs"]) == 1, "a replay must not create a second run"


async def test_a_malformed_argument_is_an_envelope_and_not_a_protocol_error(
    seeded: ServiceContext,
) -> None:
    """`server/args.py`'s whole reason for annotating every parameter ``object``.

    ``invoke`` asserts ``is_error is False`` on every call, so reaching the
    assertions below is itself the proof that nothing raised.
    """
    async with connected(seeded) as client:
        bad_selector = await invoke(
            client, "run_start", run_id=RUN_ID, agent_id=AGENT, selector="priya"
        )
        assert rules(bad_selector) == ["AP-001"]
        assert bad_selector["errors"][0]["pointer"] == "/selector"

        bad_model = await invoke(
            client,
            "run_start",
            run_id=RUN_ID,
            agent_id=AGENT,
            selector={"dataset_id": PRIYA},
            model="claude",
        )
        assert rules(bad_model) == ["AP-001"]

        bad_iteration = await invoke(client, "fetch_step", run_id=RUN_ID, iteration="two")
        assert rules(bad_iteration) == ["AP-001"]
        assert bad_iteration["errors"][0]["pointer"] == "/iteration"

        bad_run_id = await invoke(client, "run_get", run_id=17)
        assert rules(bad_run_id) == ["AP-001"]

        bad_limit = await invoke(client, "run_find", limit=True)
        assert rules(bad_limit) == ["AP-001"], "true is not an integer"

        bad_actual = await invoke(
            client, "record_step", run_id=RUN_ID, actual="complete", node_id=POOL_NODE
        )
        assert rules(bad_actual) == ["AP-001"]
        assert bad_actual["errors"][0]["pointer"] == "/actual"

        bad_outcome = await invoke(
            client, "run_finish", run_id=RUN_ID, outcome=["complete"], status="finished"
        )
        assert rules(bad_outcome) == ["AP-001"]
        assert bad_outcome["errors"][0]["pointer"] == "/outcome"

        bad_status = await invoke(
            client, "run_finish", run_id=RUN_ID, outcome={}, status=["finished"]
        )
        assert rules(bad_status) == ["AP-001"]
        assert bad_status["errors"][0]["pointer"] == "/status"


async def test_fetch_step_is_byte_identical_on_the_wire(seeded: ServiceContext) -> None:
    """M6's first acceptance clause, in the form it is written: byte-identical.

    Serialised with ``sort_keys=False`` so key *order* counts too - the whole
    response, not a field-by-field comparison that would tolerate a reordering.
    """
    golden = load_document("datasets/priya-missing-docs.json")
    async with connected(seeded) as client:
        await invoke(
            client, "run_start", run_id=RUN_ID, agent_id=AGENT, selector={"dataset_id": PRIYA}
        )
        first = await invoke(client, "fetch_step", run_id=RUN_ID, node_id="fetch_store_profile")
        second = await invoke(client, "fetch_step", run_id=RUN_ID, node_id="fetch_store_profile")
        assert json.dumps(first) == json.dumps(second)
        assert first["data"]["step"]["fixture"] == golden["nodes"]["fetch_store_profile"]

        run = await invoke(client, "run_get", run_id=RUN_ID)
        assert len(run["data"]["run"]["steps"]) == 1
        assert len(run["data"]["run"]["path"]) == 1


async def test_the_wire_walk_resolves_a_repeated_tool_name_both_ways(
    seeded: ServiceContext,
) -> None:
    """One session, several calls - the shape a client actually uses.

    Also the M8 script's step 7 through the transport: the same tool name
    answering two different nodes, and an unresolvable position answering
    RT-E01 with the candidates named.
    """
    async with connected(seeded) as client:
        await invoke(
            client, "run_start", run_id=RUN_ID, agent_id=AGENT, selector={"dataset_id": PRIYA}
        )
        await invoke(client, "fetch_step", run_id=RUN_ID, node_id="receive_request")
        early = await invoke(client, "fetch_step", run_id=RUN_ID, tool_name=REPEATED_TOOL)
        assert early["data"]["step"]["resolved_node_id"] == "fetch_store_profile"

        await invoke(client, "fetch_step", run_id=RUN_ID, node_id="check_docs")
        ambiguous = await invoke(client, "fetch_step", run_id=RUN_ID, tool_name=REPEATED_TOOL)
        assert rules(ambiguous) == ["RT-E01"]
        assert ambiguous["errors"][0]["context"]["candidates"] == [
            "fetch_store_profile",
            "recheck_store",
        ]

        await invoke(client, "fetch_step", run_id=RUN_ID, node_id=POOL_NODE, iteration=0)
        late = await invoke(client, "fetch_step", run_id=RUN_ID, tool_name=REPEATED_TOOL)
        assert late["data"]["step"]["resolved_node_id"] == "recheck_store"


async def test_the_pool_boundary_over_the_wire(seeded: ServiceContext) -> None:
    """Ruling R-52: the two-entry golden pool exhausts at iteration 2, not 3."""
    async with connected(seeded) as client:
        await invoke(
            client, "run_start", run_id=RUN_ID, agent_id=AGENT, selector={"dataset_id": PRIYA}
        )
        inside = await invoke(client, "fetch_step", run_id=RUN_ID, node_id=POOL_NODE, iteration=1)
        assert codes(inside) == []
        past = await invoke(client, "fetch_step", run_id=RUN_ID, node_id=POOL_NODE, iteration=2)
        assert past["ok"] is True, "an extra loop is never a hard failure (ruling R-03)"
        assert codes(past) == ["pool_exhausted"]
        assert past["data"]["step"]["fixture"] == inside["data"]["step"]["fixture"]


async def test_an_unknown_run_is_rt_e03_on_both_run_reads(seeded: ServiceContext) -> None:
    async with connected(seeded) as client:
        assert rules(await invoke(client, "fetch_step", run_id="nobodys-run")) == ["RT-E03"]
        assert rules(await invoke(client, "run_get", run_id="nobodys-run")) == ["RT-E03"]


async def test_run_find_returns_rows_and_is_deterministic(seeded: ServiceContext) -> None:
    """M4's determinism criterion, applied to the tool M6 adds."""
    async with connected(seeded) as client:
        await invoke(
            client, "run_start", run_id=RUN_ID, agent_id=AGENT, selector={"dataset_id": PRIYA}
        )
        await invoke(
            client,
            "run_start",
            run_id="m6-wire-run-two",
            agent_id=AGENT,
            selector={"dataset_id": PRIYA},
            run_class="load",
        )
        first = await invoke(client, "run_find", agent_id=AGENT)
        second = await invoke(client, "run_find", agent_id=AGENT)
        assert json.dumps(first) == json.dumps(second)
        assert [row["id"] for row in first["data"]["runs"]] == [RUN_ID, "m6-wire-run-two"]
        filtered = await invoke(client, "run_find", run_class="load")
        assert [row["id"] for row in filtered["data"]["runs"]] == ["m6-wire-run-two"]


# ------------------------------------------------------- record_step, run_finish


async def test_record_step_and_run_finish_over_the_wire(seeded: ServiceContext) -> None:
    """M8's two writes, end to end on one session: fetch, record, finish, read back.

    The payload shapes contracts section 4 documents, under M4's one-named-key
    convention: ``record`` carries the stored step plus the resolved node id -
    which is the field a ``tool_name`` caller has no other way to learn - and
    ``run_finish`` returns the run itself.
    """
    async with connected(seeded) as client:
        await invoke(
            client, "run_start", run_id=RUN_ID, agent_id=AGENT, selector={"dataset_id": PRIYA}
        )
        await invoke(client, "fetch_step", run_id=RUN_ID, node_id="receive_request")
        recorded = await invoke(
            client,
            "record_step",
            run_id=RUN_ID,
            actual={"store_id": "store_bengaluru_04", "franchisee": "priya"},
            tool_name="acme.onboarding.intake",
        )
        assert recorded["ok"] is True
        assert set(recorded["data"]) == {"record"}
        payload = recorded["data"]["record"]
        assert set(payload) == {"step", "resolved_node_id"}
        assert payload["resolved_node_id"] == "receive_request", "addressed by tool name"
        assert payload["step"]["actual"] == {
            "store_id": "store_bengaluru_04",
            "franchisee": "priya",
        }
        assert codes(recorded) == []

        finished = await invoke(
            client,
            "run_finish",
            run_id=RUN_ID,
            outcome={"onboarding_status": "complete", "outstanding_tasks": 0},
            status="finished",
        )
        assert finished["ok"] is True
        assert set(finished["data"]) == {"run"}
        run = finished["data"]["run"]
        assert run["status"] == "finished"
        assert run["outcome"] == {"onboarding_status": "complete", "outstanding_tasks": 0}
        assert run["finished_at"] is not None
        assert codes(finished) == []

        stored = await invoke(client, "run_get", run_id=RUN_ID)
        assert stored["data"]["run"]["steps"][0]["actual"] == payload["step"]["actual"]
        assert stored["data"]["run"]["status"] == "finished"


async def test_a_record_step_for_an_unserved_step_is_ap_004(seeded: ServiceContext) -> None:
    """Ruling R-33's "an actual cannot be reported for a step that was never served".

    ``escalate`` is a real node in the blueprint, so resolution succeeds and the
    finding is about the *step key* rather than about the address - which is why
    it is ``AP-004`` and not an ``RT-*`` code.
    """
    async with connected(seeded) as client:
        await invoke(
            client, "run_start", run_id=RUN_ID, agent_id=AGENT, selector={"dataset_id": PRIYA}
        )
        answered = await invoke(
            client,
            "record_step",
            run_id=RUN_ID,
            actual={"onboarding_status": "escalated"},
            node_id="escalate",
        )
        assert rules(answered) == ["AP-004"]
        assert answered["errors"][0]["pointer"] == "/node_id"
        assert answered["errors"][0]["context"]["resolved_node_id"] == "escalate"


async def test_an_unknown_run_is_rt_e03_on_both_run_writes(seeded: ServiceContext) -> None:
    """The same answer the two reads give, so a caller learns one code for one cause."""
    async with connected(seeded) as client:
        recorded = await invoke(
            client, "record_step", run_id="nobodys-run", actual={}, node_id="receive_request"
        )
        assert rules(recorded) == ["RT-E03"]
        finished = await invoke(
            client, "run_finish", run_id="nobodys-run", outcome={}, status="finished"
        )
        assert rules(finished) == ["RT-E03"]


async def test_an_unknown_finish_status_is_ap_001_naming_the_vocabulary(
    seeded: ServiceContext,
) -> None:
    """``running`` is refused with the rest: ``run_finish`` closes a run."""
    async with connected(seeded) as client:
        await invoke(
            client, "run_start", run_id=RUN_ID, agent_id=AGENT, selector={"dataset_id": PRIYA}
        )
        for status in ("running", "done", ""):
            answered = await invoke(client, "run_finish", run_id=RUN_ID, outcome={}, status=status)
            assert rules(answered) == ["AP-001"], status
            assert answered["errors"][0]["pointer"] == "/status"
            assert answered["errors"][0]["context"]["allowed"] == ["abandoned", "finished"]
        assert (await invoke(client, "run_get", run_id=RUN_ID))["data"]["run"][
            "status"
        ] == "running", "a refused status wrote nothing"


async def test_a_repeated_write_answers_by_whether_the_value_differs(
    seeded: ServiceContext,
) -> None:
    """Ruling R-65 over the wire, **both ends**, for both writes.

    The two halves have two different envelopes and this asserts each against
    the other, in one session, so neither can be read as the general case:

    - the **identical** repeat is a **silent** success - R-65 as amended: a
      retry after a timeout is the *expected* outcome, so there is nothing to
      report, and a vocabulary that fires on expected outcomes trains callers to
      ignore it. Asserted on the response *and* on the stored run, because a
      warning merged onto the run would outlive the request that caused it;
    - the **differing** one is ``ok: false`` with ``AP-007``, and the stored
      value is unchanged afterwards, which is the assertion that makes it a
      refused *write* rather than a rejected argument.

    Over a real MCP round trip because that is where ``ok`` becomes a JSON
    boolean a caller branches on, and because the client's typed methods raise
    on ``ok: false`` - so a client author reading only the service tests would
    not see which of these two throws.
    """
    async with connected(seeded) as client:
        await invoke(
            client, "run_start", run_id=RUN_ID, agent_id=AGENT, selector={"dataset_id": PRIYA}
        )
        await invoke(client, "fetch_step", run_id=RUN_ID, node_id=POOL_NODE, iteration=0)
        first = {"requested": ["fssai"], "received": []}

        landed = await invoke(
            client, "record_step", run_id=RUN_ID, actual=first, node_id=POOL_NODE, iteration=0
        )
        assert landed["ok"] is True and codes(landed) == []

        repeated = await invoke(
            client, "record_step", run_id=RUN_ID, actual=first, node_id=POOL_NODE, iteration=0
        )
        assert repeated["ok"] is True, "an identical re-record is a no-op success (ruling R-65)"
        assert codes(repeated) == [], "and it is silent"
        assert repeated["data"]["record"]["step"]["actual"] == first

        refused = await invoke(
            client,
            "record_step",
            run_id=RUN_ID,
            actual={"requested": ["pan"], "received": []},
            node_id=POOL_NODE,
            iteration=0,
        )
        assert refused["ok"] is False, "a differing re-record is a refused write (ruling R-65)"
        assert rules(refused) == ["AP-007"]
        assert refused["errors"][0]["pointer"] == "/actual"
        assert refused["errors"][0]["context"]["resolved_node_id"] == POOL_NODE

        outcome = {"onboarding_status": "complete", "outstanding_tasks": 0}
        closed = await invoke(
            client, "run_finish", run_id=RUN_ID, outcome=outcome, status="finished"
        )
        assert closed["ok"] is True and codes(closed) == []

        again = await invoke(
            client, "run_finish", run_id=RUN_ID, outcome=outcome, status="finished"
        )
        assert again["ok"] is True, "an identical re-finish is a no-op success"
        assert codes(again) == [], "run_already_finished belongs to the read path"
        assert again["data"]["run"] == closed["data"]["run"], "the repeat changed the run"

        diverging = await invoke(
            client,
            "run_finish",
            run_id=RUN_ID,
            outcome={"onboarding_status": "escalated"},
            status="abandoned",
        )
        assert diverging["ok"] is False, "a differing re-finish is a refused write"
        assert rules(diverging) == ["AP-007"]
        assert diverging["errors"][0]["pointer"] == "/status"
        assert sorted(diverging["errors"][0]["context"]["diverged"]) == ["outcome", "status"]

        stored = await invoke(client, "run_get", run_id=RUN_ID)
        assert stored["data"]["run"]["status"] == "finished", "the refused finish wrote anyway"
        assert stored["data"]["run"]["outcome"] == outcome
        assert stored["data"]["run"]["steps"][0]["actual"] == first
        assert stored["data"]["run"]["warnings"] == [], (
            "four writes on one run - two that landed, two no-ops and two refusals - and none "
            "of them is an event the run should carry"
        )
