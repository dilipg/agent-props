"""The ``DS-*`` rules except the entity timeline: `docs/contracts.md` section 3.2.

DS-008 to DS-011 live in `timeline.py`; everything else is here. Each function
takes a :class:`~agentprops.validation.context.DatasetContext` and returns a
list of findings, and the registry records which of them need the blueprint -
when DS-001 cannot resolve one, those are skipped rather than guessing.

The rules that are not what the catalogue literally says:

- **DS-002** exempts ``pool: true`` nodes, whose fixtures belong in ``pools``
  (ruling R-01). Taken literally the rule rejects both golden datasets.
- **DS-003** additionally covers a ``pool: true`` node appearing in *both*
  ``nodes`` and ``pools`` (ruling R-01).
- **DS-004** covers ``nodes`` only - pool fixtures are DS-019's (ruling R-18) -
  is skipped entirely when ``fault`` is set, and then requires ``output`` to be
  absent or an object (ruling R-07).
- **DS-013** is unreachable through a parsed JSON document, because a mapping
  cannot hold one key twice. It reads the duplicate keys the *tool boundary*
  found in the raw text (ruling R-20).
- **DS-019** owns every pool fixture, including the ``fault`` skip DS-004 gets,
  for the same reason (see DECISIONS.md).
- **DS-022** implements the unknown-key check itself, because ``FaultSpec`` is
  deliberately ``extra="allow"`` so the key reaches this rule instead of raising
  (ruling R-07).
- **DS-033** is a rule the register adds (ruling R-21): ``expected.rationale``
  mirrors DS-026's requirement on ``provenance.intent``, because contracts 2.2
  says both are required and only ``intent`` had a rule.
"""

import re
from itertools import pairwise
from typing import Any, Final

from agentprops.models.errors import RuleError
from agentprops.validation.context import (
    DatasetContext,
    as_int,
    as_mapping,
    as_sequence,
    as_text,
)
from agentprops.validation.jsonschemas import instance_findings, resolve_entity_refs
from agentprops.validation.pointers import pointer

__all__ = [
    "AUTHOR_AGENTS",
    "COMPARISONS",
    "FAULT_KINDS",
    "HANDLE_PATTERN",
    "MIN_INTENT_LENGTH",
    "MIN_NARRATIVE_LENGTH",
    "MIN_RATIONALE_LENGTH",
    "TITLE_MAX_LENGTH",
    "ds_001",
    "ds_002",
    "ds_003",
    "ds_004",
    "ds_005",
    "ds_006",
    "ds_007",
    "ds_012",
    "ds_013",
    "ds_014",
    "ds_015",
    "ds_016",
    "ds_017",
    "ds_018",
    "ds_019",
    "ds_020",
    "ds_021",
    "ds_022",
    "ds_023",
    "ds_024",
    "ds_025",
    "ds_026",
    "ds_027",
    "ds_028",
    "ds_029",
    "ds_030",
    "ds_031",
    "ds_032",
    "ds_033",
]

#: `docs/contracts.md` section 2.2's provenance table.
HANDLE_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
AUTHOR_AGENTS: Final[frozenset[str]] = frozenset({"claude-code", "codex", "human", "generator"})
COMPARISONS: Final[frozenset[str]] = frozenset({"exact", "schema", "subset"})
FAULT_KINDS: Final[frozenset[str]] = frozenset({"error", "timeout", "malformed"})
TITLE_MAX_LENGTH: Final = 120
MIN_INTENT_LENGTH: Final = 30
MIN_NARRATIVE_LENGTH: Final = 30
MIN_RATIONALE_LENGTH: Final = 30

#: The keys ``FaultSpec`` declares (ruling R-07). ``kind`` is required.
FAULT_KEYS: Final[frozenset[str]] = frozenset({"kind", "code", "after_ms"})


