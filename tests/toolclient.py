"""Calling a tool over a real in-memory MCP session, and why it is not a fixture.

Ruling R-16: ``mcp.Client`` accepts an ``MCPServer`` instance directly, so every
tool test in this suite is a genuine MCP round trip with no subprocess.

Why a helper rather than a ``client`` fixture
---------------------------------------------

The obvious shape - an async generator fixture that yields an entered
``Client`` - **does not work**, and the failure is in the plumbing rather than
in the idea. ``Client.__aenter__`` opens an ``anyio`` task group, and an
``anyio`` cancel scope has to be exited by the same task that entered it.
pytest-asyncio 1.4 runs an async fixture's setup and its finalizer through two
separate ``runner.run(...)`` calls, which are two different tasks, so the
teardown raises::

    RuntimeError: Attempted to exit cancel scope in a different task
    than it was entered in

Verified against the installed versions, on every test that took such a
fixture. So the session is opened and closed inside one coroutine instead -
:func:`connected` - and :func:`invoke` wraps the common case where a test makes
one call.

A session per call is not a compromise
--------------------------------------

Every M4 tool is a read or a single write against the store, and all state lives
in the store rather than in the session, so a session per call is
indistinguishable from a shared session *and* it exercises the initialise
handshake on every assertion.
:func:`test_tools_contract.test_one_session_serves_many_calls` covers session
reuse explicitly, so the shared-session path is not left untested.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from mcp import Client
from mcp_types import CallToolResult

from agentprops.server import binding, mcp
from agentprops.service import ServiceContext


@asynccontextmanager
async def connected(context: ServiceContext) -> AsyncIterator[Client]:
    """A client speaking to the server object, with ``context`` bound for the block.

    :func:`agentprops.server.binding` scopes the store to this block, so one
    test's store cannot leak into the next.
    """
    with binding(context):
        async with Client(mcp) as client:
            yield client


async def attempt(
    target: ServiceContext | Client, name: str, arguments: Mapping[str, Any] | None = None
) -> CallToolResult:
    """The raw ``CallToolResult``, for the few assertions that need ``is_error``."""
    if isinstance(target, Client):
        return await target.call_tool(name, dict(arguments or {}))
    async with connected(target) as client:
        return await client.call_tool(name, dict(arguments or {}))


async def invoke(target: ServiceContext | Client, name: str, **arguments: Any) -> dict[str, Any]:
    """Call a tool and return its envelope, asserting the SDK reported no error.

    ``target`` is either a :class:`~agentprops.service.ServiceContext` - a
    session is opened for the call - or an already-connected ``Client``, for a
    test that wants several calls on one session.

    ``is_error`` is the SDK's channel for "the tool raised", so asserting it is
    ``False`` on every call is how the suite enforces CLAUDE.md's "structured
    errors, never exceptions": an escaping exception fails the test that caused
    it rather than a summary test at the end.
    """
    result = await attempt(target, name, arguments)
    assert result.is_error is False, f"{name} raised: {result.content}"
    payload: dict[str, Any] | None = result.structured_content
    assert payload is not None, f"{name} returned no structured content"
    return payload
