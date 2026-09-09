"""``run_evidence`` and ``run_export`` through the MCP surface. Shapes, not prose.

`test_service_evidence.py` owns the behaviour; this file owns the two things only
the tool surface can be wrong about: the envelope a caller actually receives, and
that the tools are reachable by the names `docs/contracts.md` gives them.

Kept short on purpose. Each tool is four lines - parse, delegate, shape - so
there is little here that is not already asserted one layer in, and the M4
convention is that a tool test pins the envelope and leaves the rules to the
service suite.

``attempt`` is called with the tool name as a **literal at the call site**, not
through a local helper, and that is deliberate:
`test_tool_surface.py::test_every_registered_tool_has_a_dedicated_test` collects
those literals with an AST walk, so a wrapper would hide these two tools from
the guard that exists to notice an untested one.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp_types import CallToolResult

from agentprops.service import ServiceContext
from agentprops.service.envelope import AP_ARGUMENT
from evidencewalk import RUN_ID, walk
from toolclient import attempt

pytestmark = pytest.mark.asyncio


def payload(result: CallToolResult) -> dict[str, Any]:
    """The envelope, asserting the SDK reported no error.

    ``is_error`` is the SDK's channel for "the tool raised", so checking it on
    every call is how this suite enforces CLAUDE.md's "structured errors, never
    exceptions".
    """
    assert result.is_error is False, f"the tool raised: {result.content}"
    assert result.structured_content is not None
    envelope: dict[str, Any] = result.structured_content
    return envelope


async def test_run_evidence_answers_a_success_envelope_with_one_named_key(
    context: ServiceContext,
) -> None:
    """``{ok, data: {evidence}, warnings}``. R-43(b)'s convention, on this tool.

    The whole bundle rides under one key, so a caller never has to ask whether
    this tool wraps - and the keys inside it are the milestone's own sentence
    plus ruling R-82's ``outcome_schema``.
    """
    walk(context)
    reply = payload(await attempt(context, "run_evidence", {"run_id": RUN_ID}))
    assert reply["ok"] is True, reply
    assert list(reply["data"]) == ["evidence"]
    assert reply["warnings"] == []
    bundle = reply["data"]["evidence"]
    assert bundle["comparison"] == "subset"
    assert bundle["outcome_schema"]["type"] == "object"
    assert len(bundle["nodes"]) == 9


async def test_run_evidence_on_an_unknown_run_is_an_error_envelope(
    context: ServiceContext,
) -> None:
    """RT-E03 with a pointer at ``/run_id``. Asserted on the rule id, never the message."""
    reply = payload(await attempt(context, "run_evidence", {"run_id": "no-such-run"}))
    assert reply["ok"] is False
    assert [item["rule"] for item in reply["errors"]] == ["RT-E03"]
    assert reply["errors"][0]["pointer"] == "/run_id"


async def test_run_export_rejects_an_unknown_target_before_dialling_anything(
    context: ServiceContext,
) -> None:
    """``AP-001`` naming the vocabulary, and no network touched to find out.

    The closed-vocabulary shape ``run_class`` and ``run_finish``'s ``status``
    already use. If the check happened *after* the export, this test would spend
    a retry budget discovering it.
    """
    walk(context)
    reply = payload(
        await attempt(context, "run_export", {"run_id": RUN_ID, "target": "braintrust"})
    )
    assert reply["ok"] is False
    assert [item["rule"] for item in reply["errors"]] == [AP_ARGUMENT]
    assert reply["errors"][0]["context"]["allowed"] == ["langfuse", "otel"]
