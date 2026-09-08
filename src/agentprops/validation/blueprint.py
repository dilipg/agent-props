"""The ``BP-*`` rules: `docs/contracts.md` section 3.1.

Every function here takes a :class:`~agentprops.validation.context.BlueprintContext`
and returns a list of findings. None of them raises - a malformed document is a
finding, never an exception (CLAUDE.md's style rule) - and none of them reads a
clock, a random source or the filesystem.

Four of the nineteen are not what the catalogue's wording literally says, and
each carries the ruling that changed it:

- **BP-005** is skipped when ``entry_node`` names no node. Reachability from a
  node that does not exist is empty, so a literal implementation reports every
  node in the graph as unreachable on top of BP-006's one finding. BP-006 owns
  the existence half (ruling R-26).
- **BP-011** checks syntax with ``entity:`` refs *stripped*, so an unresolvable
  entity is BP-009 alone (ruling R-18).
- **BP-018** is checked by deleting the loop nodes and looking for a remaining
  cycle, which is equivalent to "every cycle passes through a loop node" and
  decidable in linear time.
- **BP-019** fires when a node is **missing** ``notes``. The catalogue states
  the satisfied condition; ruling R-13 reads it as the violation, and makes it a
  warning.

Two rules were widened after M2's review, each closing a hole M2 reported:

- **BP-011** also checks ``entities[*].schema`` (ruling R-27). Nothing did
  before, because R-18 has BP-011 strip the very refs that reach an entity.
- **BP-016** does *not* fire on a byte-identical re-publish (ruling R-29), so
  M7's import stays idempotent.
"""

import re
from collections.abc import Mapping
from typing import Any, Final

from agentprops.models.errors import RuleError
from agentprops.validation.context import BlueprintContext, as_int, as_text
from agentprops.validation.jsonschemas import (
    canonical,
    declared_properties,
    entity_ref_names,
    schema_error,
    strip_entity_refs,
)
from agentprops.validation.pointers import pointer

__all__ = [
    "AGENT_ID_PATTERN",
    "SEMVER_PATTERN",
    "bp_001",
    "bp_002",
    "bp_003",
    "bp_004",
    "bp_005",
    "bp_006",
    "bp_007",
    "bp_008",
    "bp_009",
    "bp_010",
    "bp_011",
    "bp_012",
    "bp_013",
    "bp_014",
    "bp_015",
    "bp_016",
    "bp_017",
    "bp_018",
    "bp_019",
    "condition_var_paths",
]

#: `docs/contracts.md` section 2.1. Three characters minimum, since the middle
#: class is ``{1,62}`` and both ends are anchored.
AGENT_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")

#: semver.org's own recommended regex, anchored: ``MAJOR.MINOR.PATCH`` with
#: optional pre-release and build metadata, and no leading zeroes.
SEMVER_PATTERN: Final = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

SCHEMA_KEYS: Final = ("input_schema", "output_schema")


def bp_001(ctx: BlueprintContext) -> list[RuleError]:
    """``agent_id`` matches ``^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$``."""
    agent_id = ctx.bp.agent_id
    if agent_id is not None and AGENT_ID_PATTERN.match(agent_id):
        return []
    return [
        ctx.error(
            "BP-001",
            pointer("agent_id"),
            "agent_id must be lowercase alphanumeric with internal hyphens, 3 to 64 characters.",
            agent_id=ctx.doc.get("agent_id"),
        )
    ]


def bp_002(ctx: BlueprintContext) -> list[RuleError]:
    """``version`` is valid semver."""
    version = ctx.bp.version
    if version is not None and SEMVER_PATTERN.match(version):
        return []
    return [
        ctx.error(
            "BP-002",
            pointer("version"),
            "version must be semver, as in 1.0.0.",
            version=ctx.doc.get("version"),
        )
    ]