def ds_001(ctx: DatasetContext) -> list[RuleError]:
    """``blueprint`` references an existing published blueprint at that exact version.

    Answered by the injected resolver, never by a store call from this package
    (ruling R-11). When this fires, the runner skips every rule that needs the
    blueprint: there is nothing to check a fixture against, and reporting twenty
    consequential errors would bury the one that matters.
    """
    ref = ctx.ds.blueprint_ref
    agent_id = as_text(ref.get("agent_id"))
    version = as_text(ref.get("version"))
    if (
        agent_id is not None
        and version is not None
        and ctx.resolver.get_published_blueprint(agent_id, version) is not None
    ):
        return []
    return [
        ctx.error(
            "DS-001",
            pointer("blueprint"),
            f"no published blueprint {agent_id!r} at version {version!r}.",
            agent_id=agent_id,
            version=version,
        )
    ]


def ds_002(ctx: DatasetContext) -> list[RuleError]:
    """Every blueprint node has an entry in ``nodes``, except ``pool: true`` nodes.

    Ruling R-01: a pool node's fixtures live in ``pools``, which DS-018 and
    DS-019 own. Both golden datasets do exactly that, and the literal rule
    rejects both.
    """
    return [
        ctx.error(
            "DS-002",
            pointer("nodes"),
            f"blueprint node {node_id!r} has no fixture in nodes.",
            node_id=node_id,
        )
        for node_id in ctx.blueprint.node_ids
        if node_id not in ctx.blueprint.pool_node_ids and node_id not in ctx.ds.nodes
    ]


def ds_003(ctx: DatasetContext) -> list[RuleError]:
    """``nodes`` contains no key that is not a blueprint node.

    Plus ruling R-01's addition: a ``pool: true`` node that appears in *both*
    ``nodes`` and ``pools`` is a DS-003 error rather than silently accepted. A
    pool node in ``nodes`` alone is DS-018's finding - there is no pool where
    one is required.
    """
    findings: list[RuleError] = []
    for node_id in sorted(ctx.ds.nodes):
        if node_id not in ctx.blueprint.node_by_id:
            findings.append(
                ctx.error(
                    "DS-003",
                    pointer("nodes", node_id),
                    f"nodes has an entry for {node_id!r}, which is not a blueprint node.",
                    node_id=node_id,
                )
            )
        elif node_id in ctx.blueprint.pool_node_ids and node_id in ctx.ds.pools:
            findings.append(
                ctx.error(
                    "DS-003",
                    pointer("nodes", node_id),
                    f"pool node {node_id!r} has a fixture in both nodes and pools; its fixtures "
                    "belong in pools alone.",
                    node_id=node_id,
                )
            )
    return findings


def _fault_of(fixture: Any) -> Any:
    """A fixture's ``fault``, or ``None`` when it is absent or null."""
    return as_mapping(fixture).get("fault")


def ds_004(ctx: DatasetContext) -> list[RuleError]:
    """Each fixture ``output`` validates against its node's ``output_schema``,
    unless ``fault`` is set.

    ``nodes`` only (ruling R-18: pool fixtures are DS-019's), and skipped
    entirely for an unknown node key (DS-003's) or a pool node (DS-003/DS-018's).
    When ``fault`` is set, ruling R-07 replaces the schema check with "``output``
    is absent or an object".
    """
    findings: list[RuleError] = []
    for node_id in sorted(ctx.ds.nodes):
        if node_id not in ctx.blueprint.node_by_id or node_id in ctx.blueprint.pool_node_ids:
            continue
        fixture = ctx.ds.nodes[node_id]
        at = pointer("nodes", node_id, "output")
        if _fault_of(fixture) is not None:
            output = fixture.get("output")
            if "output" in fixture and not isinstance(output, dict):
                findings.append(
                    ctx.error(
                        "DS-004",
                        at,
                        f"faulted fixture {node_id!r} must carry no output or an object, "
                        f"got {type(output).__name__}.",
                        node_id=node_id,
                    )
                )
            continue
        if "output" not in fixture:
            continue  # a required field: the model owns presence (ruling R-04)
        schema = resolve_entity_refs(
            ctx.blueprint.schema_of(node_id, "output_schema"), ctx.blueprint.entity_schemas
        )
        findings.extend(
            ctx.error(
                "DS-004",
                finding.pointer_under(at),
                f"fixture output for {node_id!r} violates output_schema: {finding.message}",
                node_id=node_id,
            )
            for finding in instance_findings(fixture.get("output"), schema)
        )
    return findings


