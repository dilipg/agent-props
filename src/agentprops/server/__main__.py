"""``python -m agentprops.server`` - open a store, bind it, serve one transport.

Not the CLI phase 1 rules out. That non-goal is about an authoring CLI; this is
the process entry point for a server whose deliverable is "``MCPServer`` with
stdio and streamable HTTP transports", and a transport with no way to start it
cannot be demonstrated to work.

Four arguments and no configuration file, on purpose::

    python -m agentprops.server --store ./agentprops.db
    python -m agentprops.server --store mongodb://localhost:27017/agentprops
    python -m agentprops.server --store postgresql://user:pw@localhost/agentprops
    python -m agentprops.server --transport http --port 8931

``--store`` takes **any of the three backends**, which is M7's change: a Mongo
URL, a Postgres URL, a SQLite URL or a bare SQLite file path, dispatched by
:func:`agentprops.service.context_for`. Read that function's module docstring
for the one thing that differs between them - **who creates the schema**. The
short version: a SQLite file is created if absent, Mongo's indexes are declared
on startup, and a Postgres database is neither, because it is migrated by
``alembic upgrade head`` and by nothing else. M4 recorded that warning here
before the dispatch existed ("a URL argument that silently ran ``create_schema``
against a migrated Postgres database would be worse than not having one") and
the dispatch honours it.

Every argument also reads an environment variable, because the container has no
command line to speak of: `docker-compose.yml` sets ``AGENTPROPS_STORE`` and
the two ``AGENTPROPS_HTTP_*`` variables and the image's ``CMD`` is just
``--transport http``. An explicit flag wins over the environment, which is the
order every other tool uses and the one a developer debugging a container
expects.

This module imports only `service/` and `server/`, never `storage/`, which is
why :func:`agentprops.service.context_for` exists: process wiring that opens a
store belongs on the service side of the layering rule, and
`tests/unit/test_layering.py` asserts that `server/` reaches no further.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Final

from agentprops.server.app import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PATH,
    DEFAULT_HTTP_PORT,
    bind,
    serve_http,
    serve_stdio,
)
from agentprops.service import context_for

#: Where ``--store`` points when neither the flag nor the environment names a
#: target. A relative SQLite file: the containerless mode, and the only default
#: that cannot reach a database someone else cares about.
DEFAULT_STORE_TARGET: Final = "agentprops.db"

#: The environment variables each argument falls back to. Named on the
#: ``AGENTPROPS_`` prefix so a container's environment is greppable, and listed
#: here rather than inline so the compose file and this module cannot disagree
#: about a spelling.
STORE_ENV_VAR: Final = "AGENTPROPS_STORE"
HTTP_HOST_ENV_VAR: Final = "AGENTPROPS_HTTP_HOST"
HTTP_PORT_ENV_VAR: Final = "AGENTPROPS_HTTP_PORT"
HTTP_PATH_ENV_VAR: Final = "AGENTPROPS_HTTP_PATH"
TRANSPORT_ENV_VAR: Final = "AGENTPROPS_TRANSPORT"


def _from_environment(name: str, fallback: str) -> str:
    """``name`` from the environment, or ``fallback``.

    An **empty** variable counts as unset. Compose writes an empty string for a
    variable that was interpolated from nothing, and treating that as "listen on
    the empty host" is how a container becomes unreachable for a reason nothing
    logs.
    """
    return os.environ.get(name) or fallback


def _port_from_environment(name: str, fallback: int) -> int:
    """``name`` from the environment as an ``int``, or ``fallback``.

    A non-numeric value falls back rather than raising: this is a process
    starting up, and a stack trace from ``int()`` names the wrong thing. The
    flag's own ``type=int`` still rejects a bad *argument*, where the caller is
    a human who can read the message.
    """
    raw = os.environ.get(name, "")
    return int(raw) if raw.isdigit() else fallback


def parser() -> argparse.ArgumentParser:
    """The argument parser, exposed so a test can exercise it without serving."""
    parsed = argparse.ArgumentParser(
        prog="python -m agentprops.server",
        description="Serve the agent-props MCP tool surface.",
    )
    parsed.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default=_from_environment(TRANSPORT_ENV_VAR, "stdio"),
        help=f"stdio (default) or streamable HTTP; ${TRANSPORT_ENV_VAR}",
    )
    parsed.add_argument(
        "--store",
        default=_from_environment(STORE_ENV_VAR, DEFAULT_STORE_TARGET),
        help=(
            "a SQLite file path, or a sqlite:// / postgresql:// / mongodb:// URL "
            f"(default: {DEFAULT_STORE_TARGET}); ${STORE_ENV_VAR}"
        ),
    )
    parsed.add_argument("--host", default=_from_environment(HTTP_HOST_ENV_VAR, DEFAULT_HTTP_HOST))
    parsed.add_argument(
        "--port", type=int, default=_port_from_environment(HTTP_PORT_ENV_VAR, DEFAULT_HTTP_PORT)
    )
    parsed.add_argument("--path", default=_from_environment(HTTP_PATH_ENV_VAR, DEFAULT_HTTP_PATH))
    return parsed


async def serve(options: argparse.Namespace) -> None:
    """Bind a store and run the chosen transport until it stops."""
    bind(context_for(options.store or DEFAULT_STORE_TARGET))
    if options.transport == "http":
        await serve_http(host=options.host, port=options.port, path=options.path)
    else:
        await serve_stdio()


def main() -> None:
    asyncio.run(serve(parser().parse_args()))


if __name__ == "__main__":
    main()
