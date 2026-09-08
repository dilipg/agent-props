"""The three admin tools. contracts section 4, Admin table.

All reads, none of them gating. ``store_status``'s ``healthy`` is a liveness
answer about the backend, not a verdict about the data: a store with zero
datasets is healthy.
"""

from __future__ import annotations

from typing import Any

from agentprops.server.app import bound, mcp
from agentprops.server.args import ArgReader, OptionalTextArg, TextArg, reply
from agentprops.service import admin, failure

__all__ = ["agent_list", "label_vocabulary", "store_status"]


@mcp.tool()
def store_status() -> dict[str, Any]:
    """Backend name, liveness, and row counts for blueprints, datasets and runs.

    The dataset count **includes archived datasets**: this is a store-health
    number, not a discovery number. `dataset_find` is the thing that hides
    archives.
    """
    return reply(admin.store_status(bound()))


@mcp.tool()
def label_vocabulary(agent_id: TextArg, version: OptionalTextArg = None) -> dict[str, Any]:
    """A blueprint's label vocabulary, plus a dataset count per label value.

    The latest published version when `version` is omitted. Dimensions and
    values keep the blueprint's declaration order, so identical inputs give
    byte-identical output. Counts exclude archived datasets and count one row
    per dataset lineage.
    """
    args = ArgReader()
    agent = args.text("agent_id", agent_id)
    wanted = args.optional_text("version", version)
    if args.errors:
        return reply(failure(args.errors))
    return reply(admin.label_vocabulary(bound(), agent, wanted))


@mcp.tool()
def agent_list() -> dict[str, Any]:
    """Every agent with a blueprint: its versions, and how many datasets it has.

    Versions keep the store's semver-aware order, so `1.10.0` follows `1.9.0`.
    Dataset counts exclude archived datasets.
    """
    return reply(admin.agents(bound()))
