"""``python -m agentprops.server`` - open a store, bind it, serve one transport.

Not the CLI phase 1 rules out. That non-goal is about an authoring CLI; this is
the process entry point for a server whose deliverable is "``MCPServer`` with
stdio and streamable HTTP transports", and a transport with no way to start it
cannot be demonstrated to work.

Four arguments and no configuration file, on purpose::

    python -m agentprops.server --store ./agentprops.db
    python -m agentprops.server --transport http --port 8931

``--store`` is a **SQLite file path**, defaulting to `./agentprops.db`. The
schema is created if absent, so a fresh file needs no migration step;
`alembic upgrade head` remains the right thing for a database that has to
survive a schema *change*, and `tests/integration/test_migrations.py` is what
proves the two agree. There is no in-memory option, for the reason
:func:`agentprops.service.sqlite_context` records.

A full SQLAlchemy URL is deliberately not accepted yet. The Postgres and Mongo
adapters land at M7, and a URL argument that silently ran `create_schema`
against a migrated Postgres database would be worse than not having one.
:func:`agentprops.service.context_from_url` is the seam M7 wires in here.

This module imports only `service/` and `server/`, never `storage/`, which is
why :func:`agentprops.service.sqlite_context` exists: process wiring that opens
a store belongs on the service side of the layering rule, and
`tests/unit/test_layering.py` asserts that `server/` reaches no further.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Final

from agentprops.server.app import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PATH,
    DEFAULT_HTTP_PORT,
    bind,
    serve_http,
    serve_stdio,
)
from agentprops.service import sqlite_context

#: Where ``--store`` points when it is not given.
DEFAULT_STORE_PATH: Final = "agentprops.db"


def parser() -> argparse.ArgumentParser:
    """The argument parser, exposed so a test can exercise it without serving."""
    parsed = argparse.ArgumentParser(
        prog="python -m agentprops.server",
        description="Serve the agent-props MCP tool surface.",
    )
    parsed.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="stdio (default) or streamable HTTP",
    )
    parsed.add_argument(
        "--store",
        default=DEFAULT_STORE_PATH,
        help=f"SQLite database file path (default: {DEFAULT_STORE_PATH})",
    )
    parsed.add_argument("--host", default=DEFAULT_HTTP_HOST)
    parsed.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    parsed.add_argument("--path", default=DEFAULT_HTTP_PATH)
    return parsed


async def serve(options: argparse.Namespace) -> None:
    """Bind a store and run the chosen transport until it stops."""
    bind(sqlite_context(options.store or DEFAULT_STORE_PATH))
    if options.transport == "http":
        await serve_http(host=options.host, port=options.port, path=options.path)
    else:
        await serve_stdio()


def main() -> None:
    asyncio.run(serve(parser().parse_args()))


if __name__ == "__main__":
    main()
