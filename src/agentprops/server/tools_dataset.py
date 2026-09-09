"""The eight dataset tools. contracts section 4, Dataset table.

Four are M4's reads, one is ``dataset_validate``, three are M5's skeleton
pipeline, and the last three are M7's - ``dataset_expand``, ``dataset_export``
and ``dataset_import``, decorated the same way and inheriting the same
plumbing, which is what "eight" became "eleven" without a new module.

**Three tools on this surface write a dataset**, and all three are the
authoring flow rather than the runtime: ``dataset_submit`` (`service/skeletons.py`),
``dataset_expand`` (`service/expansion.py`) and ``dataset_import``
(`service/promotion.py`). Ground rule 1 is about the *runtime* - "nothing writes
to a dataset except the authoring flow" - and
`tests/unit/test_runtime_is_read_only.py` enumerates those three ``put_dataset``
call sites by name, so a fourth is a visible decision rather than a silent
widening. All of the behaviour is in `service/`; each function here reads its
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
    OptionalTextArrayArg,
    RequiredIntArg,
    TextArg,
    reply,
)
from agentprops.service import datasets, expansion, failure, promotion, skeletons

__all__ = [
    "dataset_archive",
    "dataset_expand",
    "dataset_export",
    "dataset_fill_part",
    "dataset_find",
    "dataset_get",
    "dataset_import",
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
    entry says, and it must carry at least one of them. Re-filling an
    already-filled section is allowed and replaces it, which is how a rejected
    submit is repaired one part at a time.
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


@mcp.tool()
def dataset_expand(dataset_id: TextArg, node_id: TextArg, count: RequiredIntArg) -> dict[str, Any]:
    """Add `count` deterministic entries to a pool node, as a new dataset version.

    Seeded from the dataset's own `seed` and the entry's position in the pool,
    so the same request always produces the same entries and expanding by 5
    equals expanding by 2 then 3. Each new entry is a copy of one of the
    node's authored fixtures with its `latency_hint_ms` varied; expansion fills
    volume, it does not invent fixture content.

    Copy-on-write, like every dataset edit: the pre-expansion version stays
    readable. An expansion that would push a loop node's pool past
    `max_iterations` is refused with `DS-023`, naming both numbers, and stores
    nothing. `count` is at most 10000 per call.
    """
    args = ArgReader()
    wanted = args.text("dataset_id", dataset_id)
    node = args.text("node_id", node_id)
    many = args.integer("count", count)
    if args.errors:
        return reply(failure(args.errors))
    return reply(expansion.expand(bound(), wanted, node, many))


@mcp.tool()
def dataset_export(agent_id: TextArg, dataset_ids: OptionalTextArrayArg = None) -> dict[str, Any]:
    """Export a portable bundle: the datasets plus the blueprint versions they need.

    Omit `dataset_ids` to export every discoverable dataset for the agent, at
    its latest version, in `dataset_find` order. Name them to export those
    lineages, archived or not, in the order given; an id that names nothing, or
    a dataset belonging to another agent, is `AP-004`.

    The bundle is `{format, format_version, agent_id, blueprints, datasets}` and
    is what `dataset_import` takes. Feed it to `dataset_import` on another
    store to promote a locally authored dataset to a shared one.
    """
    args = ArgReader()
    agent = args.text("agent_id", agent_id)
    wanted = args.text_array("dataset_ids", dataset_ids)
    if args.errors:
        return reply(failure(args.errors))
    return reply(promotion.export_bundle(bound(), agent, wanted))


@mcp.tool()
def dataset_import(bundle: ObjectArg) -> dict[str, Any]:
    """Import a `dataset_export` bundle, re-running full validation on arrival.

    Every blueprint is checked against the whole `BP-*` catalogue and every
    dataset against the whole `DS-*` catalogue, against *this* store - so
    DS-001 and DS-031, which are existence checks, are answered here rather
    than trusted from wherever the bundle came from. Nothing is written unless
    all of it passes, and each finding's pointer names which document in the
    bundle it came from (`/datasets/2/provenance/title`).

    Returns the `{agent_id, version}` of each blueprint published and the
    `{id, version}` of each dataset stored. Version numbers are allocated by
    this store, so an imported dataset starts at version 1; `created_at` is
    authored content and survives, which is what keeps `dataset_find`'s
    ordering identical on both sides.
    """
    return reply(promotion.import_bundle(bound(), bundle))
