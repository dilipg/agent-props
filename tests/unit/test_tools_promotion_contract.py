"""The three M7 tools through the in-memory MCP client: envelopes, not prose.

`test_service_expansion.py` and `test_service_promotion.py` test the behaviour;
this tests the **surface**. Which is a different thing, and M4 and M5 both found
defects that only exist at this layer: a permissive ``object`` annotation that
lets a wrong-typed argument through, an integer that reaches a column and raises
instead of answering an envelope, an argument reader that was never wired up.

So every test here goes through :func:`toolclient.invoke`, which asserts
``is_error is False`` on every call - the invariant CLAUDE.md states absolutely
and the one M4's and M5's blockers both broke.

Three things this file is careful about:

- **``dataset_ids`` is the first array parameter on the whole surface.** Its
  reader is new, so both the happy path and the wrong-element-type path are
  here, and the finding points at the *index* rather than at the argument.
- **``count`` is the fifth integer parameter**, and `test_bounded_integers.py`
  covers the range mechanically. What is here instead is the *semantics* the
  range check cannot see: which of ``AP-001`` and ``DS-023`` answers, and why.
- **A bundle round-trips through the tool surface**, not through the service
  functions - because that is the path a developer promoting a dataset actually
  takes, and it is the one where an argument annotation could quietly drop the
  bundle.
"""

from __future__ import annotations

from typing import Any, Final

import pytest

from agentprops.models import Dataset
from agentprops.service import ServiceContext, blueprints
from agentprops.service.expansion import MAX_EXPAND_COUNT
from conftest import load_document
from toolclient import connected, invoke

AGENT: Final = "location-onboarding"
POOL_NODE: Final = "request_docs"
PRIYA: Final = "datasets/priya-missing-docs.json"
ARUN: Final = "datasets/arun-escalated.json"


@pytest.fixture
def authored(context: ServiceContext) -> ServiceContext:
    """The published blueprint and both golden datasets, straight into the store."""
    assert blueprints.upsert(
        context, load_document("blueprints/location-onboarding-1.0.0.json"), publish=True
    ).ok
    for relative in (PRIYA, ARUN):
        context.store.put_dataset(Dataset.model_validate(load_document(relative)))
    return context


def identifier(relative: str) -> str:
    found: str = load_document(relative)["id"]
    return found