def ds_005(ctx: DatasetContext) -> list[RuleError]:
    """Each fixture ``input``, when present, validates against its node's ``input_schema``.

    ``nodes`` only, for the same reason as DS-004. ``input`` is optional - the
    golden datasets omit it on both terminal nodes - so an absent or null
    ``input`` is not a finding.
    """
    findings: list[RuleError] = []
    for node_id in sorted(ctx.ds.nodes):
        if node_id not in ctx.blueprint.node_by_id or node_id in ctx.blueprint.pool_node_ids:
            continue
        fixture = ctx.ds.nodes[node_id]
        if fixture.get("input") is None:
            continue
        at = pointer("nodes", node_id, "input")
        schema = resolve_entity_refs(
            ctx.blueprint.schema_of(node_id, "input_schema"), ctx.blueprint.entity_schemas
        )
        findings.extend(
            ctx.error(
                "DS-005",
                finding.pointer_under(at),
                f"fixture input for {node_id!r} violates input_schema: {finding.message}",
                node_id=node_id,
            )
            for finding in instance_findings(fixture.get("input"), schema)
        )
    return findings


def ds_006(ctx: DatasetContext) -> list[RuleError]:
    """Every id in ``entity_refs`` resolves to a declared entity.

    The ``entity_id`` segment only (ruling R-18): DS-009 owns ``@revision``.
    """
    findings: list[RuleError] = []
    for node_id, prefix, fixture in ctx.ds.fixture_sites():
        for index, raw in enumerate(as_sequence(fixture.get("entity_refs"))):
            ref = as_text(raw)
            entity_id = ref.split("@", 1)[0] if ref is not None else None
            if entity_id is not None and entity_id in ctx.ds.entities:
                continue
            findings.append(
                ctx.error(
                    "DS-006",
                    prefix + pointer("entity_refs", index),
                    f"{node_id!r} references entity {entity_id!r}, which the dataset does not "
                    "declare.",
                    node_id=node_id,
                    entity=entity_id,
                )
            )
    return findings


def ds_007(ctx: DatasetContext) -> list[RuleError]:
    """*(warning)* Every declared entity is referenced by at least one fixture.

    An unreferenced entity is dead weight in the world - nothing the agent sees
    depends on it - but it blocks nothing, so ruling R-13 keeps it a warning and
    the dataset still stores.
    """
    referenced: set[str] = set()
    for _, _, fixture in ctx.ds.fixture_sites():
        for raw in as_sequence(fixture.get("entity_refs")):
            ref = as_text(raw)
            if ref is not None:
                referenced.add(ref.split("@", 1)[0])
    return [
        ctx.error(
            "DS-007",
            pointer("entities", entity_id),
            f"entity {entity_id!r} is declared but no fixture references it.",
            entity=entity_id,
        )
        for entity_id in sorted(ctx.ds.entities)
        if entity_id not in referenced
    ]


def ds_012(ctx: DatasetContext) -> list[RuleError]:
    """Every label dimension and value exists in the blueprint's ``label_schema``."""
    findings: list[RuleError] = []
    for dimension in sorted(ctx.ds.labels):
        at = pointer("labels", dimension)
        if dimension not in ctx.blueprint.label_dimensions:
            findings.append(
                ctx.error(
                    "DS-012",
                    at,
                    f"label dimension {dimension!r} is not declared in the blueprint.",
                    dimension=dimension,
                )
            )
            continue
        value = ctx.ds.labels[dimension]
        permitted = ctx.blueprint.label_dimensions[dimension]
        if value not in permitted:
            findings.append(
                ctx.error(
                    "DS-012",
                    at,
                    f"label {dimension}={value!r} is outside the declared vocabulary "
                    f"{list(permitted)}.",
                    dimension=dimension,
                    value=value,
                )
            )
    return findings


def ds_013(ctx: DatasetContext) -> list[RuleError]:
    """No label dimension appears twice.

    Structurally unreachable once a document is parsed - a JSON object cannot
    hold a duplicate key and neither can a ``dict`` - so ruling R-20 keeps the
    rule and moves the *detection* to the tool boundary, where the raw text
    still exists. The boundary hands the duplicate pointers to
    ``validate_dataset``; this rule reports the ones under ``/labels``.
    """
    return [
        ctx.error(
            "DS-013",
            duplicate,
            f"label dimension at {duplicate} appears more than once in the submitted document.",
            duplicate_pointer=duplicate,
        )
        for duplicate in ctx.duplicate_keys
        if duplicate.startswith("/labels/")
    ]


