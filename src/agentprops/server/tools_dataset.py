"""The five dataset tools M4 owns. contracts section 4, Dataset table.

``dataset_skeleton``, ``dataset_fill_part`` and ``dataset_submit`` are M5's;
``dataset_expand``, ``dataset_export`` and ``dataset_import`` are M7's. They go
in this module, decorated the same way, and inherit the same plumbing.

``dataset_validate`` is here rather than at M5 because ruling R-15 assigns it to
M4 beside ``blueprint_validate``. It is also the only tool on this surface with
a second, ``str``-annotated argument: ``dataset_json`` is ruling R-20's raw-text
boundary, and `service/documents.py` carries the evidence for why the parsed
argument cannot reach DS-013.
"""

from __future__ import annotations

from typing import Any

from agentprops.models import DatasetQuery
from agentprops.server.app import bound, mcp
from agentprops.server.args import (
    ArgReader,
    IntArg,
    ObjectArg,
    OptionalObjectArg,
    OptionalTextArg,
    TextArg,
    reply,
)
from agentprops.service import datasets, failure

__all__ = [
    "dataset_archive",
    "dataset_find",
    "dataset_get",
    "dataset_restore",
    "dataset_validate",
]


@mcp.tool()
def dataset_find(
    agent_id: OptionalTextArg = None,
    labels: OptionalObjectArg = None,
    author: OptionalTextArg = None,
    q: OptionalTextArg = None,
    blueprint_version: OptionalTextArg = None,
    limit: IntArg = None,
    offset: IntArg = None,
) -> dict[str, Any]:
    """Find datasets. One row per dataset lineage, at its latest version.

    Archived datasets are excluded. Ordering is `(created_at, id)`, which is
    total, so identical inputs return byte-identical output. `labels` matches
    every dimension given, exactly. `author` matches
    `provenance.author.handle`. `q` is a case-folded substring match over
    `title` and `intent`. `limit` defaults to 50.
    """
    args = ArgReader()
    query = DatasetQuery(
        agent_id=args.optional_text("agent_id", agent_id),
        labels=args.labels("labels", labels),
        author=args.optional_text("author", author),
        q=args.optional_text("q", q),
        blueprint_version=args.optional_text("blueprint_version", blueprint_version),
        limit=args.number("limit", limit),
        offset=args.number("offset", offset),
    )
    if args.errors:
        return reply(failure(args.errors))
    return reply(datasets.find(bound(), query))


@mcp.tool()
def dataset_get(dataset_id: TextArg, version: IntArg = None) -> dict[str, Any]:
    """Fetch one dataset in full. The latest version when `version` is omitted.

    Returns archived datasets, with a `dataset_archived` warning: a run holding
    a pin has to keep reading a dataset that was archived after it started.
    """
    args = ArgReader()
    wanted = args.text("dataset_id", dataset_id)
    at = args.number("version", version)
    if args.errors:
        return reply(failure(args.errors))
    return reply(datasets.get(bound(), wanted, at))


@mcp.tool()
def dataset_archive(dataset_id: TextArg) -> dict[str, Any]:
    """Hide a dataset from discovery. Every version of the lineage, never a delete."""
    args = ArgReader()
    wanted = args.text("dataset_id", dataset_id)
    if args.errors:
        return reply(failure(args.errors))
    return reply(datasets.archive(bound(), wanted))


@mcp.tool()
def dataset_restore(dataset_id: TextArg) -> dict[str, Any]:
    """Un-hide an archived dataset. Every version of the lineage."""
    args = ArgReader()
    wanted = args.text("dataset_id", dataset_id)
    if args.errors:
        return reply(failure(args.errors))
    return reply(datasets.restore(bound(), wanted))


@mcp.tool()
def dataset_validate(dataset: ObjectArg = None, dataset_json: str = "") -> dict[str, Any]:
    """Validate a dataset document against the DS-* catalogue. Stores nothing.

    Give either `dataset` as an object or `dataset_json` as raw JSON text, not
    both. The raw-text form is the only one that can report DS-013 (no label
    dimension appears twice), because duplicate keys stop existing the moment
    JSON is parsed.
    """
    return reply(datasets.validate(bound(), dataset, dataset_json))
