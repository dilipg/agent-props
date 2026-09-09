"""MCP surface. Thin. No business logic.

The ``MCPServer`` instance, the two transports, and twenty tools across four
modules. Importing this package is what *registers* the tools: each
`tools_*.py` module decorates its functions with ``@mcp.tool()`` at import time,
so the four imports below are the registration, and removing one silently
removes its tools from the surface.

``tests/unit/test_tool_surface.py`` is what makes that safe. It enumerates the
tools the running server actually reports and asserts that every one of them is
exercised by a test, in the same way `test_validation_drift.py` asserts that
every catalogue rule has an implementation and a fixture. A tool added without a
test fails; a tool whose test is deleted fails.

How to add a tool - for M7 and after
-------------------------------------

1. Write the service function in `service/`. All of the behaviour goes here:
   the validation, the resolution, the store calls, the warnings. It takes a
   :class:`~agentprops.service.context.ServiceContext` as its first argument and
   returns a :data:`~agentprops.service.envelope.Reply`.
2. Add a module-level function in the matching `server/tools_*.py`, decorated
   ``@mcp.tool()``, whose docstring is the tool description an LLM caller reads.
   Annotate each parameter with one of the aliases in `server/args.py` -
   ``TextArg``, ``ObjectArg``, ``IntArg`` and the rest - never with a bare
   ``str`` or ``int``; the module docstring there explains what that annotation
   is protecting.
3. The body is: build an ``ArgReader``, read each argument through it, return
   ``reply(failure(args.errors))`` if any reader complained, then
   ``reply(<service function>(bound(), ...))``. Under 20 lines, no exceptions.
4. Give it at least one test. The surface test above will tell you if you
   forget.

A new *module* of tools also needs an import in this file, and its name in
``TOOL_MODULES`` so the layering guard covers it.

What a new tool inherits, and therefore need not build
------------------------------------------------------

- both transports, and the in-memory ``Client`` test harness;
- the store and clock, through :func:`bound`;
- the ``Resolver`` the catalogue's existence checks need (ruling R-11);
- both envelopes, the five ``AP-*`` boundary codes, and the
  ``RuleError``-to-``Warning`` conversion a write path needs for ruling R-13;
- argument coercion, so a malformed argument is an envelope rather than an MCP
  protocol error;
- ruling R-20's raw-text document seam, for anything that accepts a dataset.
"""

from typing import Final

from agentprops.server import tools_admin, tools_blueprint, tools_dataset, tools_run
from agentprops.server.app import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PATH,
    DEFAULT_HTTP_PORT,
    SERVER_NAME,
    SERVER_VERSION,
    bind,
    binding,
    bound,
    mcp,
    serve_http,
    serve_stdio,
)

#: Every module that registers tools. Read by `tests/unit/test_layering.py` and
#: by `tests/unit/test_tool_surface.py`, so a new tool module is covered by
#: adding one name here rather than by remembering to update two test files.
TOOL_MODULES: Final = (tools_blueprint, tools_dataset, tools_admin, tools_run)

__all__ = [
    "DEFAULT_HTTP_HOST",
    "DEFAULT_HTTP_PATH",
    "DEFAULT_HTTP_PORT",
    "SERVER_NAME",
    "SERVER_VERSION",
    "TOOL_MODULES",
    "bind",
    "binding",
    "bound",
    "mcp",
    "serve_http",
    "serve_stdio",
]
