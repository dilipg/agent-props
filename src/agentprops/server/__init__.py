"""MCP surface. Thin. No business logic.

The ``MCPServer`` instance, the two transports, twenty-five tools across four
modules, four prompts and five resources. Importing this package is what
*registers* all three surfaces: each `tools_*.py` module decorates its functions
with ``@mcp.tool()`` at import time, `prompts.py` with ``@mcp.prompt()`` and
`resources.py` with ``@mcp.resource()``, so the imports below *are* the
registration and removing one silently removes its surface.

``tests/unit/test_tool_surface.py`` is what makes that safe for tools, and
``tests/unit/test_prompt_and_resource_surface.py`` does the same job for the
other two. Each enumerates what the running server actually reports and asserts
that every entry is exercised by a test, in the same way
`test_validation_drift.py` asserts that every catalogue rule has an
implementation and a fixture. A tool, prompt or resource added without a test
fails; one whose test is deleted fails.

The prompts and resources are M9.5, ruling R-76: the server advertised both
capabilities in its ``initialize`` reply from M4 and registered neither, so
every workflow instruction lived in `README.md` - a second place to drift from
the tools it describes, and one that already had. **Registering a prompt does
not put an LLM in the service** (ground rule 4): a prompt is templated text
served over a protocol method, the same category as a tool description.
`server/prompts.py` says that at length, because the word invites the reading.

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
``TOOL_MODULES`` so the layering guard covers it. A prompt or resource module is
the same, against ``PROMPT_MODULES`` / ``RESOURCE_MODULES``.

How to add a prompt or a resource
---------------------------------

1. Compose it in `service/`. Anything whose content depends on store contents
   *must* go there (ruling R-76's layering clause), and in practice all of it
   does: `service/prompts.py` for text, `service/examples.py` for a resource
   body and for the URI strings both of them name.
2. Decorate a module-level function in `server/prompts.py` or
   `server/resources.py`. The docstring is the description a caller reads, the
   same as for a tool. Return the ``str`` or ``dict`` the service composed and
   nothing else - a prompt or resource function is one delegation, and
   `tests/unit/test_layering.py` holds it to the same thinness budget a tool
   function has.
3. Do **not** wrap either in a ``SuccessEnvelope`` (ruling R-43(b)):
   ``prompts/get`` and ``resources/read`` have their own protocol shapes.
4. Give it at least one test. The surface guard will tell you if you forget,
   and it names the one you forgot.

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

from agentprops.server import (
    prompts,
    resources,
    tools_admin,
    tools_blueprint,
    tools_dataset,
    tools_run,
)
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

#: Every module that registers prompts, and every module that registers
#: resources. Separate from :data:`TOOL_MODULES` because the layering guards
#: that measure a *tool* function match on the ``@mcp.tool()`` decorator, and
#: because a prompt module registers nothing callable through ``tools/call``.
PROMPT_MODULES: Final = (prompts,)
RESOURCE_MODULES: Final = (resources,)

__all__ = [
    "DEFAULT_HTTP_HOST",
    "DEFAULT_HTTP_PATH",
    "DEFAULT_HTTP_PORT",
    "PROMPT_MODULES",
    "RESOURCE_MODULES",
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
