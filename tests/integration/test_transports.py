"""Evidence that both transports actually start and serve. Not the tool contracts.

M4's deliverable is "``MCPServer`` with stdio **and** streamable HTTP
transports". The in-memory ``Client`` in `tests/unit/test_tools_contract.py`
proves the *tools*; it proves nothing about either transport, because it never
touches one. These are separate claims and they get separate evidence.

So each test here starts a real server over a real transport, completes the MCP
initialise handshake, calls one tool, and shuts down:

- **stdio** through an actual subprocess - ``python -m agentprops.server`` -
  driven by handing ``StdioServerParameters`` straight to ``mcp.Client``, which
  its constructor accepts alongside an ``MCPServer`` instance. (Wiring
  ``stdio_client``'s stream pair into ``Client`` instead does **not** work:
  ``Client`` expects a transport, and a ``(read, write)`` tuple raises
  ``TypeError: 'builtins.tuple' object does not support the asynchronous
  context manager protocol``.) This is the only place in the suite that spawns a
  process, which is why it is marked ``integration`` rather than living in
  `tests/unit/` (CLAUDE.md: "no subprocesses in unit tests").
- **streamable HTTP** by running ``run_streamable_http_async`` in a background
  task on an ephemeral port and connecting a ``Client`` to the URL.

Both write to a real SQLite file under ``tmp_path``, so the transport is
exercised against the same store the rest of the suite uses.

The port is claimed by binding a socket and releasing it, which is a narrow race
rather than a fixed number that collides with whatever else is running. The
alternative - asking the server which port it bound - is not available:
``run_streamable_http_async`` takes a port and returns nothing until it stops.
"""

from __future__ import annotations

import asyncio
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing, suppress
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from agentprops.server import DEFAULT_HTTP_PATH, binding, serve_http
from agentprops.service import sqlite_context

pytestmark = pytest.mark.integration

#: How long to wait for a transport to come up before failing. Generous, because
#: a slow CI runner starting a subprocess is not a defect.
STARTUP_TIMEOUT_SECONDS = 30.0


def free_port() -> int:
    """A port nothing is listening on, released immediately."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def test_the_stdio_transport_serves_a_tool_call(tmp_path: Path) -> None:
    """``python -m agentprops.server --transport stdio`` over a real pipe."""
    database = tmp_path / "stdio.db"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentprops.server", "--transport", "stdio", "--store", str(database)],
    )
    async with asyncio.timeout(STARTUP_TIMEOUT_SECONDS):
        async with Client(parameters) as client:
            assert client.server_info is not None
            assert client.server_info.name == "agent-props"

            listed = await client.list_tools()
            assert {tool.name for tool in listed.tools} >= {"store_status", "blueprint_list"}

            result = await client.call_tool("store_status", {})
            assert result.is_error is False
            assert result.structured_content is not None
            assert result.structured_content["data"]["status"]["backend"] == "sqlite"

    assert database.exists(), "the subprocess should have created its SQLite file"


@asynccontextmanager
async def http_server(database: Path, port: int) -> AsyncIterator[str]:
    """Serve over streamable HTTP in a background task; yield the URL.

    The task is cancelled on exit and the ``CancelledError`` is expected, not
    swallowed sloppily: ``run_streamable_http_async`` runs until it is stopped,
    so cancellation *is* the shutdown path.
    """
    with binding(sqlite_context(database)):
        task = asyncio.create_task(serve_http(host="127.0.0.1", port=port))
        try:
            yield f"http://127.0.0.1:{port}{DEFAULT_HTTP_PATH}"
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def connect_when_ready(url: str) -> None:
    """Poll until the HTTP transport accepts a session, then use it once.

    A retry loop with **one** exit: it returns on the first successful session
    and otherwise runs out of attempts and lets the last exception propagate.
    The alternative shape - breaking out on success *and* on a deadline *and* on
    a specific exception type - is the smell `DECISIONS.md` records from the M3
    fix rounds.
    """
    deadline = asyncio.get_running_loop().time() + STARTUP_TIMEOUT_SECONDS
    last: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            async with Client(url) as client:
                assert client.server_info is not None
                assert client.server_info.name == "agent-props"
                result = await client.call_tool("store_status", {})
                assert result.is_error is False
                assert result.structured_content is not None
                assert result.structured_content["data"]["status"]["backend"] == "sqlite"
                return
        except Exception as exc:  # the transport is not up yet
            last = exc
            await asyncio.sleep(0.1)
    raise AssertionError(f"the streamable HTTP transport never accepted a session: {last!r}")


async def test_the_streamable_http_transport_serves_a_tool_call(tmp_path: Path) -> None:
    """``run_streamable_http_async`` on an ephemeral port, driven by a real client."""
    database = tmp_path / "http.db"
    async with http_server(database, free_port()) as url:
        await connect_when_ready(url)
    assert database.exists()


async def test_the_streamable_http_transport_serves_the_whole_tool_surface(
    tmp_path: Path,
) -> None:
    """One tool per module over HTTP, so the transport is not proved by one read.

    ``store_status`` alone would leave open whether a tool taking arguments, or
    one returning a failure envelope, survives the transport's serialisation.
    """
    database = tmp_path / "http-surface.db"
    async with http_server(database, free_port()) as url:
        await connect_when_ready(url)
        async with Client(url) as client:
            listed = await client.list_tools()
            assert len(listed.tools) == 13

            missing = await client.call_tool("blueprint_get", {"agent_id": "nobody"})
            assert missing.structured_content is not None
            assert missing.structured_content["ok"] is False
            assert missing.structured_content["errors"][0]["rule"] == "AP-004"

            diffed = await client.call_tool(
                "blueprint_diff",
                {"agent_id": "nobody", "from_version": "1.0.0", "to_version": "1.1.0"},
            )
            assert diffed.structured_content is not None
            assert diffed.structured_content["ok"] is True, "blueprint_diff never fails"