def ds_014(ctx: DatasetContext) -> list[RuleError]:
    """``expected.final`` validates against the blueprint's ``outcome_schema``."""
    at = pointer("expected", "final")
    schema = resolve_entity_refs(ctx.blueprint.outcome_schema, ctx.blueprint.entity_schemas)
    return [
        ctx.error(
            "DS-014",
            finding.pointer_under(at),
            f"expected.final violates outcome_schema: {finding.message}",
        )
        for finding in instance_findings(ctx.ds.expected.get("final"), schema)
    ]


def ds_015(ctx: DatasetContext) -> list[RuleError]:
    """``expected.expected_path``, when present, is a valid traversal.

    Consecutive pairs are connected by an edge, it starts at ``entry_node``, and
    it ends at a terminal node.
    """
    raw = ctx.ds.expected.get("expected_path")
    if raw is None:
        return []
    at = pointer("expected", "expected_path")
    path = [as_text(step) for step in as_sequence(raw)]
    if not path:
        return [ctx.error("DS-015", at, "expected_path is present but empty.")]
    findings: list[RuleError] = []
    entry = ctx.blueprint.entry_node
    if path[0] != entry:
        findings.append(
            ctx.error(
                "DS-015",
                at + pointer(0),
                f"expected_path starts at {path[0]!r}, not at the entry node {entry!r}.",
                entry_node=entry,
            )
        )
    last = path[-1]
    if last not in ctx.blueprint.terminal_node_ids:
        findings.append(
            ctx.error(
                "DS-015",
                at + pointer(len(path) - 1),
                f"expected_path ends at {last!r}, which is not a kind: terminal node.",
                node_id=last,
            )
        )
    for index, (source, target) in enumerate(pairwise(path)):
        if source is None or target is None or not ctx.blueprint.graph.has_edge(source, target):
            findings.append(
                ctx.error(
                    "DS-015",
                    at + pointer(index + 1),
                    f"expected_path has no edge from {source!r} to {target!r}.",
                    from_node=source,
                    to_node=target,
                )
            )
    return findings


def ds_016(ctx: DatasetContext) -> list[RuleError]:
    """Every key in ``expected.node_expectations`` is an existing node.

    Only the keys. ``args_match`` is deliberately unvalidated (ruling R-19): the
    service never evaluates it, so there is nothing to check it against.
    """
    expectations = as_mapping(ctx.ds.expected.get("node_expectations"))
    return [
        ctx.error(
            "DS-016",
            pointer("expected", "node_expectations", node_id),
            f"node_expectations names {node_id!r}, which is not a blueprint node.",
            node_id=node_id,
        )
        for node_id in sorted(expectations)
        if node_id not in ctx.blueprint.node_by_id
    ]


def ds_017(ctx: DatasetContext) -> list[RuleError]:
    """``expected.comparison`` is one of ``exact``, ``schema``, ``subset``."""
    value = ctx.ds.expected.get("comparison")
    if isinstance(value, str) and value in COMPARISONS:
        return []
    return [
        ctx.error(
            "DS-017",
            pointer("expected", "comparison"),
            f"comparison must be one of {sorted(COMPARISONS)}, got {value!r}.",
            comparison=value,
        )
    ]


def ds_018(ctx: DatasetContext) -> list[RuleError]:
    """``pools`` has an entry for every ``pool: true`` node and no entry for any other."""
    findings: list[RuleError] = []
    for node_id in ctx.blueprint.node_ids:
        if node_id in ctx.blueprint.pool_node_ids and node_id not in ctx.ds.pools:
            findings.append(
                ctx.error(
                    "DS-018",
                    pointer("pools"),
                    f"node {node_id!r} declares pool: true but has no entry in pools.",
                    node_id=node_id,
                )
            )
    for node_id in sorted(ctx.ds.pools):
        if node_id not in ctx.blueprint.pool_node_ids:
            findings.append(
                ctx.error(
                    "DS-018",
                    pointer("pools", node_id),
                    f"pools has an entry for {node_id!r}, which does not declare pool: true.",
                    node_id=node_id,
                )
            )
    return findings