def bp_003(ctx: BlueprintContext) -> list[RuleError]:
    """Node ids are unique."""
    return [
        ctx.error(
            "BP-003",
            pointer("nodes", index, "id"),
            f"node id {node_id!r} is already declared earlier in nodes.",
            node_id=node_id,
            first_index=ctx.bp.node_indexes.get(node_id),
        )
        for index, node_id in ctx.bp.duplicate_node_ids
    ]


def bp_004(ctx: BlueprintContext) -> list[RuleError]:
    """Every edge ``from`` and ``to`` references an existing node."""
    findings: list[RuleError] = []
    known = set(ctx.bp.node_ids)
    for index, edge in enumerate(ctx.bp.edges):
        for end in ("from", "to"):
            node_id = as_text(edge.get(end))
            if node_id is None or node_id not in known:
                findings.append(
                    ctx.error(
                        "BP-004",
                        pointer("edges", index, end),
                        f"edge {end} {edge.get(end)!r} is not a declared node.",
                        node_id=edge.get(end),
                    )
                )
    return findings


def bp_005(ctx: BlueprintContext) -> list[RuleError]:
    """Every node is reachable from ``entry_node``.

    Skipped when ``entry_node`` names no node: BP-006 owns that, and reporting
    every node as unreachable on top of it would break the exact-set gate.
    """
    entry = ctx.bp.entry_node
    if entry is None or entry not in ctx.bp.node_by_id:
        return []
    reachable = ctx.bp.graph.reachable_from(entry, include_start=True)
    return [
        ctx.error(
            "BP-005",
            ctx.bp.node_pointer(node_id),
            f"node {node_id!r} is not reachable from entry node {entry!r}.",
            node_id=node_id,
            entry_node=entry,
        )
        for node_id in ctx.bp.node_ids
        if node_id not in reachable
    ]


def bp_006(ctx: BlueprintContext) -> list[RuleError]:
    """``entry_node`` exists and has no inbound edges."""
    entry = ctx.bp.entry_node
    if entry is None or entry not in ctx.bp.node_by_id:
        return [
            ctx.error(
                "BP-006",
                pointer("entry_node"),
                f"entry_node {ctx.doc.get('entry_node')!r} is not a declared node.",
                entry_node=ctx.doc.get("entry_node"),
            )
        ]
    inbound = sorted(ctx.bp.graph.predecessors(entry))
    if not inbound:
        return []
    return [
        ctx.error(
            "BP-006",
            pointer("entry_node"),
            f"entry_node {entry!r} has inbound edges from {inbound}, so it is not an entry point.",
            entry_node=entry,
            inbound_from=inbound,
        )
    ]


def bp_007(ctx: BlueprintContext) -> list[RuleError]:
    """At least one node has ``kind: terminal``, and terminal nodes have no outbound edges."""
    if not ctx.bp.terminal_node_ids:
        return [
            ctx.error(
                "BP-007",
                pointer("nodes"),
                "no node declares kind: terminal, so no run can end.",
            )
        ]
    findings: list[RuleError] = []
    for node_id in sorted(ctx.bp.terminal_node_ids):
        outbound = sorted(ctx.bp.graph.successors(node_id))
        if outbound:
            findings.append(
                ctx.error(
                    "BP-007",
                    ctx.bp.node_pointer(node_id),
                    f"terminal node {node_id!r} has outbound edges to {outbound}.",
                    node_id=node_id,
                    outbound_to=outbound,
                )
            )
    return findings


def bp_008(ctx: BlueprintContext) -> list[RuleError]:
    """``kind: loop`` nodes declare ``max_iterations`` as an integer greater than 0.

    Both halves live here. The "is an integer" half is *also* enforced by
    ``StrictInt`` on the model, but ruling R-23 runs this against the raw
    document first, so the rule id is reachable.
    """
    findings: list[RuleError] = []
    for node_id in sorted(ctx.bp.loop_node_ids):
        raw = ctx.bp.node_by_id[node_id].get("max_iterations")
        value = as_int(raw)
        if value is None or value <= 0:
            findings.append(
                ctx.error(
                    "BP-008",
                    ctx.bp.node_pointer(node_id, "max_iterations"),
                    f"loop node {node_id!r} must declare max_iterations as an integer > 0, "
                    f"got {raw!r}.",
                    node_id=node_id,
                    max_iterations=raw,
                )
            )
    return findings


