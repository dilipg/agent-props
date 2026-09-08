"""The five blueprint tools. contracts section 4, Blueprint table.

Every function here does the same four things in the same order - read the
arguments, check them, delegate to `service/`, dump the envelope - and nothing
else. There is no branch in this module that depends on what a blueprint
*means*: BP-016, the R-29 idempotent re-publish, the ``status`` normalisation
and the diff computation all live in `service/blueprints.py`, which is where a
reader looking for them will look and where they can be tested without an MCP
client.

Adding a tool here is four lines of ceremony and one line of delegation. See
`server/__init__.py` for the registration story.
"""

from __future__ import annotations

from typing import Any

from agentprops.server.app import bound, mcp
from agentprops.server.args import ArgReader, BoolArg, ObjectArg, OptionalTextArg, TextArg, reply
from agentprops.service import blueprints, failure

__all__ = [
    "blueprint_diff",
    "blueprint_get",
    "blueprint_list",
    "blueprint_upsert",
    "blueprint_validate",
]


@mcp.tool()
def blueprint_upsert(blueprint: ObjectArg, publish: BoolArg = False) -> dict[str, Any]:
    """Store a blueprint, as a draft or as a published version.

    A published version is immutable (BP-016), with one exception: re-submitting
    a byte-identical document is a no-op success, so a CI pipeline that
    publishes on every run and an import into a store that already holds the
    version are both idempotent. `status` is derived from `publish` and may be
    omitted from the document.
    """
    args = ArgReader()
    flag = args.flag("publish", publish)
    if args.errors:
        return reply(failure(args.errors))
    return reply(blueprints.upsert(bound(), args.document("blueprint", blueprint), publish=flag))


@mcp.tool()
def blueprint_get(agent_id: TextArg, version: OptionalTextArg = None) -> dict[str, Any]:
    """Fetch one blueprint. The latest published version when `version` is omitted."""
    args = ArgReader()
    agent = args.text("agent_id", agent_id)
    wanted = args.optional_text("version", version)
    if args.errors:
        return reply(failure(args.errors))
    return reply(blueprints.get(bound(), agent, wanted))


@mcp.tool()
def blueprint_list(status: OptionalTextArg = None) -> dict[str, Any]:
    """List blueprint summaries, ordered by `(agent_id, version)` with version as semver.

    `status` filters on `draft` or `published`. Any other value matches nothing
    and returns an empty list rather than an error.
    """
    args = ArgReader()
    wanted = args.optional_text("status", status)
    if args.errors:
        return reply(failure(args.errors))
    return reply(blueprints.list_summaries(bound(), wanted))


@mcp.tool()
def blueprint_validate(blueprint: ObjectArg) -> dict[str, Any]:
    """Validate a blueprint document against the BP-* catalogue. Stores nothing.

    Returns `{ok, errors}`. A document whose only findings are warnings reports
    `ok: true` with those warnings in `errors`.
    """
    return reply(blueprints.validate(bound(), blueprint))


@mcp.tool()
def blueprint_diff(agent_id: TextArg, from_version: TextArg, to_version: TextArg) -> dict[str, Any]:
    """Compare two blueprint versions: nodes, edges, per-node schemas, label vocabulary.

    Informational and never a failure signal. A version that does not exist is
    reported as `present: false` plus a `blueprint_version_missing` warning, on
    a successful response.
    """
    args = ArgReader()
    agent = args.text("agent_id", agent_id)
    before = args.text("from_version", from_version)
    after = args.text("to_version", to_version)
    if args.errors:
        return reply(failure(args.errors))
    return reply(blueprints.diff(bound(), agent, before, after))