def ds_019(ctx: DatasetContext) -> list[RuleError]:
    """Every pool has at least one fixture, and each validates against the node's
    ``output_schema``.

    Ruling R-18 gives DS-019 the whole of ``pools``, so DS-004 and DS-005 never
    look here. Pool keys that are not ``pool: true`` nodes are DS-018's and are
    skipped. A faulted pool entry skips the schema check exactly as DS-004 does
    (ruling R-07's reasoning, extended - see DECISIONS.md).
    """
    findings: list[RuleError] = []
    for node_id in sorted(ctx.ds.pools):
        if node_id not in ctx.blueprint.pool_node_ids:
            continue
        entries = ctx.ds.pools[node_id]
        if not entries:
            findings.append(
                ctx.error(
                    "DS-019",
                    pointer("pools", node_id),
                    f"the pool for {node_id!r} is empty, so iteration 0 has no fixture.",
                    node_id=node_id,
                )
            )
            continue
        schema = resolve_entity_refs(
            ctx.blueprint.schema_of(node_id, "output_schema"), ctx.blueprint.entity_schemas
        )
        for index, entry in enumerate(entries):
            if _fault_of(entry) is not None:
                continue
            if "output" not in entry:
                continue  # a required field: the model owns presence (ruling R-04)
            at = pointer("pools", node_id, index, "output")
            findings.extend(
                ctx.error(
                    "DS-019",
                    finding.pointer_under(at),
                    f"pool fixture {index} for {node_id!r} violates output_schema: "
                    f"{finding.message}",
                    node_id=node_id,
                    iteration=index,
                )
                for finding in instance_findings(entry.get("output"), schema)
            )
    return findings


def ds_020(ctx: DatasetContext) -> list[RuleError]:
    """``seed`` is an integer.

    Implemented against the raw document (ruling R-23). The model spells this
    field ``StrictInt``, so a string seed raises at parse time - which is why
    the corpus case for this rule is marked as one that does not parse, and why
    the check has to happen *before* a model exists for the rule id to be
    reachable at all.
    """
    if as_int(ctx.doc.get("seed")) is not None:
        return []
    return [
        ctx.error(
            "DS-020",
            pointer("seed"),
            f"seed must be an integer, got {ctx.doc.get('seed')!r}.",
            seed=ctx.doc.get("seed"),
        )
    ]


def ds_021(ctx: DatasetContext) -> list[RuleError]:
    """``narrative`` is present and at least 30 characters."""
    narrative = as_text(ctx.doc.get("narrative"))
    if narrative is not None and len(narrative) >= MIN_NARRATIVE_LENGTH:
        return []
    return [
        ctx.error(
            "DS-021",
            pointer("narrative"),
            f"narrative must be at least {MIN_NARRATIVE_LENGTH} characters of in-world prose.",
            length=len(narrative) if narrative is not None else None,
        )
    ]


def ds_022(ctx: DatasetContext) -> list[RuleError]:
    """When ``fault`` is set, it conforms to the FaultSpec shape.

    ``FaultSpec = {kind (required), code, after_ms}`` with no additional
    properties (ruling R-07). The unknown-key half is implemented here rather
    than by the model: ``FaultSpec`` is ``extra="allow"`` on purpose, so that an
    unexpected key reaches this rule as a rule id instead of raising.
    """
    findings: list[RuleError] = []
    for node_id, prefix, fixture in ctx.ds.fixture_sites():
        fault = fixture.get("fault")
        if fault is None:
            continue
        at = prefix + pointer("fault")
        if not isinstance(fault, dict):
            findings.append(
                ctx.error(
                    "DS-022",
                    at,
                    f"fault on {node_id!r} must be an object, got {type(fault).__name__}.",
                    node_id=node_id,
                )
            )
            continue
        kind = as_text(fault.get("kind"))
        if kind is None or kind not in FAULT_KINDS:
            findings.append(
                ctx.error(
                    "DS-022",
                    at + pointer("kind"),
                    f"fault.kind must be one of {sorted(FAULT_KINDS)}, got {fault.get('kind')!r}.",
                    node_id=node_id,
                    kind=fault.get("kind"),
                )
            )
        code = fault.get("code")
        if code is not None and not isinstance(code, str):
            findings.append(
                ctx.error(
                    "DS-022",
                    at + pointer("code"),
                    f"fault.code must be a string or null, got {type(code).__name__}.",
                    node_id=node_id,
                )
            )
        after_ms = fault.get("after_ms")
        if after_ms is not None and as_int(after_ms) is None:
            findings.append(
                ctx.error(
                    "DS-022",
                    at + pointer("after_ms"),
                    f"fault.after_ms must be an integer or null, got {after_ms!r}.",
                    node_id=node_id,
                )
            )
        for key in sorted(set(fault) - FAULT_KEYS):
            findings.append(
                ctx.error(
                    "DS-022",
                    at + pointer(key),
                    f"fault carries an unknown key {key!r}; FaultSpec declares "
                    f"{sorted(FAULT_KEYS)}.",
                    node_id=node_id,
                    unknown_key=key,
                )
            )
    return findings