def errors_of(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """The findings on a wire envelope.

    `tests/envelopes.py` does this for a ``Reply`` *model*; a tool answers a
    ``dict``, and asserting through the model would test a conversion this layer
    does not perform.
    """
    findings: list[dict[str, Any]] = envelope["errors"]
    return findings


def rules_of(envelope: dict[str, Any]) -> list[str]:
    """Just the rule ids. What a test asserts on - never the message."""
    return [finding["rule"] for finding in errors_of(envelope)]


async def test_dataset_expand_answers_a_success_envelope(authored: ServiceContext) -> None:
    """The happy path, and the one named key ``data`` carries (ruling R-43)."""
    async with connected(authored) as client:
        envelope = await invoke(
            client, "dataset_expand", dataset_id=identifier(PRIYA), node_id=POOL_NODE, count=1
        )
    assert envelope["ok"] is True
    assert list(envelope["data"]) == ["dataset"]
    assert len(envelope["data"]["dataset"]["pools"][POOL_NODE]) == 3


async def test_dataset_expand_reports_ds_023_through_the_surface(
    authored: ServiceContext,
) -> None:
    """A rule id and a pointer, not a message. Ruling R-56's answer, at the boundary."""
    async with connected(authored) as client:
        envelope = await invoke(
            client, "dataset_expand", dataset_id=identifier(PRIYA), node_id=POOL_NODE, count=9
        )
    assert rules_of(envelope) == ["DS-023"]
    assert errors_of(envelope)[0]["pointer"] == f"/pools/{POOL_NODE}"


async def test_dataset_expand_reports_the_per_call_maximum_as_ap_001(
    authored: ServiceContext,
) -> None:
    """The two refusals answer differently, and the difference is the point.

    ``DS-023`` means "this pool would break its own blueprint" and carries the
    blueprint's number. ``AP-001`` at ``/count`` means "no single call builds
    that many" and carries this service's number. A caller can act on either;
    collapsing them would leave it guessing which limit it hit.
    """
    async with connected(authored) as client:
        envelope = await invoke(
            client,
            "dataset_expand",
            dataset_id=identifier(PRIYA),
            node_id=POOL_NODE,
            count=MAX_EXPAND_COUNT + 1,
        )
    assert rules_of(envelope) == ["AP-001"]
    assert errors_of(envelope)[0]["pointer"] == "/count"


@pytest.mark.parametrize("count", ["1", True, None, 1.5], ids=["text", "bool", "null", "float"])
async def test_a_non_integer_count_is_an_envelope(authored: ServiceContext, count: object) -> None:
    """``true`` is not an integer here, which the module docstring of `args.py` argues.

    ``isinstance(True, int)`` is true in Python, and the same trap already cost
    a rule: ``validation.context.as_int`` excludes ``bool`` explicitly so
    DS-020 does not accept ``"seed": true``. A ``count`` of ``true`` silently
    becoming ``1`` is the same class of bug one layer out.
    """
    async with connected(authored) as client:
        envelope = await invoke(
            client, "dataset_expand", dataset_id=identifier(PRIYA), node_id=POOL_NODE, count=count
        )
    assert envelope["ok"] is False
    assert rules_of(envelope) == ["AP-001"]
    assert errors_of(envelope)[0]["pointer"] == "/count"


async def test_dataset_export_returns_a_bundle(authored: ServiceContext) -> None:
    async with connected(authored) as client:
        envelope = await invoke(client, "dataset_export", agent_id=AGENT)
    assert envelope["ok"] is True
    assert list(envelope["data"]) == ["bundle"]
    assert len(envelope["data"]["bundle"]["datasets"]) == 2


async def test_dataset_export_takes_an_array_of_ids(authored: ServiceContext) -> None:
    """The first array parameter on the surface, exercised as one."""
    async with connected(authored) as client:
        envelope = await invoke(
            client, "dataset_export", agent_id=AGENT, dataset_ids=[identifier(ARUN)]
        )
    assert envelope["ok"] is True
    assert [item["id"] for item in envelope["data"]["bundle"]["datasets"]] == [identifier(ARUN)]


async def test_a_non_string_id_in_the_array_points_at_its_index(
    authored: ServiceContext,
) -> None:
    """``/dataset_ids/1``, not ``/dataset_ids``.

    The same choice :meth:`ArgReader.labels` makes for a bad label value: tell
    the caller which element to change rather than handing the whole argument
    back. A five-element array with one bad entry is where this matters.
    """
    async with connected(authored) as client:
        envelope = await invoke(
            client, "dataset_export", agent_id=AGENT, dataset_ids=[identifier(PRIYA), 7]
        )
    assert envelope["ok"] is False
    assert rules_of(envelope) == ["AP-001"]
    assert errors_of(envelope)[0]["pointer"] == "/dataset_ids/1"


async def test_a_dataset_ids_that_is_not_an_array_is_an_envelope(
    authored: ServiceContext,
) -> None:
    async with connected(authored) as client:
        envelope = await invoke(client, "dataset_export", agent_id=AGENT, dataset_ids="not-a-list")
    assert envelope["ok"] is False
    assert rules_of(envelope) == ["AP-001"]
    assert errors_of(envelope)[0]["pointer"] == "/dataset_ids"


async def test_export_then_import_through_the_tool_surface(authored: ServiceContext) -> None:
    """The journey a developer promoting a dataset actually takes.

    Two tool calls, and the bundle from the first is the argument to the second
    with nothing in between. That is the one thing neither service test can
    check: that the bundle survives being serialised through the MCP boundary
    and parsed back as a tool argument.
    """
    async with connected(authored) as client:
        exported = await invoke(client, "dataset_export", agent_id=AGENT)
        imported = await invoke(client, "dataset_import", bundle=exported["data"]["bundle"])
    assert imported["ok"] is True
    assert list(imported["data"]) == ["imported"]
    assert [row["version"] for row in imported["data"]["imported"]["datasets"]] == [2, 2]


async def test_dataset_import_reports_a_bundle_that_is_not_one(
    authored: ServiceContext,
) -> None:
    async with connected(authored) as client:
        envelope = await invoke(client, "dataset_import", bundle={"format": "nope"})
    assert envelope["ok"] is False
    assert set(rules_of(envelope)) == {"AP-001"}
    assert {finding["pointer"] for finding in errors_of(envelope)} == {
        "/bundle/format",
        "/bundle/format_version",
        "/bundle/blueprints",
        "/bundle/datasets",
    }


async def test_dataset_import_reports_a_bundle_that_is_not_an_object(
    authored: ServiceContext,
) -> None:
    """The permissive ``object`` annotation, doing its job.

    With ``bundle: dict``, the SDK would reject this before the tool ran and the
    caller would get an MCP protocol error carrying a Pydantic message - which
    is not the section 1 envelope, and is exactly what `args.py` annotates
    everything ``object`` to avoid.
    """
    async with connected(authored) as client:
        envelope = await invoke(client, "dataset_import", bundle="a bundle, honestly")
    assert envelope["ok"] is False
    assert rules_of(envelope) == ["AP-001"]


async def test_the_three_tools_publish_the_types_they_take(authored: ServiceContext) -> None:
    """The ``json_schema_extra`` half of the permissive-annotation trade.

    An LLM caller reads the published schema, so the advertised contract has to
    be *stricter* than the implementation - which is the safe direction. This
    checks the three new tools specifically, because the mechanical guard in
    `test_tool_surface.py` only asserts that a type is present.
    """
    from agentprops.server import mcp

    schemas: dict[str, Any] = {tool.name: tool.input_schema for tool in await mcp.list_tools()}
    assert schemas["dataset_expand"]["properties"]["count"]["type"] == "integer"
    assert schemas["dataset_export"]["properties"]["dataset_ids"]["type"] == ["array", "null"]
    assert schemas["dataset_export"]["properties"]["dataset_ids"]["items"] == {"type": "string"}
    assert schemas["dataset_import"]["properties"]["bundle"]["type"] == "object"
    assert schemas["dataset_import"]["required"] == ["bundle"]
