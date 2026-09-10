"""The ``MCPServer`` instance, its one injection point, and the two transports.

Ruling R-16's import path, verified against the installed `mcp` 2.2.0
--------------------------------------------------------------------

``from mcp.server import MCPServer``. `docs/build-handoff.md` section 2 says
``from mcp import MCPServer``, which raises ``ImportError``: ``MCPServer`` is not
a top-level export of `mcp` 2.2.0. Everything else the handoff claims was
re-verified while building this module and is correct - the ``@mcp.tool()``
decorator, ``run_stdio_async()``, ``run_streamable_http_async()``, and
``mcp.Client`` accepting an ``MCPServer`` instance directly, which is what lets
the whole tool-contract suite run in-process with no subprocess.

Why the instance lives here and not in ``__init__.py``
------------------------------------------------------

`docs/build-handoff.md` puts "the ``MCPServer`` instance and both transports" in
`server/__init__.py`, and that is where they are *exported* from. They are
*defined* here because a tool module has to import the instance to decorate
against it, and a tool module importing its own package's ``__init__`` while
that ``__init__`` is importing the tool module is a partially-initialised-module
cycle. The alternative - registering the tool modules at the bottom of
``__init__.py`` behind ``# noqa: E402`` - works, and swaps a clean two-module
split for a lint suppression and an import whose position is load-bearing.

The one mutable global, and why it is one
-----------------------------------------

Tools are registered at *import* time by a decorator on a module-level
function, so a tool cannot close over a store that does not exist yet. The
store and clock therefore arrive through :func:`bind` and are read by
:func:`bound` at call time.

Two alternatives were weighed. Building a fresh ``MCPServer`` per store, with
the tools as closures inside a ``register(mcp, context)`` function, removes the
global - at the cost of nesting thirteen tool functions inside three
registration functions, where a reader looking for ``blueprint_upsert`` finds it
indented inside something else. A ``ContextVar`` looks safer than a global and
is not: the SDK runs a sync tool through ``anyio.to_thread.run_sync`` and the
in-memory ``Client`` runs the server in a task it creates itself, so whether a
value set by a test fixture is visible inside a tool depends on when the fixture
ran relative to the client's task group. A plain module attribute has no such
question - it is read at call time from whatever thread or task is asking.

:func:`binding` is the context manager tests use, so a test cannot leak its
store into the next one.

Not a CLI
---------

`server/__main__.py` exists so the server can be *started* - "MCPServer with
stdio and streamable HTTP transports" is M4's deliverable and a transport with
no entry point cannot be shown to work. It takes a transport and a store URL and
nothing else. Phase 1's "no CLI" non-goal is about an authoring CLI, which this
is not.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

from mcp.server import MCPServer

from agentprops.service import ServiceContext

__all__ = [
    "DEFAULT_HTTP_HOST",
    "DEFAULT_HTTP_PATH",
    "DEFAULT_HTTP_PORT",
    "SERVER_NAME",
    "SERVER_VERSION",
    "bind",
    "binding",
    "bound",
    "mcp",
    "serve_http",
    "serve_stdio",
]

SERVER_NAME: Final = "agent-props"
SERVER_VERSION: Final = "0.1.0"

DEFAULT_HTTP_HOST: Final = "127.0.0.1"
DEFAULT_HTTP_PORT: Final = 8000
DEFAULT_HTTP_PATH: Final = "/mcp"

INSTRUCTIONS: Final = """\
Blueprint-driven narrative fixture service. A blueprint is a graph of an
agent's steps; a dataset is one hand-authored, immutable, versioned world for
that blueprint.

Every tool returns one of two envelopes and never raises for anything you can
cause: {"ok": true, "data": {...}, "warnings": [...]} on success, or
{"ok": false, "errors": [{"rule", "severity", "pointer", "section", "message",
"context"}]} on a validation or resolution failure. `data` always holds the
payload under one named key. Read `rule` and `pointer`, not `message`.

Warnings never block anything. A policy problem, an archived dataset, a missing
blueprint version in a diff - all arrive as warnings on a successful response.

New here? Read the resource `agentprops://orientation` before calling anything
else. Twenty-seven tools carry no ordering between them, and that resource is
where the ordering lives: the five phases, which tools belong to each, and which
phase this store has reached. In short - author a blueprint, author datasets
against it, then run_start / fetch_step / record_step / run_finish while the
agent works, then run_evidence to read the result back.

The four prompts hold the authoring detail and read this store as they render,
so call prompts/list even where your interface shows no prompts of its own.
"""

mcp: Final = MCPServer(name=SERVER_NAME, version=SERVER_VERSION, instructions=INSTRUCTIONS)


class _Binding:
    """Holds the bound context. A class so no function needs ``global``."""

    context: ServiceContext | None = None


_BINDING: Final = _Binding()


def bind(context: ServiceContext) -> ServiceContext:
    """Give the tool surface its store and clock. Returns what was bound."""
    _BINDING.context = context
    return context


def bound() -> ServiceContext:
    """The bound context.

    Raises ``RuntimeError`` if nothing is bound. That is a *programming* error -
    a process that started serving without opening a store - and it is
    deliberately not translated into an envelope: an envelope would tell a
    caller their request was wrong when the server is misconfigured, and would
    make every tool answer plausibly while doing nothing.
    """
    context = _BINDING.context
    if context is None:
        raise RuntimeError(
            "no ServiceContext is bound; call agentprops.server.bind(context) before serving"
        )
    return context


@contextmanager
def binding(context: ServiceContext) -> Iterator[ServiceContext]:
    """Bind ``context`` for the duration of the block, then restore.

    What the ``mcp_client`` fixture uses. Restores rather than clearing, so a
    nested bind inside a test does not leave the outer one broken.
    """
    previous = _BINDING.context
    _BINDING.context = context
    try:
        yield context
    finally:
        _BINDING.context = previous


async def serve_stdio() -> None:
    """Serve over stdio. The transport an editor or `claude mcp` launches."""
    await mcp.run_stdio_async()


async def serve_http(
    *,
    host: str = DEFAULT_HTTP_HOST,
    port: int = DEFAULT_HTTP_PORT,
    path: str = DEFAULT_HTTP_PATH,
) -> None:
    """Serve over streamable HTTP. The transport the M9 web app will use."""
    await mcp.run_streamable_http_async(host=host, port=port, streamable_http_path=path)