def bp_009(ctx: BlueprintContext) -> list[RuleError]:
    """Every ``entity:`` ref in any node schema resolves to a declared entity."""
    findings: list[RuleError] = []
    known = set(ctx.bp.entity_schemas)
    for node_id in ctx.bp.node_ids:
        for key in SCHEMA_KEYS:
            for path, name in entity_ref_names(ctx.bp.schema_of(node_id, key)):
                if name in known:
                    continue
                findings.append(
                    ctx.error(
                        "BP-009",
                        ctx.bp.node_pointer(node_id, key) + pointer(*path, "$ref"),
                        f"node {node_id!r} references entity {name!r}, which is not declared.",
                        node_id=node_id,
                        entity=name,
                    )
                )
    return findings


def condition_var_paths(condition: Any) -> list[str]:
    """Every ``var`` path anywhere in a JSONLogic condition tree (ruling R-19).

    Both operand forms are accepted: ``{"var": "path"}`` and
    ``{"var": ["path", default]}``. The tree is walked *structurally* and never
    evaluated, so no operator subset has to be defined and JSONLogic is not a
    dependency.
    """
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                if key == "var":
                    if isinstance(value, str):
                        found.append(value)
                    elif isinstance(value, list) and value and isinstance(value[0], str):
                        found.append(value[0])
                    if not isinstance(value, str):
                        walk(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(condition)
    return found


def bp_010(ctx: BlueprintContext) -> list[RuleError]:
    """Every ``var`` path in an edge condition exists as a property in the source
    node's ``output_schema``.

    Dotted paths are split on ``.`` and walked one segment at a time, with
    ``entity:`` refs resolved, reporting the first segment the schema does not
    declare (ruling R-19). An empty path (``{"var": ""}``) means "the whole
    object" in JSONLogic and is not a property reference, so it is skipped.
    """
    findings: list[RuleError] = []
    for index, edge in enumerate(ctx.bp.edges):
        condition = edge.get("condition")
        if condition is None:
            continue
        source = as_text(edge.get("from"))
        if source is None or source not in ctx.bp.node_by_id:
            continue  # BP-004 owns an edge with an unknown source
        schema = ctx.bp.schema_of(source, "output_schema")
        for path in condition_var_paths(condition):
            if path == "":
                continue
            current: Any = schema
            walked: list[str] = []
            for segment in path.split("."):
                properties = declared_properties(current, ctx.bp.entity_schemas)
                if segment not in properties:
                    findings.append(
                        ctx.error(
                            "BP-010",
                            pointer("edges", index, "condition"),
                            f"condition references {path!r}, but {'.'.join(walked) or source!r} "
                            f"declares no property {segment!r}.",
                            node_id=source,
                            var_path=path,
                            missing_segment=segment,
                        )
                    )
                    break
                current = properties[segment]
                walked.append(segment)
    return findings


def bp_011(ctx: BlueprintContext) -> list[RuleError]:
    """Every ``input_schema``, ``output_schema`` and entity ``schema`` is valid
    Draft 2020-12 JSON Schema.

    Checked with ``entity:`` refs stripped (ruling R-18), so an unresolvable
    entity reports BP-009 and nothing else.

    Entity schemas were the hole ruling R-27 closed. BP-011 covered node
    schemas, BP-012 ``outcome_schema``, and R-18's stripping meant nobody ever
    looked at ``entities[*].schema`` - so a malformed entity schema published,
    and the first symptom was a confusing DS-004 finding one milestone later,
    when a fixture failed to validate against a schema that could not be
    applied. Extending the existing owner keeps the registry and the corpus
    stable.
    """
    findings: list[RuleError] = []
    for node_id in ctx.bp.node_ids:
        for key in SCHEMA_KEYS:
            reason = schema_error(strip_entity_refs(ctx.bp.schema_of(node_id, key)))
            if reason is not None:
                findings.append(
                    ctx.error(
                        "BP-011",
                        ctx.bp.node_pointer(node_id, key),
                        f"{key} of node {node_id!r} is not valid Draft 2020-12 JSON Schema: "
                        f"{reason}",
                        node_id=node_id,
                        schema_key=key,
                    )
                )
    for index, entity in enumerate(ctx.bp.entities):
        reason = schema_error(strip_entity_refs(entity.get("schema")))
        if reason is not None:
            findings.append(
                ctx.error(
                    "BP-011",
                    pointer("entities", index, "schema"),
                    f"schema of entity {entity.get('id')!r} is not valid Draft 2020-12 "
                    f"JSON Schema: {reason}",
                    entity=entity.get("id"),
                    schema_key="schema",
                )
            )
    return findings


def bp_012(ctx: BlueprintContext) -> list[RuleError]:
    """``outcome_schema`` is valid Draft 2020-12 JSON Schema."""
    reason = schema_error(strip_entity_refs(ctx.bp.outcome_schema))
    if reason is None:
        return []
    return [
        ctx.error(
            "BP-012",
            pointer("outcome_schema"),
            f"outcome_schema is not valid Draft 2020-12 JSON Schema: {reason}",
        )
    ]


def bp_013(ctx: BlueprintContext) -> list[RuleError]:
    """Every ``label_schema`` dimension has at least one value, and values are unique."""
    findings: list[RuleError] = []
    for dimension, values in ctx.bp.label_dimensions.items():
        at = pointer("label_schema", "dimensions", dimension)
        if not values:
            findings.append(
                ctx.error(
                    "BP-013",
                    at,
                    f"label dimension {dimension!r} declares no values.",
                    dimension=dimension,
                )
            )
            continue
        seen: set[str] = set()
        duplicates: list[str] = []
        for value in values:
            key = value if isinstance(value, str) else repr(value)
            if key in seen and key not in duplicates:
                duplicates.append(key)
            seen.add(key)
        if duplicates:
            findings.append(
                ctx.error(
                    "BP-013",
                    at,
                    f"label dimension {dimension!r} repeats {duplicates}.",
                    dimension=dimension,
                    duplicates=duplicates,
                )
            )
    return findings


def bp_014(ctx: BlueprintContext) -> list[RuleError]:
    """Two nodes sharing a ``tool_name`` must not both be reachable in one step
    from any single node.

    Otherwise ``fetch_step`` cannot resolve a tool-name call, and CLAUDE.md
    forbids guessing: the ambiguity has to be impossible by construction, since
    at runtime it cannot be repaired.
    """
    findings: list[RuleError] = []
    reported: set[tuple[str, str]] = set()
    for source in ctx.bp.node_ids:
        by_tool: dict[str, list[str]] = {}
        for successor in sorted(ctx.bp.graph.successors(source)):
            tool_name = as_text(ctx.bp.node_by_id[successor].get("tool_name"))
            if tool_name is not None:
                by_tool.setdefault(tool_name, []).append(successor)
        for tool_name, node_ids in by_tool.items():
            if len(node_ids) < 2:
                continue
            for node_id in node_ids[1:]:
                key = (tool_name, node_id)
                if key in reported:
                    continue
                reported.add(key)
                findings.append(
                    ctx.error(
                        "BP-014",
                        ctx.bp.node_pointer(node_id, "tool_name"),
                        f"nodes {node_ids} share tool_name {tool_name!r} and are all one step "
                        f"from {source!r}, so a tool-name call cannot be resolved.",
                        tool_name=tool_name,
                        candidates=node_ids,
                        from_node=source,
                    )
                )
    return findings


def bp_015(ctx: BlueprintContext) -> list[RuleError]:
    """Entity ids are unique."""
    return [
        ctx.error(
            "BP-015",
            pointer("entities", index, "id"),
            f"entity id {entity_id!r} is already declared earlier in entities.",
            entity=entity_id,
        )
        for index, entity_id in ctx.bp.duplicate_entity_ids
    ]


def bp_016(ctx: BlueprintContext) -> list[RuleError]:
    """A ``published`` blueprint version cannot be **modified**.

    The resolver answers "is there already a published version at this
    ``{agent_id, version}``, and what is it?" (ruling R-11).
    ``validate_blueprint`` defaults to a resolver that always says no, so
    validating a document on its own terms - which is what
    ``blueprint_validate`` does - never fires this. The publish path passes a
    real resolver.

    **A byte-identical re-publish is a no-op success, not an error** (ruling
    R-29). contracts 3.1 says "any upsert against an existing published
    ``{agent_id, version}`` is rejected", and the literal reading was
    implemented first and flagged; R-29 rules that the rule fires only when the
    submitted document *differs* from the stored one. Two reasons: M7's
    ``dataset_import`` carries a blueprint version alongside its datasets, so
    re-importing into a store that already holds that version identically would
    fail, defeating the promotion path M7 exists to build; and a CI pipeline
    that publishes on every run is the normal case, not an abuse. Immutability
    is untouched - nothing changes, because the documents are identical.

    Comparison uses the same canonical form DS-008 uses, so a re-ordered key is
    not a modification.
    """
    agent_id = ctx.bp.agent_id
    version = ctx.bp.version
    if agent_id is None or version is None:
        return []  # BP-001 and BP-002 own these
    stored = ctx.resolver.get_published_blueprint(agent_id, version)
    if stored is None:
        return []
    if canonical(dict(stored)) == canonical(dict(ctx.doc)):
        return []  # ruling R-29: an identical upsert changes nothing
    return [
        ctx.error(
            "BP-016",
            pointer("version"),
            f"{agent_id} {version} is already published and published versions are immutable; "
            "publish a new version instead.",
            agent_id=agent_id,
            version=version,
        )
    ]


def bp_017(ctx: BlueprintContext) -> list[RuleError]:
    """``kind: loop`` implies ``pool: true``."""
    return [
        ctx.error(
            "BP-017",
            ctx.bp.node_pointer(node_id, "pool"),
            f"loop node {node_id!r} must declare pool: true; each iteration draws its own fixture.",
            node_id=node_id,
            pool=ctx.bp.node_by_id[node_id].get("pool"),
        )
        for node_id in sorted(ctx.bp.loop_node_ids)
        if ctx.bp.node_by_id[node_id].get("pool") is not True
    ]


def bp_018(ctx: BlueprintContext) -> list[RuleError]:
    """Every cycle in the graph passes through at least one ``kind: loop`` node.

    Checked by deleting the loop nodes and looking for a remaining cycle, which
    is the same statement and needs no cycle enumeration.
    """
    cycle = ctx.bp.graph.cycle_ignoring(ctx.bp.loop_node_ids)
    if cycle is None:
        return []
    return [
        ctx.error(
            "BP-018",
            pointer("edges"),
            f"the cycle {' -> '.join(cycle)} passes through no kind: loop node, so a run "
            "entering it cannot terminate.",
            cycle=cycle,
        )
    ]


def bp_019(ctx: BlueprintContext) -> list[RuleError]:
    """*(warning)* A node is missing ``notes``.

    Ruling R-13: the catalogue states the satisfied condition ("A node declares
    ``notes``"); the finding is the absence, and it is a warning, so a blueprint
    without notes still publishes.
    """
    findings: list[RuleError] = []
    for node_id in ctx.bp.node_ids:
        notes = as_text(ctx.bp.node_by_id[node_id].get("notes"))
        if notes is None or not notes.strip():
            findings.append(
                ctx.error(
                    "BP-019",
                    ctx.bp.node_pointer(node_id, "notes"),
                    f"node {node_id!r} declares no notes; generated fixtures for it will be "
                    "weaker.",
                    node_id=node_id,
                )
            )
    return findings
