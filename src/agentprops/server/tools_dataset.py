"""The eight dataset tools. contracts section 4, Dataset table.

Four are M4's reads, one is ``dataset_validate``, and the last three are M5's
skeleton pipeline. ``dataset_expand``, ``dataset_export`` and ``dataset_import``
are M7's: they go in this module, decorated the same way, and inherit the same
plumbing.

``dataset_submit`` is the **only tool on this surface that writes a dataset** -
ground rule 1's "nothing writes to a dataset except the authoring flow". All of
the behaviour is in `service/skeletons.py`; each function here reads its
arguments and delegates, the same shape as every other tool in this package.

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
    RequiredIntArg,
    TextArg,
    reply,
)
from agentprops.service import datasets, failure, skeletons

__all__ = [
    "dataset_archive",
    "dataset_fill_part",
    "dataset_find",
    "dataset_get",
    "dataset_restore",
    "dataset_skeleton",
    "dataset_submit",
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


@mcp.tool()
def dataset_skeleton(
    agent_id: TextArg, version: TextArg, labels: ObjectArg, seed: RequiredIntArg
) -> dict[str, Any]:
    """Start authoring a dataset: get a partially-filled skeleton to complete.

    Returns `skeleton_id`, the ordered section `manifest`, the partially-filled
    `skeleton` document, and `instructions` for filling it. Fill the sections in
    manifest order with `dataset_fill_part`, then call `dataset_submit`.

    `labels` and `seed` are fixed by this call and are not fillable sections. A
    label outside the blueprint's vocabulary cannot be repaired by re-filling,
    so check `label_vocabulary` first. Calling this again with identical
    arguments returns the same skeleton, with its filled sections intact.
    """
    args = ArgReader()
    agent = args.text("agent_id", agent_id)
    at = args.text("version", version)
    dimensions = args.required_labels("labels", labels)
    number = args.integer("seed", seed)
    if args.errors:
        return reply(failure(args.errors))
    return reply(skeletons.skeleton(bound(), agent, at, dimensions, number))


@mcp.tool()
def dataset_fill_part(skeleton_id: TextArg, section: TextArg, content: ObjectArg) -> dict[str, Any]:
    """Fill one section of a skeleton. Returns which sections are filled and which remain.

    `section` is one of the manifest's ids, and may not be filled before every
    earlier one is (SK-002). `content` is a fragment of the dataset document: an
    object whose keys are the section's own top-level fields, as its manifest
    entry says. Re-filling an already-filled section is allowed and replaces it,
    which is how a rejected submit is repaired one part at a time.
    """
    args = ArgReader()
    wanted = args.text("skeleton_id", skeleton_id)
    part = args.text("section", section)
    document = args.mapping("content", content)
    if args.errors:
        return reply(failure(args.errors))
    return reply(skeletons.fill_part(bound(), wanted, part, document))


@mcp.tool()
def dataset_submit(skeleton_id: TextArg) -> dict[str, Any]:
    """Submit a filled skeleton: validate every DS-* rule, then store the dataset.

    Returns the stored dataset, or every finding against it. Each error carries
    a rule id, an RFC 6901 pointer into the assembled document, and the section
    to re-fill. An unfilled required section is SK-004, and a skeleton becomes
    exactly one dataset (SK-005).
    """
    args = ArgReader()
    wanted = args.text("skeleton_id", skeleton_id)
    if args.errors:
        return reply(failure(args.errors))
    return reply(skeletons.submit(bound(), wanted))