def ds_023(ctx: DatasetContext) -> list[RuleError]:
    """A loop node's pool has at most ``max_iterations`` entries.

    Skipped when ``max_iterations`` is not a usable integer: BP-008 owns that,
    and a blueprint that failed it never reaches a dataset.
    """
    findings: list[RuleError] = []
    for node_id in sorted(ctx.ds.pools):
        if node_id not in ctx.blueprint.loop_node_ids:
            continue
        cap = ctx.blueprint.max_iterations_of(node_id)
        if cap is None or cap <= 0:
            continue
        entries = ctx.ds.pools[node_id]
        if len(entries) > cap:
            findings.append(
                ctx.error(
                    "DS-023",
                    pointer("pools", node_id),
                    f"the pool for {node_id!r} has {len(entries)} fixtures but max_iterations "
                    f"is {cap}.",
                    node_id=node_id,
                    pool_length=len(entries),
                    max_iterations=cap,
                )
            )
    return findings


def ds_024(ctx: DatasetContext) -> list[RuleError]:
    """``labels`` carries a value for every declared dimension. Partial labelling is rejected.

    Ground rule 8: no dataset without provenance, and complete labels are part
    of it. A partially labelled dataset is one ``dataset_find`` cannot be
    trusted to return.
    """
    return [
        ctx.error(
            "DS-024",
            pointer("labels"),
            f"labels carries no value for the declared dimension {dimension!r}.",
            dimension=dimension,
        )
        for dimension in ctx.blueprint.label_dimensions
        if dimension not in ctx.ds.labels
    ]


def ds_025(ctx: DatasetContext) -> list[RuleError]:
    """``provenance.title`` is present, non-whitespace, and at most 120 characters."""
    title = as_text(ctx.ds.provenance.get("title"))
    if title is not None and title.strip() and len(title) <= TITLE_MAX_LENGTH:
        return []
    return [
        ctx.error(
            "DS-025",
            pointer("provenance", "title"),
            f"title must be non-whitespace and at most {TITLE_MAX_LENGTH} characters.",
            length=len(title) if title is not None else None,
        )
    ]


def ds_026(ctx: DatasetContext) -> list[RuleError]:
    """``provenance.intent`` is present and at least 30 characters."""
    intent = as_text(ctx.ds.provenance.get("intent"))
    if intent is not None and len(intent) >= MIN_INTENT_LENGTH:
        return []
    return [
        ctx.error(
            "DS-026",
            pointer("provenance", "intent"),
            f"intent must be at least {MIN_INTENT_LENGTH} characters saying why this dataset "
            "exists in the suite.",
            length=len(intent) if intent is not None else None,
        )
    ]


def ds_027(ctx: DatasetContext) -> list[RuleError]:
    """*(warning)* ``provenance.intent`` is byte-identical to ``narrative``.

    They answer different questions: ``narrative`` is what happens in the world,
    ``intent`` is why the dataset exists in the suite. Identical text means one
    of the two was not written.
    """
    intent = as_text(ctx.ds.provenance.get("intent"))
    narrative = as_text(ctx.doc.get("narrative"))
    if intent is None or narrative is None or intent != narrative:
        return []
    return [
        ctx.error(
            "DS-027",
            pointer("provenance", "intent"),
            "intent is identical to narrative; they answer different questions.",
        )
    ]


