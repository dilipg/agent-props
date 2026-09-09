"""The transport. **The only module here that opens anything.**

`compare.py` grades, `run.py` shapes requests, `envelope.py` reads responses,
and none of the three imports a network library. This module is where `mcp`
enters, and the layering is asserted rather than described:
`tests/unit/test_client_import_isolation.py` imports `compare` and `run` in a
fresh interpreter and asserts neither pulls in ``socket``, then imports **this**
module and asserts it does - so the guard is shown to be load-bearing rather
than vacuous.

Why that split, and not "the client needs a network anyway"
-----------------------------------------------------------

Ground rule 2: "the service never grades ... the three comparison helpers live
in the Python client as pure functions." Someone grading a stored run - in CI,
in a notebook, from a JSON file a colleague sent - has two documents and no
server, and must not need one. That is only true if the import graph says so.

Two ways in
-----------

:func:`connect_async` for an ``async`` caller and :func:`connect` for a
synchronous one. Both take whatever ``mcp.Client`` takes: a URL string, a
``StdioServerParameters``, or an ``MCPServer`` instance for an in-process
session with no subprocess at all (ruling R-16 verified that last one against
`mcp` 2.2.0, and it is what every tool test in this repo uses).

The sync bridge, and the cancel-scope trap it avoids
----------------------------------------------------

The MCP SDK is async-only, so a synchronous client has to drive an event loop.
The obvious shapes are both wrong in ways this repo has already paid for once:

- ``asyncio.run`` per call re-opens the session per request. For a stdio server
  that spawns the process again per step.
- a hand-rolled loop thread, entering the session in one submitted coroutine
  and exiting it in another, hits exactly the failure `tests/toolclient.py`
  records: ``Client.__aenter__`` opens an ``anyio`` task group, and an
  ``anyio`` cancel scope must be exited **by the task that entered it**.
  Entering and exiting from two different submitted coroutines raises
  ``RuntimeError: Attempted to exit cancel scope in a different task``.

So the bridge is ``anyio.from_thread.start_blocking_portal``, which exists for
this: it runs a loop in a worker thread, and
``portal.wrap_async_context_manager`` enters and exits the session inside **one**
portal task, which is the property the trap above is about. ``anyio`` is
already the SDK's own event-loop layer, so this adds no dependency the transport
did not have.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from anyio.from_thread import start_blocking_portal
from mcp import Client

from agentprops_client.run import AsyncRunClient, RunClient

__all__ = ["connect", "connect_async"]


@asynccontextmanager
async def connect_async(
    target: Any, *, agent_id: str, run_id: str | None = None
) -> AsyncIterator[AsyncRunClient]:
    """An :class:`AsyncRunClient` over an MCP session, for the block.

    ``target`` is anything ``mcp.Client`` accepts - a ``"http://host/mcp"`` URL,
    a ``StdioServerParameters``, or an ``MCPServer`` instance for an in-process
    session.

    The run id is generated when the client is constructed, which is inside this
    block: so it exists before the first call and is the same for every call in
    it, which is the property PRD 5.4 asks for.
    """
    async with Client(target) as client:

        async def call(tool: str, arguments: dict[str, Any]) -> Mapping[str, Any] | None:
            result = await client.call_tool(tool, arguments)
            _reject_protocol_error(tool, result)
            payload: Mapping[str, Any] | None = result.structured_content
            return dict(payload) if payload is not None else None

        yield AsyncRunClient(call, agent_id=agent_id, run_id=run_id)


@contextmanager
def connect(target: Any, *, agent_id: str, run_id: str | None = None) -> Iterator[RunClient]:
    """A :class:`RunClient` over an MCP session held open on a worker thread.

    One session for the whole block, not one per call - see the module docstring
    for why the portal is what makes that safe.

    The ``with`` below closes them in the order that matters: the **session
    first**, then the portal. The session's ``__aexit__`` has to run *on* the
    portal's loop and in the task that entered it, so a portal already stopped
    would have nowhere to run it - which is exactly the failure this whole
    arrangement exists to avoid, and why the two are one ``with`` rather than
    two.
    """
    with (
        start_blocking_portal() as portal,
        portal.wrap_async_context_manager(Client(target)) as client,
    ):

        def call(tool: str, arguments: dict[str, Any]) -> Mapping[str, Any] | None:
            result = portal.call(client.call_tool, tool, arguments)
            _reject_protocol_error(tool, result)
            payload: Mapping[str, Any] | None = result.structured_content
            return dict(payload) if payload is not None else None

        yield RunClient(call, agent_id=agent_id, run_id=run_id)


def _reject_protocol_error(tool: str, result: Any) -> None:
    """``is_error`` means the tool *raised*, which the service promises never to do.

    CLAUDE.md: "structured errors, never exceptions, for anything a user could
    cause." So ``is_error: true`` is not a rejected request - it is a bug in the
    server or a transport fault, and it arrives with ``structured_content:
    None`` rather than an envelope. Surfacing it as its own exception keeps the
    two apart: :class:`~agentprops_client.envelope.ToolError` means the
    service answered "no", and this means nobody answered.

    `tests/toolclient.py` asserts the same thing on every call in the server's
    own suite, for the same reason.
    """
    if getattr(result, "is_error", False):
        raise RuntimeError(
            f"{tool} raised instead of answering an envelope: {getattr(result, 'content', None)}"
        )
