"""The four run tools over a real in-memory MCP session.

The same division of labour `test_tools_contract.py` records: **envelopes,
warnings and determinism** here, behaviour in `test_service_runs.py` and
`test_service_resolution.py`. Duplicating the service suite through the client
would test the SDK's serialisation another fifty times.

What genuinely needs a client, and is therefore here rather than there:

- the four tools answer the section 1 envelope over a round trip at all, which
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
REPEATED_TOOL = "delightree.stores.get"


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