def ds_028(ctx: DatasetContext) -> list[RuleError]:
    """``provenance.author.name`` is present and non-whitespace."""
    name = as_text(ctx.ds.author.get("name"))
    if name is not None and name.strip():
        return []
    return [
        ctx.error(
            "DS-028",
            pointer("provenance", "author", "name"),
            "author.name must name a person; there is no default author.",
        )
    ]


def ds_029(ctx: DatasetContext) -> list[RuleError]:
    """``provenance.author.handle`` matches ``^[a-z0-9][a-z0-9._-]{0,62}$``."""
    handle = as_text(ctx.ds.author.get("handle"))
    if handle is not None and HANDLE_PATTERN.match(handle):
        return []
    return [
        ctx.error(
            "DS-029",
            pointer("provenance", "author", "handle"),
            f"author.handle must match {HANDLE_PATTERN.pattern}, got "
            f"{ctx.ds.author.get('handle')!r}.",
            handle=ctx.ds.author.get("handle"),
        )
    ]


def ds_030(ctx: DatasetContext) -> list[RuleError]:
    """``provenance.author.agent`` is one of the four declared agents.

    ``claude-code``, ``codex``, ``human`` or ``generator``. Attribution, never
    authentication: the value is self-declared and recorded verbatim.
    """
    agent = as_text(ctx.ds.author.get("agent"))
    if agent is not None and agent in AUTHOR_AGENTS:
        return []
    return [
        ctx.error(
            "DS-030",
            pointer("provenance", "author", "agent"),
            f"author.agent must be one of {sorted(AUTHOR_AGENTS)}, got "
            f"{ctx.ds.author.get('agent')!r}.",
            agent=ctx.ds.author.get("agent"),
        )
    ]


def ds_031(ctx: DatasetContext) -> list[RuleError]:
    """``provenance.supersedes``, when non-null, references a dataset that exists.

    Answered by the resolver (ruling R-11). Archived datasets count as
    existing - lineage is exactly what superseding records.
    """
    supersedes = ctx.ds.provenance.get("supersedes")
    if supersedes is None:
        return []
    dataset_id = as_text(supersedes)
    if dataset_id is not None and ctx.resolver.dataset_exists(dataset_id):
        return []
    return [
        ctx.error(
            "DS-031",
            pointer("provenance", "supersedes"),
            f"supersedes names {supersedes!r}, which is not an existing dataset.",
            supersedes=supersedes,
        )
    ]


def ds_032(ctx: DatasetContext) -> list[RuleError]:
    """*(warning)* ``provenance.intent`` is byte-identical to ``expected.rationale``.

    ``intent`` says why the dataset exists in the suite; ``rationale`` says why
    that expected outcome is right given this world. Contracts 2.2: "Both are
    required, neither substitutes for the other."
    """
    intent = as_text(ctx.ds.provenance.get("intent"))
    rationale = as_text(ctx.ds.expected.get("rationale"))
    if intent is None or rationale is None or intent != rationale:
        return []
    return [
        ctx.error(
            "DS-032",
            pointer("provenance", "intent"),
            "intent is identical to expected.rationale; they answer different questions.",
        )
    ]


def ds_033(ctx: DatasetContext) -> list[RuleError]:
    """``expected.rationale`` is present and at least 30 characters.

    Ruling R-21 adds this rule. Contracts 2.2 already required ``rationale``
    in prose - "Both are required, neither substitutes for the other" - but no
    rule enforced it, so ``rationale: ""`` validated clean. It mirrors DS-026.
    """
    rationale = as_text(ctx.ds.expected.get("rationale"))
    if rationale is not None and len(rationale) >= MIN_RATIONALE_LENGTH:
        return []
    return [
        ctx.error(
            "DS-033",
            pointer("expected", "rationale"),
            f"expected.rationale must be at least {MIN_RATIONALE_LENGTH} characters saying why "
            "this outcome is correct in this world.",
            length=len(rationale) if rationale is not None else None,
        )
    ]
